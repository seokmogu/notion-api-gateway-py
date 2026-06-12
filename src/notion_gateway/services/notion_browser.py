"""Playwright browser automation for Notion integration management."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from notion_gateway.config import get_config
from notion_gateway.types import ProvisioningResult

logger = logging.getLogger(__name__)

NOTION_APP_ORIGIN = "https://app.notion.com"
NOTION_WWW_ORIGIN = "https://www.notion.so"
NOTION_HOSTS = {"app.notion.com", "www.notion.so"}
INTEGRATIONS_URL = f"{NOTION_APP_ORIGIN}/profile/integrations"
NEW_INTEGRATION_URL = f"{NOTION_APP_ORIGIN}/profile/integrations/form/new-integration"
LOGIN_URL = f"{NOTION_APP_ORIGIN}/login"
VIEWPORT = {"width": 1440, "height": 900}
SELECTOR_TIMEOUT = 15_000  # 15s


async def _first_visible(
    page: Page, locators: list[Locator], timeout: int = 3000
) -> Locator | None:
    """Try each locator, return the first one that becomes visible."""
    for loc in locators:
        try:
            await loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Browser backend abstraction
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _open_browser(
    storage_state_path: Path | None = None,
) -> AsyncIterator[tuple[BrowserContext, Page]]:
    """Yield a local Chromium (context, page) pair."""
    async with _open_local(storage_state_path) as pair:
        yield pair


@asynccontextmanager
async def _open_local(
    storage_state_path: Path | None = None,
) -> AsyncIterator[tuple[BrowserContext, Page]]:
    """Open a local ephemeral Chromium browser with optional storage state."""
    pw = await async_playwright().start()
    cfg = get_config()
    launch_args = ["--start-maximized", "--disable-blink-features=AutomationControlled"]
    if cfg.no_ssl_verify:
        launch_args.append("--ignore-certificate-errors")
    browser = await pw.chromium.launch(
        headless=cfg.notion_headless,
        args=launch_args,
    )
    ctx_kwargs: dict = {
        "viewport": VIEWPORT if cfg.notion_headless else None,
        "locale": "en-US",
    }
    if storage_state_path and storage_state_path.exists():
        ctx_kwargs["storage_state"] = str(storage_state_path)
    context = await browser.new_context(**ctx_kwargs)
    for origin in (NOTION_APP_ORIGIN, NOTION_WWW_ORIGIN):
        await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    try:
        page = await context.new_page()
        yield context, page
    finally:
        await context.close()
        await pw.stop()


def _is_login_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname or "") in NOTION_HOSTS and parsed.path.startswith("/login")


def _is_notion_url(url: str) -> bool:
    parsed = urlparse(url)
    return (parsed.hostname or "") in NOTION_HOSTS


async def _is_logged_in(page: Page) -> bool:
    """Check whether navigating to integrations stays out of the login flow."""
    try:
        await page.goto(INTEGRATIONS_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(1000)
        return _is_notion_url(page.url) and not _is_login_url(page.url)
    except Exception as e:
        logger.debug("Failed to verify Notion login state: %s", e)
        return False


async def _submit_login_code(page: Page, code: str | None, timeout: int = 10_000) -> bool:
    """Submit Notion's email verification code when the login flow asks for one."""
    code_input = await _first_visible(
        page,
        [
            page.locator('input[autocomplete="one-time-code"]'),
            page.locator('input[inputmode="numeric"]'),
            page.locator('input[name="code"]'),
            page.get_by_placeholder(re.compile(r"code|verification|인증|코드", re.I)),
        ],
        timeout=timeout,
    )
    if not code_input:
        return False

    if not code:
        raise RuntimeError(
            "Notion requested an email verification code. "
            "Set NOTION_LOGIN_CODE or run 'notion-gateway auth' manually."
        )

    login_code = code.strip()
    if not login_code:
        raise RuntimeError("NOTION_LOGIN_CODE is empty.")

    try:
        await code_input.fill(login_code)
    except Exception:
        await code_input.click()
        await page.keyboard.type(login_code)
    await page.keyboard.press("Enter")

    verify_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"continue|verify|확인|인증|계속", re.I)),
            page.get_by_text(re.compile(r"continue|verify|확인|인증|계속", re.I)),
        ],
        timeout=2000,
    )
    if verify_btn:
        await verify_btn.click()
    await page.wait_for_timeout(3000)
    return True


async def _handle_login(page: Page) -> None:
    """Handle automatic login if credentials are configured."""
    cfg = get_config()
    if not cfg.notion_email or not (cfg.notion_password or cfg.notion_login_code):
        raise RuntimeError(
            "Not logged in and no credentials configured. "
            "Run 'notion-gateway auth' to bootstrap a session manually."
        )

    logger.info("Attempting automatic login for %s", cfg.notion_email)

    # Navigate to the current app domain. www.notion.so/login redirects here.
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1000)
    if _is_notion_url(page.url) and not _is_login_url(page.url):
        logger.info("Existing Notion browser session is already logged in")
        return

    # Enter email — input fields are stable across UI changes
    email_input = await _first_visible(
        page,
        [
            page.locator('input[type="email"]'),
            page.locator('input[name="email"]'),
            page.get_by_placeholder(re.compile(r"email", re.I)),
        ],
        timeout=10_000,
    )
    if not email_input:
        raise RuntimeError("Cannot find email input on login page")
    await email_input.fill(cfg.notion_email)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(2000)

    # Notion may use password login or email-code login depending on account policy.
    password_input = await _first_visible(
        page,
        [
            page.locator('input[type="password"]'),
            page.get_by_placeholder(re.compile(r"password", re.I)),
        ],
        timeout=5000,
    )
    if password_input:
        if not cfg.notion_password:
            raise RuntimeError("Notion requested a password but NOTION_PASSWORD is not configured.")
        await password_input.fill(cfg.notion_password)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)
        await _submit_login_code(page, cfg.notion_login_code, timeout=5000)
    elif not await _submit_login_code(page, cfg.notion_login_code, timeout=5000):
        raise RuntimeError(
            "Password or verification-code input not found. Your account may use SSO. "
            "Run 'notion-gateway auth' to log in manually."
        )

    # Wait for login to complete
    await page.wait_for_timeout(3000)

    # Check for bot detection
    bot_msg = await _first_visible(
        page, [page.locator("text=/Please try again later/i")], timeout=3000
    )
    if bot_msg:
        raise RuntimeError(
            "Bot detection triggered. Wait a few minutes and try again, "
            "or run 'notion-gateway auth' for manual login."
        )

    # Verify we're logged in
    if not await _is_logged_in(page):
        raise RuntimeError("Login appears to have failed. Run 'notion-gateway auth' manually.")

    logger.info("Login successful")


async def _ensure_integration_exists(page: Page, integration_name: str) -> None:
    """Navigate to or create a Notion integration by name."""
    await page.goto(INTEGRATIONS_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(1500)

    # Check if integration already exists
    escaped_name = re.escape(integration_name)
    existing = page.locator(f"text=/{escaped_name}/i")
    try:
        await existing.first.wait_for(state="visible", timeout=5000)
        logger.info("Integration '%s' already exists, opening it", integration_name)
        await existing.first.click()
        await page.wait_for_timeout(2000)
        return
    except Exception:
        pass

    # Click "New integration" button
    new_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"new integration", re.I)),
            page.get_by_role("button", name=re.compile(r"새 API 통합", re.I)),
            page.get_by_text(re.compile(r"new integration", re.I)),
            page.get_by_text(re.compile(r"새 API 통합 만들기", re.I)),
        ],
        timeout=SELECTOR_TIMEOUT,
    )
    if not new_btn:
        raise RuntimeError("Cannot find 'New Integration' button")
    await new_btn.click()
    await page.wait_for_timeout(2000)

    # Safety check: ensure we're on the form page
    if "/form/new-integration" not in page.url:
        logger.warning("Expected form URL but got %s, navigating directly", page.url)
        await page.goto(NEW_INTEGRATION_URL, wait_until="domcontentloaded", timeout=30_000)

    # Fill integration name
    name_input = await _first_visible(
        page,
        [
            page.get_by_label(re.compile(r"name|이름|API 통합 이름", re.I)),
            page.locator('input[name="name"]'),
            page.locator("input").first,
        ],
        timeout=SELECTOR_TIMEOUT,
    )
    if not name_input:
        raise RuntimeError("Cannot find integration name input")
    await name_input.fill(integration_name)

    # Select workspace (required) — find dropdown via parent of "관련 워크스페이스" label
    cfg = get_config()
    ws_label_section = (
        page.locator("text=/관련 워크스페이스|associated workspace/i").locator("..").locator("..")
    )
    ws_dropdown = await _first_visible(
        page,
        [ws_label_section.locator('[role="button"][aria-haspopup="dialog"]')],
        timeout=5000,
    )
    if ws_dropdown:
        await ws_dropdown.click()
        await page.wait_for_timeout(1000)

        dialog = page.locator("[role=dialog]").last
        ws_name = cfg.notion_workspace_name or ""
        if ws_name:
            ws_option = await _first_visible(
                page,
                [
                    dialog.get_by_role("menuitem", name=ws_name),
                    dialog.get_by_role("menuitem").filter(has_text=ws_name),
                ],
                timeout=5000,
            )
            if ws_option:
                await ws_option.click()
                logger.info("Selected workspace: %s", ws_name)
        else:
            # No name configured — select first option
            first_option = await _first_visible(
                page,
                [dialog.get_by_role("menuitem").first],
                timeout=5000,
            )
            if first_option:
                await first_option.click()
                logger.info("Selected first available workspace")
        await page.wait_for_timeout(500)
    else:
        logger.warning("Workspace dropdown not found")

    # Dismiss any lingering overlay left by the workspace dropdown
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(500)

    # Submit form — Notion uses div[role=button], not <button>
    submit_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"^생성하기$|^submit$|^create$", re.I)),
        ],
        timeout=SELECTOR_TIMEOUT,
    )
    if not submit_btn:
        raise RuntimeError("Cannot find submit button (생성하기)")
    await submit_btn.click(force=True)
    logger.info("Clicked submit button")
    await page.wait_for_timeout(5000)

    # Handle post-creation modal — click "Configure integration settings" button
    configure_btn = await _first_visible(
        page,
        [
            page.get_by_role(
                "button", name=re.compile(r"configure integration|API 통합 설정 구성", re.I)
            ),
            page.get_by_text(re.compile(r"API 통합 설정 구성", re.I)),
            page.get_by_text(re.compile(r"configure integration", re.I)),
        ],
        timeout=5000,
    )
    if configure_btn:
        await configure_btn.click()
        await page.wait_for_timeout(3000)
        logger.info("Navigated to integration settings via configure button")
    else:
        # Fallback: navigate to integration list and click the integration
        logger.warning("Configure button not found, navigating to integration list")
        await page.goto(INTEGRATIONS_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2000)
        escaped = re.escape(integration_name)
        link = await _first_visible(
            page,
            [
                page.get_by_role("link", name=re.compile(escaped, re.I)),
                page.get_by_text(integration_name),
            ],
            timeout=5000,
        )
        if link:
            await link.click()
            await page.wait_for_timeout(2000)
            logger.info("Navigated to integration via list")
        else:
            logger.error("Could not find integration '%s' in list", integration_name)

    logger.info("Created integration '%s'", integration_name)


async def _copy_integration_token(page: Page) -> str:
    """Reveal and copy the integration token from the current page."""
    # Click Show button
    show_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"show|표시하기", re.I)),
            page.get_by_text(re.compile(r"^show$|^표시하기$", re.I)),
        ],
        timeout=SELECTOR_TIMEOUT,
    )
    if show_btn:
        await show_btn.click()
        logger.info("Clicked Show button, waiting for token to appear")
        await page.wait_for_timeout(2000)
    else:
        logger.warning("Show button not found, trying to read token directly")
        await page.wait_for_timeout(2000)

    # Find token by iterating all inputs (matches TS version)
    # Try multiple times with delay (token may take time to render)
    for attempt in range(3):
        all_inputs = await page.locator("input").all()
        logger.debug("Attempt %d: found %d input elements", attempt + 1, len(all_inputs))
        for inp in all_inputs:
            try:
                value = await inp.input_value()
                if value:
                    logger.debug("Input value: %s...", value[:20])
                if value.startswith("ntn_") or value.startswith("secret_"):
                    logger.info("Token found via input value")
                    return value.strip()
            except Exception:
                continue
        if attempt < 2:
            logger.debug("Token not found in inputs, retrying in 2s...")
            await page.wait_for_timeout(2000)

    # Fallback: clipboard via More menu
    more_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"more|더보기", re.I)),
            page.locator('[aria-label="More"]'),
        ],
    )
    if more_btn:
        await more_btn.click()
        await page.wait_for_timeout(1000)
        copy_option = await _first_visible(
            page,
            [
                page.get_by_text(
                    re.compile(r"copy internal integration token|내부 통합 토큰 복사", re.I)
                ),
            ],
        )
        if copy_option:
            await copy_option.click()
            await page.wait_for_timeout(500)
            token = await page.evaluate("navigator.clipboard.readText()")
            if token and (token.startswith("ntn_") or token.startswith("secret_")):
                return token

    # Log current page state for debugging
    logger.error("Token not found. Current URL: %s", page.url)
    raise RuntimeError(
        "Could not retrieve integration token. "
        "The token input was not found after Show button click. "
        f"Current URL: {page.url}"
    )


async def _connect_integration_to_page(page: Page, page_url: str, integration_name: str) -> bool:
    """Connect an integration to a Notion page via UI automation."""
    logger.info("Connecting integration '%s' to page %s", integration_name, page_url)
    await page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)

    # Wait for Actions button to appear (TS pattern: waitForSelector with fallback)
    try:
        await page.wait_for_selector('[aria-label="작업"], [aria-label="Actions"]', timeout=15_000)
    except Exception:
        await page.wait_for_timeout(5000)

    for attempt in range(2):
        # Click Actions button
        actions_btn = await _first_visible(
            page,
            [
                page.locator('[aria-label="작업"]'),
                page.locator('[aria-label="Actions"]'),
                page.get_by_role("button", name=re.compile(r"^more$", re.I)),
                page.locator('[aria-label*="More"]'),
            ],
            timeout=SELECTOR_TIMEOUT,
        )
        if not actions_btn:
            if attempt == 0:
                await page.reload(wait_until="domcontentloaded")
                try:
                    await page.wait_for_selector(
                        '[aria-label="작업"], [aria-label="Actions"]', timeout=15_000
                    )
                except Exception:
                    await page.wait_for_timeout(5000)
                continue
            raise RuntimeError("Cannot find Actions button on page")
        await actions_btn.click()
        await page.wait_for_timeout(1000)

        # Click Connections — scope to overlay container like TS version
        overlay = page.locator(".notion-overlay-container div[tabindex]")
        conn_menu = await _first_visible(
            page,
            [
                overlay.filter(has_text=re.compile(r"^연결\d*$")),
                overlay.filter(has_text=re.compile(r"^Connections?\d*$", re.I)),
            ],
            timeout=5000,
        )
        # Scroll down within the overlay menu if Connections not yet visible
        if not conn_menu:
            try:
                menu_el = overlay.first
                for _ in range(3):
                    await menu_el.evaluate("el => el.scrollTop = el.scrollHeight")
                    await page.wait_for_timeout(500)
                    conn_menu = await _first_visible(
                        page,
                        [
                            overlay.filter(has_text=re.compile(r"^연결\d*$")),
                            overlay.filter(has_text=re.compile(r"^Connections?\d*$", re.I)),
                        ],
                        timeout=2000,
                    )
                    if conn_menu:
                        break
            except Exception:
                pass
        if not conn_menu:
            await page.keyboard.press("Escape")
            if attempt == 0:
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                continue
            raise RuntimeError("Cannot find Connections menu")
        await conn_menu.click()
        await page.wait_for_timeout(1000)

        # Click Add Connection — scope to overlay container
        add_conn = await _first_visible(
            page,
            [
                overlay.filter(has_text=re.compile(r"연결 추가하기")),
                overlay.filter(has_text=re.compile(r"Add connection", re.I)),
            ],
            timeout=5000,
        )
        if not add_conn:
            await page.keyboard.press("Escape")
            if attempt == 0:
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                continue
            raise RuntimeError("Cannot find 'Add connection' option")
        await add_conn.click()
        await page.wait_for_timeout(3000)

        # Find and click integration — scope to overlay container
        escaped_name = re.escape(integration_name)
        integration_option = overlay.filter(has_text=re.compile(escaped_name, re.I))
        try:
            await integration_option.first.wait_for(state="visible", timeout=10_000)
            await integration_option.first.click()
            await page.wait_for_timeout(1000)
        except Exception:
            await page.keyboard.press("Escape")
            if attempt == 0:
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                continue
            logger.warning("Integration '%s' not found in connection list", integration_name)
            return False

        # Confirm access dialog
        confirm_btn = await _first_visible(
            page,
            [
                page.get_by_role("button", name=re.compile(r"confirm|확인|허용", re.I)),
                page.locator('button:has-text("확인")'),
                page.locator('button:has-text("허용")'),
            ],
            timeout=5000,
        )
        if confirm_btn:
            await confirm_btn.click()
            await page.wait_for_timeout(1000)

        logger.info("Successfully connected integration to page")
        return True

    return False


async def provision_token_for_page(
    integration_name: str,
    target_space_id: str | None = None,
) -> ProvisioningResult:
    """Provision a Notion integration token.

    Uses internal API (no browser) for creation and token retrieval.
    Falls back to browser automation if internal API fails.

    Args:
        integration_name: Integration name
        target_space_id: Workspace to create the bot in. MUST match the target
            page's workspace, otherwise connection will fail with
            "Cannot add bot permission for a bot from a different workspace".
    """
    from notion_gateway.services.notion_internal_api import (
        NotionInternalApiError,
        create_integration,
        find_bot_by_name,
        get_available_spaces,
        get_bot_token,
    )

    try:
        # Check if integration already exists
        existing = await find_bot_by_name(integration_name)
        if existing:
            # Guard: if an existing integration is in the wrong workspace, it can't connect
            if target_space_id and existing.space_id and existing.space_id != target_space_id:
                logger.warning(
                    "Existing integration '%s' is in space %s but page is in %s, "
                    "creating a new one",
                    integration_name,
                    existing.space_id,
                    target_space_id,
                )
            else:
                token = await get_bot_token(existing.bot_id)
                logger.info("Reusing existing integration '%s' via API", integration_name)
                return ProvisioningResult(
                    token=token,
                    integration_name=integration_name,
                    bot_id=existing.bot_id,
                    space_id=existing.space_id,
                )

        # Choose workspace
        if target_space_id:
            # Verify user can create integrations in this space
            spaces = await get_available_spaces()
            if target_space_id not in spaces:
                raise NotionInternalApiError(
                    f"User cannot create integrations in target space {target_space_id}. "
                    f"Available spaces: {spaces}"
                )
            chosen_space = target_space_id
        else:
            spaces = await get_available_spaces()
            if not spaces:
                raise NotionInternalApiError("No spaces available for integration creation")
            chosen_space = spaces[0]

        bot = await create_integration(integration_name, chosen_space)
        token = await get_bot_token(bot.bot_id)
        logger.info(
            "Created integration '%s' via internal API (botId=%s, space=%s)",
            integration_name, bot.bot_id, chosen_space,
        )
        return ProvisioningResult(
            token=token,
            integration_name=integration_name,
            bot_id=bot.bot_id,
            space_id=bot.space_id or chosen_space,
        )

    except NotionInternalApiError as e:
        logger.warning("Internal API failed (%s), falling back to browser: %s", e.endpoint, e)
        return await _provision_token_via_browser(integration_name)


async def _provision_token_via_browser(
    integration_name: str,
) -> ProvisioningResult:
    """Fallback: provision token via browser automation."""
    cfg = get_config()
    storage_path = cfg.storage_state_path

    async with _open_browser(storage_path) as (context, page):
        if not await _is_logged_in(page):
            raise RuntimeError("Not logged in. Run 'notion-gateway auth' to bootstrap a session.")
        await _ensure_integration_exists(page, integration_name)
        token = await _copy_integration_token(page)
        await context.storage_state(path=str(storage_path))
        return ProvisioningResult(token=token, integration_name=integration_name)


async def connect_integration_to_page(
    page_url: str,
    integration_name: str,
    bot_id: str | None = None,
    space_id: str | None = None,
) -> bool:
    """Connect an integration to a page.

    Uses internal API (no browser) via saveTransactionsFanout.
    Falls back to browser automation if the internal API fails.

    Args:
        page_url: Notion page URL
        integration_name: Integration name (used for fallback lookup)
        bot_id: Optional bot ID from provisioning (skips name lookup)
        space_id: Optional space ID from provisioning
    """
    from notion_gateway.services.notion_internal_api import (
        NotionInternalApiError,
        connect_bot_to_page,
        find_bot_by_name,
        get_page_space_id,
    )
    from notion_gateway.services.page_id import extract_canonical_page_id

    try:
        page_id = extract_canonical_page_id(page_url)

        # Use provided bot info, or look up by name
        if not bot_id:
            bot = await find_bot_by_name(integration_name)
            if not bot:
                logger.warning(
                    "Integration '%s' not found via internal API, falling back to browser",
                    integration_name,
                )
                return await _connect_via_browser(page_url, integration_name)
            bot_id = bot.bot_id
            space_id = space_id or bot.space_id

        if not space_id:
            space_id = await get_page_space_id(page_id)
        if not space_id:
            logger.warning("Could not determine space_id, falling back to browser")
            return await _connect_via_browser(page_url, integration_name)

        await connect_bot_to_page(bot_id, page_id, space_id)
        return True

    except NotionInternalApiError as e:
        msg = str(e)
        # Permission errors are not retryable — don't fall back to browser
        permission_markers = ["Non-admin", "does not have edit access", "different workspace"]
        if (
            any(k in msg for k in permission_markers)
            or "permission" in msg.lower()
            or "unauthorized" in msg.lower()
        ):
            logger.error(
                "Cannot connect integration: user lacks admin rights on the page. "
                "The page owner must add the integration manually."
            )
            raise RuntimeError(
                "페이지 관리자 권한 없음: 해당 페이지는 현재 사용자가 관리자가 아니어서 "
                "통합을 자동 연결할 수 없습니다. 페이지 소유자가 수동으로 연결해야 합니다."
            ) from e
        logger.warning(
            "Internal API connect failed (%s): %s, falling back to browser",
            e.endpoint,
            e,
        )
        return await _connect_via_browser(page_url, integration_name)
    except ValueError as e:
        logger.error("Invalid page URL: %s", e)
        return False


async def _connect_via_browser(
    page_url: str,
    integration_name: str,
) -> bool:
    """Fallback: connect integration via browser automation."""
    cfg = get_config()
    storage_path = cfg.storage_state_path

    async with _open_browser(storage_path) as (_context, page):
        return await _connect_integration_to_page(page, page_url, integration_name)


async def bootstrap_admin_session() -> None:
    """Launch a browser and log in to Notion to bootstrap a session."""
    cfg = get_config()
    await _bootstrap_local(cfg)


async def repair_saved_session_from_profile() -> bool:
    """Refresh storage-state from the persistent profile without manual login."""
    cfg = get_config()
    pw = await async_playwright().start()
    launch_args: list[str] = ["--disable-blink-features=AutomationControlled"]
    if cfg.no_ssl_verify:
        launch_args.append("--ignore-certificate-errors")
    context = await pw.chromium.launch_persistent_context(
        cfg.notion_browser_profile_dir,
        headless=True,
        viewport=VIEWPORT,
        locale="en-US",
        args=launch_args,
    )
    try:
        page = await context.new_page()
        if await _is_logged_in(page):
            await context.storage_state(path=str(cfg.storage_state_path))
            logger.info("Repaired saved browser session from persistent profile")
            return True
        logger.warning("Persistent profile is not logged in; cannot repair saved session")
        return False
    finally:
        await context.close()
        await pw.stop()


async def _bootstrap_local(cfg: object) -> None:
    """Bootstrap session via local persistent browser (interactive login)."""
    from notion_gateway.config import AppConfig

    assert isinstance(cfg, AppConfig)
    pw = await async_playwright().start()
    launch_args: list[str] = []
    if cfg.no_ssl_verify:
        launch_args.append("--ignore-certificate-errors")
    context = await pw.chromium.launch_persistent_context(
        cfg.notion_browser_profile_dir,
        headless=False,
        viewport=VIEWPORT,
        locale="en-US",
        args=launch_args,
    )
    try:
        page = await context.new_page()
        await page.goto(INTEGRATIONS_URL, wait_until="domcontentloaded", timeout=60_000)

        if await _is_logged_in(page):
            await context.storage_state(path=str(cfg.storage_state_path))
            logger.info("Existing browser session saved to %s", cfg.storage_state_path)
            return

        # Try automatic login
        if cfg.notion_email and (cfg.notion_password or cfg.notion_login_code):
            try:
                await _handle_login(page)
                await context.storage_state(path=str(cfg.storage_state_path))
                logger.info("Auto-login successful, session saved")
                return
            except Exception as e:
                logger.warning("Auto-login failed: %s. Please log in manually.", e)

        # Manual login fallback
        print("\n=== Manual Login Required ===")
        print("Log in to Notion in the browser window.")
        print("Press Enter here after you are logged in...")

        # Wait for login (poll every 3s, up to 5 minutes)
        for i in range(100):
            try:
                url = page.url
                if _is_notion_url(url) and not _is_login_url(url):
                    if await _is_logged_in(page):
                        await context.storage_state(path=str(cfg.storage_state_path))
                        print(f"\nSession saved to {cfg.storage_state_path}")
                        logger.info("Session saved to %s", cfg.storage_state_path)
                        return
            except Exception:
                pass
            if i % 10 == 0 and i > 0:
                print(f"  Waiting for login... ({i * 3}s)")
            await page.wait_for_timeout(3000)

        raise RuntimeError("Login timeout (5min). Please try again.")
    finally:
        await context.close()
        await pw.stop()


async def refresh_session() -> bool:
    """Refresh the saved browser session."""
    cfg = get_config()
    storage_path = cfg.storage_state_path
    if not storage_path.exists():
        logger.warning("No saved session found at %s", storage_path)
        return False

    async with _open_browser(storage_path) as (context, page):
        if await _is_logged_in(page):
            await context.storage_state(path=str(storage_path))
            logger.info("Session refreshed successfully")
            return True
        logger.warning("Session expired, manual re-authentication required")
        return False
