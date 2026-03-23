"""Diagnostic tool for environment verification."""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"  \033[32m[OK]\033[0m   {msg}")


def _warn(msg: str) -> None:
    print(f"  \033[33m[WARN]\033[0m {msg}")


def _fail(msg: str) -> None:
    print(f"  \033[31m[FAIL]\033[0m {msg}")


async def run_doctor() -> None:
    """Run all diagnostic checks."""
    print("\n=== Notion API Gateway - Diagnostics ===\n")
    all_ok = True

    # 1. Python version
    v = sys.version_info
    if v >= (3, 12):
        _ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        _fail(f"Python {v.major}.{v.minor}.{v.micro} (requires >= 3.12)")
        all_ok = False

    # 2. Dependencies
    for pkg in ("httpx", "playwright", "pydantic", "pydantic_settings"):
        try:
            __import__(pkg)
            _ok(f"Package '{pkg}' installed")
        except ImportError:
            _fail(f"Package '{pkg}' not installed. Run: pip install -e '.[dev]'")
            all_ok = False

    # 3. Playwright browsers
    pw_path = shutil.which("playwright")
    if pw_path:
        _ok(f"Playwright CLI found: {pw_path}")
    else:
        _warn("Playwright CLI not in PATH. Run: playwright install chromium")

    # 4. .env file
    env_path = Path(".env")
    if env_path.exists():
        _ok(".env file found")
    else:
        _warn(".env file not found. Copy .env.example to .env and configure")

    # 5. Configuration
    try:
        from notion_gateway.config import get_config

        cfg = get_config()
        _ok(f"NOTION_TOKEN configured ({cfg.notion_token[:8]}...)")
        _ok(f"NOTION_REQUESTS_DATABASE_ID configured ({cfg.notion_requests_database_id[:8]}...)")

        if cfg.slack_bot_token:
            _ok("SLACK_BOT_TOKEN configured")
        else:
            _warn("SLACK_BOT_TOKEN not set (Slack notifications disabled)")
    except Exception as e:
        _fail(f"Configuration error: {e}")
        all_ok = False

    # 6. Data directory
    data_dir = Path("./data")
    if data_dir.exists():
        _ok("Data directory exists")
    else:
        _warn("Data directory not found. It will be created on first run.")

    # 7. Storage state
    storage_path = Path("./data/storage-state.json")
    if storage_path.exists():
        _ok(f"Browser session found: {storage_path}")
    else:
        _warn("No saved browser session. Run: notion-gateway auth")

    # 8. Notion API connectivity
    try:
        from notion_gateway.services.notion_api import notion_fetch

        data, _ = await notion_fetch("users/me")
        bot_name = data.get("name", "unknown")
        _ok(f"Notion API reachable (bot: {bot_name})")
    except Exception as e:
        _fail(f"Notion API unreachable: {e}")
        all_ok = False

    # 9. Database access
    try:
        from notion_gateway.config import get_config
        from notion_gateway.services.notion_api import notion_fetch as nf

        cfg = get_config()
        await nf(f"databases/{cfg.notion_requests_database_id}")
        _ok("Requests database accessible")
    except Exception as e:
        _fail(f"Cannot access requests database: {e}")
        all_ok = False

    print()
    if all_ok:
        print("\033[32mAll checks passed!\033[0m")
    else:
        print("\033[31mSome checks failed. Please fix the issues above.\033[0m")
    print()
