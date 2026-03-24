"""Main request processing logic and polling loop."""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from notion_gateway.config import get_config
from notion_gateway.services.notion_api import verify_page_access
from notion_gateway.services.notion_browser import (
    connect_integration_to_page,
    provision_token_for_page,
    refresh_session,
)
from notion_gateway.services.notion_records import (
    cleanup_max_retries,
    get_existing_token_for_page,
    get_issued_requests,
    get_pending_requests,
    mark_request_completed,
    mark_request_connected,
    mark_request_failed,
    mark_request_issued,
    mark_request_processing,
)
from notion_gateway.services.notifier import notify_failure, notify_requested, notify_requester
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
    cfg = get_config()
    logger.info(
        "Processing request %s: org=%s, url=%s",
        record.id,
        record.organization,
        record.page_url,
    )

    await mark_request_processing(record.id)

    # 0. Notify request received
    try:
        await notify_requested(record.id)
    except Exception as e:
        logger.warning("Request notification failed (non-fatal): %s", e)

    # 1. Extract canonical page ID
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
        if await verify_page_access(canonical_page_id, existing.token):
            logger.info("Reusing existing token for page %s", canonical_page_id)
            await mark_request_issued(
                record.id,
                existing.token,
                existing.integration_name or "existing",
                canonical_page_id,
            )
            await _notify_and_complete(record.id)
            return

    # 3. Provision new token via browser
    integration_name = build_deterministic_integration_name(
        cfg.notion_integration_name_prefix,
        canonical_page_id,
        record.organization,
    )

    result = await provision_token_for_page(integration_name)

    # 4. Verify token access
    if not await verify_page_access(canonical_page_id, result.token):
        logger.warning("Token provisioned but cannot access page yet, marking as issued anyway")

    await mark_request_issued(
        record.id,
        result.token,
        result.integration_name,
        canonical_page_id,
    )

    # 5. Notify (best-effort)
    await _notify_and_complete(record.id)

    # 6. Connect integration to page (best-effort, background)
    try:
        connected = await connect_integration_to_page(page_url, integration_name)
        if connected:
            await mark_request_connected(record.id)
            # Re-verify after connection
            if await verify_page_access(canonical_page_id, result.token):
                logger.info("Connection verified for page %s", canonical_page_id)
    except Exception as e:
        logger.warning("Failed to connect integration to page (best-effort): %s", e)


async def _notify_and_complete(request_id: str) -> None:
    """Send notifications and mark as completed (best-effort)."""
    try:
        await notify_requester(request_id)
    except Exception as e:
        logger.warning("Notification failed (non-fatal): %s", e)

    try:
        await mark_request_completed(request_id)
    except Exception as e:
        logger.warning("Failed to mark as completed: %s", e)


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
                await mark_request_failed(record.id, str(e), record.retry_count)
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
        if record.connection_status == "Yes":
            continue
        try:
            page_url = record.page_url
            if not page_url and record.canonical_page_id:
                page_url = f"https://www.notion.so/{record.canonical_page_id.replace('-', '')}"
            if page_url and record.integration_name:
                connected = await connect_integration_to_page(page_url, record.integration_name)
                if connected:
                    await mark_request_connected(record.id)

            # Send notifications if not yet completed
            await _notify_and_complete(record.id)
            retried += 1
        except Exception as e:
            logger.warning("Retry failed for %s: %s", record.id, e)
    return retried


async def run_poll_loop() -> None:
    """Run the main polling loop with session refresh every hour."""
    global _shutdown_requested
    _shutdown_requested = False

    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    cfg = get_config()
    last_refresh = time.monotonic()
    refresh_interval = 3600  # 1 hour

    logger.info(
        "Starting poll loop (interval=%.1fs, limit=%d)",
        cfg.poll_interval_seconds,
        cfg.request_poll_limit,
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

        # Cleanup Max Retries → Failed
        try:
            await cleanup_max_retries()
        except Exception as e:
            logger.error("Error in max retries cleanup: %s", e)

        # Process pending requests
        try:
            await process_pending_requests(cfg.request_poll_limit)
        except Exception as e:
            logger.error("Error in pending requests processing: %s", e)

        # Retry issued requests
        try:
            await retry_issued_requests()
        except Exception as e:
            logger.error("Error in retry processing: %s", e)

        # Sleep (1s chunks for fast shutdown)
        remaining = cfg.poll_interval_seconds
        while remaining > 0 and not _shutdown_requested:
            await asyncio.sleep(min(1.0, remaining))
            remaining -= 1.0

    logger.info("Poll loop stopped (shutdown requested)")
