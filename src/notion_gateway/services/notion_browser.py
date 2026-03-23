"""Playwright browser automation for Notion integration management."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from playwright.async_api import BrowserContext, Locator, Page, async_playwright

from notion_gateway.config import get_config
from notion_gateway.types import ProvisioningResult

logger = logging.getLogger(__name__)

INTEGRATIONS_URL = "https://www.notion.so/my-integrations"
NEW_INTEGRATION_URL = "https://www.notion.so/my-integrations/form/new-integration"
VIEWPORT = {"width": 1440, "height": 900}
SELECTOR_TIMEOUT = 15_000  # 15s


async def _first_visible(page: Page, locators: list[Locator], timeout: int = 3000) -> Locator | None:
    """Try each locator, return the first one that becomes visible."""
    for loc in locators:
        try:
            await loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:
            continue
    return None


async def _launch_persistent_context(headless: bool = True) -> BrowserContext:
    """Launch a persistent browser context for interactive login."""
    cfg = get_config()
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        cfg.notion_browser_profile_dir,
        headless=headless,
        viewport=VIEWPORT,
        locale="ko-KR",
    )
    return context


async def _launch_ephemeral(storage_state_path: Path) -> tuple[BrowserContext, object]:
    """Launch an ephemeral browser context using saved storage state."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=get_config().notion_headless,
        args=["--start-maximized"],
    )
    context = await browser.new_context(
        storage_state=str(storage_state_path),
        viewport=VIEWPORT,
        locale="ko-KR",
    )
    return context, pw


async def _is_logged_in(page: Page) -> bool:
    """Check if the page shows an authenticated Notion integrations view."""
    try:
        await page.goto(INTEGRATIONS_URL, wait_until="networkidle", timeout=30_000)
        # Look for signs of being logged in: integration list or new integration button
        logged_in = await _first_visible(
            page,
            [
                page.get_by_role("button", name=re.compile(r"new integration|새 API 통합", re.I)),
                page.locator('[data-testid="integration-list"]'),
                page.locator("text=/My integrations|내 통합/i"),
            ],
            timeout=10_000,
        )
        return logged_in is not None
    except Exception:
        return False


async def _handle_login(page: Page) -> None:
    """Handle automatic login if credentials are configured."""
    cfg = get_config()
    if not cfg.notion_email or not cfg.notion_password:
        raise RuntimeError(
            "Not logged in and no credentials configured. "
            "Run 'notion-gateway auth' to bootstrap a session manually."
        )

    logger.info("Attempting automatic login for %s", cfg.notion_email)

    # Navigate to login
    await page.goto("https://www.notion.so/login", wait_until="networkidle", timeout=30_000)

    # Enter email
    email_input = await _first_visible(
        page,
        [
            page.get_by_placeholder(re.compile(r"email|이메일", re.I)),
            page.locator('input[type="email"]'),
            page.locator('input[name="email"]'),
        ],
        timeout=10_000,
    )
    if not email_input:
        raise RuntimeError("Cannot find email input on login page")
    await email_input.fill(cfg.notion_email)

    # Click continue
    continue_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"continue|계속", re.I)),
            page.locator('button[type="submit"]'),
        ],
    )
    if continue_btn:
        await continue_btn.click()
        await page.wait_for_timeout(2000)

    # Enter password
    password_input = await _first_visible(
        page,
        [
            page.get_by_placeholder(re.compile(r"password|비밀번호", re.I)),
            page.locator('input[type="password"]'),
        ],
        timeout=10_000,
    )
    if not password_input:
        # Could be SSO or email-code login
        raise RuntimeError(
            "Password input not found. Your account may use SSO or email-code login. "
            "Run 'notion-gateway auth' to log in manually."
        )
    await password_input.fill(cfg.notion_password)

    # Submit login
    submit_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"log in|로그인|continue|계속", re.I)),
            page.locator('button[type="submit"]'),
        ],
    )
    if submit_btn:
        await submit_btn.click()

    # Handle 2FA if needed
    if cfg.notion_login_code:
        code_input = await _first_visible(
            page,
            [
                page.get_by_placeholder(re.compile(r"code|인증|인증코드", re.I)),
                page.locator('input[name="code"]'),
            ],
            timeout=10_000,
        )
        if code_input:
            await code_input.fill(cfg.notion_login_code)
            verify_btn = await _first_visible(
                page,
                [page.get_by_role("button", name=re.compile(r"verify|확인|인증", re.I))],
            )
            if verify_btn:
                await verify_btn.click()

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


async def _login_if_needed(page: Page, context: BrowserContext, storage_path: Path) -> None:
    """Ensure the browser is logged into Notion."""
    if await _is_logged_in(page):
        logger.debug("Already logged in")
        return
    await _handle_login(page)
    await context.storage_state(path=str(storage_path))
    logger.info("Saved session to %s", storage_path)


async def _ensure_integration_exists(page: Page, integration_name: str) -> None:
    """Navigate to or create a Notion integration by name."""
    await page.goto(INTEGRATIONS_URL, wait_until="networkidle", timeout=30_000)

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
        await page.goto(NEW_INTEGRATION_URL, wait_until="networkidle", timeout=30_000)

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

    # Select workspace if configured
    cfg = get_config()
    if cfg.notion_workspace_name:
        ws_dropdown = await _first_visible(
            page,
            [
                page.locator(f"text=/{re.escape(cfg.notion_workspace_name)}/i"),
                page.get_by_label(re.compile(r"associated workspace|관련 워크스페이스", re.I)),
            ],
        )
        if ws_dropdown:
            await ws_dropdown.click()
            ws_option = page.locator(f"text=/{re.escape(cfg.notion_workspace_name)}/i")
            try:
                await ws_option.first.click(timeout=5000)
            except Exception:
                logger.warning("Could not select workspace '%s'", cfg.notion_workspace_name)

    # Submit form
    submit_btn = await _first_visible(
        page,
        [
            page.get_by_role("button", name=re.compile(r"submit|create|생성하기", re.I)),
            page.locator('button[type="submit"]'),
        ],
        timeout=SELECTOR_TIMEOUT,
    )
    if not submit_btn:
        raise RuntimeError("Cannot find submit button")
    await submit_btn.click()
    await page.wait_for_timeout(3000)

    # Handle post-creation modal
    configure_modal = await _first_visible(
        page,
        [page.get_by_text(re.compile(r"API 통합 설정 구성|configure", re.I))],
        timeout=5000,
    )
    if configure_modal:
        await configure_modal.click()
        await page.wait_for_timeout(2000)

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
        await page.wait_for_timeout(1000)

    # Read token from input
    token_input = page.locator('input[value^="ntn_"], input[value^="secret_"]')
    try:
        await token_input.first.wait_for(state="visible", timeout=5000)
        token = await token_input.first.input_value()
        if token and (token.startswith("ntn_") or token.startswith("secret_")):
            return token
    except Exception:
        pass

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

    raise RuntimeError("Could not retrieve integration token")


async def _connect_integration_to_page(
    page: Page, page_url: str, integration_name: str
) -> bool:
    """Connect an integration to a Notion page via UI automation."""
    logger.info("Connecting integration '%s' to page %s", integration_name, page_url)
    await page.goto(page_url, wait_until="networkidle", timeout=30_000)
    await page.wait_for_timeout(2000)

    for attempt in range(2):
        # Click Actions button
        actions_btn = await _first_visible(
            page,
            [
                page.locator('[aria-label="작업"]'),
                page.locator('[aria-label="Actions"]'),
                page.get_by_role("button", name=re.compile(r"actions|작업", re.I)),
            ],
            timeout=SELECTOR_TIMEOUT,
        )
        if not actions_btn:
            if attempt == 0:
                await page.reload(wait_until="networkidle")
                await page.wait_for_timeout(2000)
                continue
            raise RuntimeError("Cannot find Actions button on page")
        await actions_btn.click()
        await page.wait_for_timeout(1000)

        # Click Connections
        conn_menu = await _first_visible(
            page,
            [
                page.locator("div").filter(has_text=re.compile(r"^연결\d*$")),
                page.locator("div").filter(has_text=re.compile(r"^Connections?\d*$", re.I)),
            ],
            timeout=5000,
        )
        if not conn_menu:
            await page.keyboard.press("Escape")
            if attempt == 0:
                await page.reload(wait_until="networkidle")
                await page.wait_for_timeout(2000)
                continue
            raise RuntimeError("Cannot find Connections menu")
        await conn_menu.click()
        await page.wait_for_timeout(1000)

        # Click Add Connection
        add_conn = await _first_visible(
            page,
            [
                page.locator("div").filter(has_text=re.compile(r"연결 추가하기")),
                page.locator("div").filter(has_text=re.compile(r"Add connection", re.I)),
            ],
            timeout=5000,
        )
        if not add_conn:
            await page.keyboard.press("Escape")
            if attempt == 0:
                await page.reload(wait_until="networkidle")
                await page.wait_for_timeout(2000)
                continue
            raise RuntimeError("Cannot find 'Add connection' option")
        await add_conn.click()
        await page.wait_for_timeout(1000)

        # Find and click integration
        escaped_name = re.escape(integration_name)
        integration_option = page.locator(f"text=/{escaped_name}/i")
        try:
            await integration_option.first.wait_for(state="visible", timeout=10_000)
            await integration_option.first.click()
            await page.wait_for_timeout(1000)
        except Exception:
            await page.keyboard.press("Escape")
            if attempt == 0:
                await page.reload(wait_until="networkidle")
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
) -> ProvisioningResult:
    """Provision a Notion integration token using browser automation.

    Creates or reuses an integration, then retrieves its token.
    """
    cfg = get_config()
    storage_path = cfg.storage_state_path

    context, pw = await _launch_ephemeral(storage_path)
    try:
        page = await context.new_page()
        await _login_if_needed(page, context, storage_path)
        await _ensure_integration_exists(page, integration_name)
        token = await _copy_integration_token(page)
        await context.storage_state(path=str(storage_path))
        return ProvisioningResult(token=token, integration_name=integration_name)
    finally:
        await context.close()
        await pw.stop()  # type: ignore[union-attr]


async def connect_integration_to_page(
    page_url: str,
    integration_name: str,
) -> bool:
    """Connect an integration to a page using a separate browser instance."""
    cfg = get_config()
    storage_path = cfg.storage_state_path

    context, pw = await _launch_ephemeral(storage_path)
    try:
        page = await context.new_page()
        return await _connect_integration_to_page(page, page_url, integration_name)
    finally:
        await context.close()
        await pw.stop()  # type: ignore[union-attr]


async def bootstrap_admin_session() -> None:
    """Launch an interactive browser for manual login."""
    cfg = get_config()
    context = await _launch_persistent_context(headless=False)
    try:
        page = await context.new_page()
        await page.goto(INTEGRATIONS_URL, wait_until="networkidle", timeout=60_000)

        # Try automatic login
        if cfg.notion_email and cfg.notion_password:
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

        # Wait for user input (blocking)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)

        # Verify login
        for _ in range(60):
            if await _is_logged_in(page):
                await context.storage_state(path=str(cfg.storage_state_path))
                logger.info("Session saved to %s", cfg.storage_state_path)
                return
            await page.wait_for_timeout(1000)

        raise RuntimeError("Login timeout (60s). Please try again.")
    finally:
        await context.close()


async def refresh_session() -> bool:
    """Refresh the saved browser session."""
    cfg = get_config()
    storage_path = cfg.storage_state_path
    if not storage_path.exists():
        logger.warning("No saved session found at %s", storage_path)
        return False

    context, pw = await _launch_ephemeral(storage_path)
    try:
        page = await context.new_page()
        if await _is_logged_in(page):
            await context.storage_state(path=str(storage_path))
            logger.info("Session refreshed successfully")
            return True
        logger.warning("Session expired, manual re-authentication required")
        return False
    finally:
        await context.close()
        await pw.stop()  # type: ignore[union-attr]
