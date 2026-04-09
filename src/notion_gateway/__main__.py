"""CLI entry point for Notion API Gateway."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logger = logging.getLogger("notion_gateway")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def _preflight_check() -> bool:
    """Validate configuration before running."""
    from notion_gateway.config import get_config
    from notion_gateway.services.notion_api import notion_fetch

    cfg = get_config()
    logger.info("Preflight check...")

    # Verify Notion token
    try:
        data, _ = await notion_fetch("users/me")
        bot_name = data.get("name", "unknown")
        logger.info("Notion token valid (bot: %s)", bot_name)
    except Exception as e:
        logger.error("Notion token invalid: %s", e)
        return False

    # Verify database access
    try:
        await notion_fetch(f"databases/{cfg.notion_requests_database_id}")
        logger.info("Database access verified")
    except Exception as e:
        logger.error("Cannot access database %s: %s", cfg.notion_requests_database_id, e)
        return False

    return True


async def cmd_auth() -> None:
    """Bootstrap admin browser session."""
    from notion_gateway.services.notion_browser import bootstrap_admin_session

    await bootstrap_admin_session()
    logger.info("Admin session bootstrapped successfully")


async def cmd_refresh() -> None:
    """Refresh saved browser session."""
    from notion_gateway.services.notion_browser import refresh_session

    ok = await refresh_session()
    if ok:
        logger.info("Session refreshed")
    else:
        logger.error("Session refresh failed. Run 'notion-gateway auth' to re-authenticate.")
        sys.exit(1)


async def cmd_poll() -> None:
    """Run continuous polling loop."""
    if not await _preflight_check():
        sys.exit(1)

    from notion_gateway.services.request_processor import run_poll_loop

    await run_poll_loop()


async def cmd_process(request_id: str | None = None) -> None:
    """Process pending requests or a specific request."""
    if not await _preflight_check():
        sys.exit(1)

    if request_id:
        from notion_gateway.services.notion_api import retrieve_page
        from notion_gateway.services.notion_records import (
            parse_request_record,
        )
        from notion_gateway.services.request_processor import process_one_request

        page = await retrieve_page(request_id)
        record = parse_request_record(page)
        await process_one_request(record)
    else:
        from notion_gateway.services.request_processor import process_pending_requests

        count = await process_pending_requests(limit=1)
        logger.info("Processed %d request(s)", count)


async def cmd_check_connections() -> None:
    """Check completed records and update connection status via API verification."""
    if not await _preflight_check():
        sys.exit(1)

    from notion_gateway.services.notion_api import verify_page_access
    from notion_gateway.services.notion_records import (
        get_completed_without_connection,
        mark_request_connected,
    )

    records = await get_completed_without_connection(limit=50)
    if not records:
        logger.info("All completed records already have connection status set.")
        return

    logger.info("Found %d completed record(s) without connection status.", len(records))
    updated = 0
    for record in records:
        if not record.token or not record.canonical_page_id:
            logger.warning("Skipping %s: missing token or page ID", record.id)
            continue
        try:
            accessible = await verify_page_access(record.canonical_page_id, record.token)
        except Exception as e:
            logger.warning("Error verifying %s (%s): %s", record.id, record.organization, e)
            continue
        if accessible:
            await mark_request_connected(record.id)
            logger.info("Updated connection status for %s (%s)", record.id, record.organization)
            updated += 1
        else:
            logger.info("Token invalid for %s (%s) — skipping", record.id, record.organization)

    logger.info("Updated %d/%d record(s).", updated, len(records))


async def cmd_doctor() -> None:
    """Run diagnostic checks."""
    from notion_gateway.doctor import run_doctor

    await run_doctor()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="notion-gateway",
        description="Notion API token provisioning automation",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    subparsers.add_parser("auth", help="Bootstrap admin browser session")
    subparsers.add_parser("refresh", help="Refresh saved browser session")
    subparsers.add_parser("poll", help="Run continuous polling loop")

    process_parser = subparsers.add_parser("process", help="Process pending requests")
    process_parser.add_argument("--request", type=str, help="Specific request ID to process")

    subparsers.add_parser("doctor", help="Run diagnostic checks")
    subparsers.add_parser(
        "check-connections", help="Verify and update connection status for completed records"
    )

    args = parser.parse_args()
    _setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "auth": cmd_auth,
        "refresh": cmd_refresh,
        "poll": cmd_poll,
        "process": lambda: cmd_process(getattr(args, "request", None)),
        "doctor": cmd_doctor,
        "check-connections": cmd_check_connections,
    }

    coro = commands[args.command]()
    asyncio.run(coro)


if __name__ == "__main__":
    main()
