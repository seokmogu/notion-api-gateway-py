"""Main request processing logic and polling loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from notion_gateway.config import AppConfig, get_config
from notion_gateway.services.notifier import notify_failure, notify_requested, notify_requester
from notion_gateway.services.notion_api import verify_page_access
from notion_gateway.services.notion_browser import (
    connect_integration_to_page,
    provision_token_for_page,
    refresh_session,
)
from notion_gateway.services.notion_records import (
    MAX_RETRY_COUNT,
    get_existing_token_for_page,
    get_issued_requests,
    get_pending_requests,
    mark_request_completed,
    mark_request_connected,
    mark_request_failed,
    mark_request_issued,
    mark_request_processing,
)
from notion_gateway.services.page_id import (
    build_deterministic_integration_name,
    extract_canonical_page_id,
)
from notion_gateway.types import RequestRecord

logger = logging.getLogger(__name__)

_shutdown_requested = False


def _request_shutdown(signum: int, frame: object) -> None:
    global _shutdown_requested
    logger.info("Shutdown requested (signal %d)", signum)
    _shutdown_requested = True


async def process_one_request(record: RequestRecord) -> None:
    """Process a single token request through the full provisioning flow."""
    from notion_gateway.services.notion_records import STATUS_COMPLETED

    # Guard: skip already completed records (e.g. via manual process --request)
    if record.status == STATUS_COMPLETED:
        logger.info("Skipping already completed request %s", record.id)
        return

    # Guard: skip records that exhausted retries
    if record.retry_count >= MAX_RETRY_COUNT:
        logger.info("Skipping request %s: max retries (%d) reached", record.id, record.retry_count)
        return

    cfg = get_config()
    logger.info(
        "Processing request %s: org=%s, url=%s",
        record.id,
        record.organization,
        record.page_url,
    )

    await mark_request_processing(record.id)

    # 0. Notify request received (only for fresh requests, not Failed retries)
    if not record.token:
        try:
            await notify_requested(record.id)
        except Exception as e:
            logger.warning("Request notification failed (non-fatal): %s", e)

    # 1. Validate page URL — reject unsupported formats before any processing
    if record.page_url and ".notion.site" in record.page_url:
        await mark_request_failed(
            record.id,
            "notion.site URLs (external shares) are not supported. "
            "Please use an internal Notion page URL: "
            "https://www.notion.so/workspace/Page-Name-<id>",
            record.retry_count,
        )
        return

    # 2. Extract canonical page ID
    source = record.canonical_page_id or record.page_url
    if not source:
        await mark_request_failed(record.id, "No page URL or page ID provided", record.retry_count)
        return

    try:
        canonical_page_id = extract_canonical_page_id(source)
    except ValueError as e:
        await mark_request_failed(record.id, str(e), record.retry_count)
        return

    page_url = record.page_url or f"https://www.notion.so/{canonical_page_id.replace('-', '')}"

    # 2. Check for existing token
    existing = await get_existing_token_for_page(canonical_page_id)
    if existing and existing.token:
        try:
            access = await verify_page_access(canonical_page_id, existing.token)
        except Exception:
            # Transient network error — conservatively reuse the existing token
            logger.warning(
                "Network error verifying existing token for page %s; reusing token",
                canonical_page_id,
            )
            access = True
        if access:
            logger.info("Reusing existing token for page %s", canonical_page_id)
            await mark_request_issued(
                record.id,
                existing.token,
                existing.integration_name or "existing",
                canonical_page_id,
            )
            # Skip notification if:
            #   - same request reprocessed (status was wrongly reset to Failed), OR
            #   - current record already has a token (was previously issued and notified)
            # Notify only for genuinely new requests reusing an existing page token.
            if existing.id == record.id or record.token:
                await mark_request_completed(record.id)
            else:
                await _notify_and_complete(record.id)
            return
        logger.info("Existing token for page %s is invalid; reprovisioning", canonical_page_id)

    # 3. Provision new token via browser
    integration_name = build_deterministic_integration_name(
        cfg.notion_integration_name_prefix,
        canonical_page_id,
        record.organization,
    )

    result = await provision_token_for_page(integration_name)

    # 4. Persist token immediately (prevents orphaned tokens on subsequent network error)
    await mark_request_issued(
        record.id,
        result.token,
        result.integration_name,
        canonical_page_id,
    )

    # 5. Connect integration to page
    connected = False
    try:
        connected = await connect_integration_to_page(page_url, integration_name)
        if connected:
            await mark_request_connected(record.id)
            try:
                if await verify_page_access(canonical_page_id, result.token):
                    logger.info("Connection verified for page %s", canonical_page_id)
            except Exception:
                logger.warning("Network error verifying connection for page %s", canonical_page_id)
    except Exception as e:
        logger.warning("Failed to connect integration to page: %s", e)

    # 6. Complete only if connected; otherwise leave as Issued for retry
    if connected:
        await _notify_and_complete(record.id)
    else:
        logger.info("Connection failed for %s — staying as Issued for retry", record.id)


async def _notify_and_complete(request_id: str) -> None:
    """Mark as completed, then send notification. Idempotent — skips if already done."""
    from notion_gateway.services.notion_api import retrieve_page
    from notion_gateway.services.notion_records import PROP_STATUS, STATUS_COMPLETED

    # Guard: re-read current status to avoid duplicate notifications
    try:
        page = await retrieve_page(request_id)
        props = page.get("properties", {})
        status_prop = props.get(PROP_STATUS, {})
        status_type = status_prop.get("type", "")
        current_status = (status_prop.get(status_type) or {}).get("name", "")
        if current_status == STATUS_COMPLETED:
            logger.debug("Request %s already completed — skipping notification", request_id)
            return
    except Exception as e:
        logger.warning("Failed to check current status for %s: %s", request_id, e)
        return  # Don't proceed if we can't verify status

    try:
        await mark_request_completed(request_id)
    except Exception as e:
        logger.warning("Failed to mark as completed: %s", e)
        return  # Don't notify if status update failed — retry next cycle

    try:
        await notify_requester(request_id)
    except Exception as e:
        logger.warning("Notification failed (non-fatal): %s", e)


async def process_pending_requests(limit: int = 10) -> int:
    """Fetch and process pending requests. Returns count processed."""
    records = await get_pending_requests(limit)
    if not records:
        return 0

    logger.info("Found %d pending request(s)", len(records))
    processed = 0
    for record in records:
        if _shutdown_requested:
            break
        try:
            await process_one_request(record)
            processed += 1
        except Exception as e:
            logger.error("Error processing request %s: %s", record.id, e)
            try:
                new_retry_count = record.retry_count + 1
                await mark_request_failed(record.id, str(e), record.retry_count)
                if new_retry_count >= MAX_RETRY_COUNT:
                    await notify_failure(record.id, str(e))
            except Exception as inner:
                logger.error("Failed to mark request as failed: %s", inner)
    return processed


async def retry_issued_requests() -> int:
    """Retry connecting issued but possibly unconnected requests."""
    records = await get_issued_requests(limit=5)
    retried = 0
    for record in records:
        if _shutdown_requested:
            break
        # Already connected — just ensure notify/complete ran
        if record.connection_status == "Yes":
            await _notify_and_complete(record.id)
            retried += 1
            continue

        # Fail fast: missing URL/page ID or token — no point retrying
        page_url = record.page_url
        if not page_url and record.canonical_page_id:
            page_url = f"https://www.notion.so/{record.canonical_page_id.replace('-', '')}"

        if not page_url or not record.integration_name or not record.token:
            reason = (
                "Missing page URL"
                if not page_url
                else "Missing integration name"
                if not record.integration_name
                else "Missing token"
            )
            logger.warning("Marking %s as failed: %s", record.id, reason)
            await mark_request_failed(record.id, reason, record.retry_count)
            if record.retry_count + 1 >= MAX_RETRY_COUNT:
                await notify_failure(record.id, reason)
            continue

        # Verify token is still valid before attempting connection
        if record.canonical_page_id:
            try:
                access = await verify_page_access(record.canonical_page_id, record.token)
                if not access:
                    logger.warning(
                        "Token invalid for %s (%s) — marking as failed",
                        record.id,
                        record.organization,
                    )
                    await mark_request_failed(
                        record.id, "Token no longer has access to page", record.retry_count
                    )
                    if record.retry_count + 1 >= MAX_RETRY_COUNT:
                        await notify_failure(record.id, "Token no longer has access to page")
                    continue
            except Exception as e:
                logger.warning("Could not verify token for %s: %s — will retry", record.id, e)

        try:
            connected = await connect_integration_to_page(page_url, record.integration_name)
            if connected:
                await mark_request_connected(record.id)
                await _notify_and_complete(record.id)
                retried += 1
            else:
                logger.info("Connection not yet ready for %s; will retry next cycle", record.id)
        except Exception as e:
            logger.warning("Retry failed for %s: %s", record.id, e)
    return retried


async def _sleep_interruptible(seconds: float) -> None:
    """Sleep in 1s chunks so shutdown signals are handled promptly."""
    remaining = seconds
    while remaining > 0 and not _shutdown_requested:
        await asyncio.sleep(min(1.0, remaining))
        remaining -= 1.0


async def _run_poll_cycle(cfg: "AppConfig") -> None:
    """Execute one poll cycle: process pending, retry issued."""
    try:
        await process_pending_requests(cfg.request_poll_limit)
    except Exception:
        raise

    try:
        await retry_issued_requests()
    except Exception:
        raise


async def run_poll_loop() -> None:
    """Run the main polling loop with session refresh every hour."""
    global _shutdown_requested
    _shutdown_requested = False

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    cfg = get_config()
    last_refresh = time.monotonic()
    refresh_interval = 3600  # 1 hour
    consecutive_failures = 0

    logger.info(
        "Starting poll loop (interval=%.1fs, limit=%d, network_retries=%d, backoff=%ds)",
        cfg.poll_interval_seconds,
        cfg.request_poll_limit,
        cfg.network_max_retries,
        cfg.network_backoff_seconds,
    )

    while not _shutdown_requested:
        # Periodic session refresh
        now = time.monotonic()
        if now - last_refresh >= refresh_interval:
            logger.info("Refreshing browser session...")
            try:
                await refresh_session()
            except Exception as e:
                logger.error("Session refresh failed: %s", e)
            last_refresh = now

        # Run poll cycle with network retry tracking
        try:
            await _run_poll_cycle(cfg)
            if consecutive_failures > 0:
                logger.info(
                    "Poll cycle succeeded after %d consecutive failure(s), resuming normal interval",
                    consecutive_failures,
                )
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            if consecutive_failures >= cfg.network_max_retries:
                logger.error(
                    "Poll cycle failed %d/%d times consecutively: %s. "
                    "Backing off for %ds before retrying.",
                    consecutive_failures,
                    cfg.network_max_retries,
                    e,
                    cfg.network_backoff_seconds,
                )
                consecutive_failures = 0
                await _sleep_interruptible(cfg.network_backoff_seconds)
                continue
            else:
                logger.warning(
                    "Poll cycle failed (%d/%d): %s. Retrying next cycle.",
                    consecutive_failures,
                    cfg.network_max_retries,
                    e,
                )

        await _sleep_interruptible(cfg.poll_interval_seconds)

    logger.info("Poll loop stopped (shutdown requested)")
