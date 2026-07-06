"""Tests for CLI command wiring."""

from __future__ import annotations

import pytest

from notion_gateway import __main__ as cli
from notion_gateway.config import AppConfig
from notion_gateway.services.notion_internal_api import BotInfo


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


@pytest.mark.asyncio
async def test_comment_capabilities_dry_run_does_not_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated: list[str] = []

    async def fake_list_bots() -> list[BotInfo]:
        return [
            BotInfo(
                bot_id="bot-1",
                name="API Access Test",
                space_id="space-1",
                integration_id="integration-1",
                alive=True,
                capabilities={"read_content": True},
            )
        ]

    async def fake_ensure_bot_required_capabilities(bot: BotInfo) -> None:
        updated.append(bot.bot_id)

    monkeypatch.setattr("notion_gateway.services.notion_internal_api.list_bots", fake_list_bots)
    monkeypatch.setattr(
        "notion_gateway.services.notion_internal_api.ensure_bot_required_capabilities",
        fake_ensure_bot_required_capabilities,
    )

    await cli.cmd_comment_capabilities("API Access", execute=False)

    assert updated == []
