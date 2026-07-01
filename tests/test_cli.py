"""Tests for CLI command wiring."""

from __future__ import annotations

import pytest

from notion_gateway import __main__ as cli
from notion_gateway.config import AppConfig


@pytest.mark.asyncio
async def test_process_uses_configured_poll_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def fake_preflight() -> bool:
        return True

    async def fake_process_pending_requests(limit: int = 10) -> int:
        calls.append(limit)
        return 0

    cfg = AppConfig(
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        request_poll_limit=10,
    )

    monkeypatch.setattr(cli, "_preflight_check", fake_preflight)
    monkeypatch.setattr("notion_gateway.config.get_config", lambda: cfg)
    monkeypatch.setattr(
        "notion_gateway.services.request_processor.process_pending_requests",
        fake_process_pending_requests,
    )

    await cli.cmd_process()

    assert calls == [10]
