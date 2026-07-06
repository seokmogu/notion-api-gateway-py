"""Tests for Notion internal integration management."""

from __future__ import annotations

from typing import Any

import pytest

from notion_gateway.services import notion_internal_api
from notion_gateway.services.notion_internal_api import (
    COMMENT_BOT_CAPABILITIES,
    REQUIRED_BOT_CAPABILITIES,
    BotInfo,
    create_integration,
    ensure_bot_required_capabilities,
    list_bots,
    merge_required_bot_capabilities,
    missing_required_bot_capabilities,
    update_bot_capabilities,
)


def test_missing_required_bot_capabilities_ignores_comment_gaps_by_default() -> None:
    capabilities = {
        "read_content": True,
        "insert_content": True,
        "update_content": True,
        "read_user_with_email": True,
        "read_user_without_email": True,
    }

    assert missing_required_bot_capabilities(capabilities) == []


def test_missing_required_bot_capabilities_detects_comment_gaps_when_requested() -> None:
    capabilities = {
        "read_content": True,
        "insert_content": True,
        "update_content": True,
        "read_user_with_email": True,
        "read_user_without_email": True,
    }

    assert missing_required_bot_capabilities(capabilities, include_comments=True) == [
        "read_comment",
        "insert_comment",
    ]


def test_merge_required_bot_capabilities_preserves_existing_keys() -> None:
    merged = merge_required_bot_capabilities({"custom_capability": "keep"})

    assert merged["custom_capability"] == "keep"
    for key in REQUIRED_BOT_CAPABILITIES:
        assert merged[key] is True
    for key in COMMENT_BOT_CAPABILITIES:
        assert key not in merged


def test_merge_required_bot_capabilities_adds_comments_when_requested() -> None:
    merged = merge_required_bot_capabilities({}, include_comments=True)

    for key in REQUIRED_BOT_CAPABILITIES | COMMENT_BOT_CAPABILITIES:
        assert merged[key] is True


@pytest.mark.asyncio
async def test_create_integration_requests_base_capabilities_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(endpoint: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        calls.append((endpoint, body))
        return {"pointer": {"id": "bot-1", "spaceId": "space-1"}}, 200

    monkeypatch.setattr(notion_internal_api, "_internal_post", fake_post)

    bot = await create_integration("API Access Test", "space-1")

    assert bot.bot_id == "bot-1"
    assert calls == [
        (
            "createDeveloperIntegrationV2",
            {
                "type": "create-bot",
                "name": "API Access Test",
                "spaceId": "space-1",
                "capabilities": REQUIRED_BOT_CAPABILITIES,
            },
        )
    ]


@pytest.mark.asyncio
async def test_create_integration_requests_comment_capabilities_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(endpoint: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        calls.append((endpoint, body))
        return {"pointer": {"id": "bot-1", "spaceId": "space-1"}}, 200

    monkeypatch.setattr(notion_internal_api, "_internal_post", fake_post)

    await create_integration("API Access Test", "space-1", include_comments=True)

    assert calls[0][1]["capabilities"] == {
        **REQUIRED_BOT_CAPABILITIES,
        **COMMENT_BOT_CAPABILITIES,
    }


@pytest.mark.asyncio
async def test_list_bots_parses_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(endpoint: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        assert endpoint == "getDeveloperBotsAndIntegrations"
        return (
            {
                "botIds": ["bot-1"],
                "recordMap": {
                    "bot": {
                        "bot-1": {
                            "value": {
                                "value": {
                                    "name": "API Access Test",
                                    "space_id": "space-1",
                                    "integration_id": "integration-1",
                                    "alive": True,
                                    "capabilities": {"read_comment": True},
                                }
                            }
                        }
                    }
                },
            },
            200,
        )

    monkeypatch.setattr(notion_internal_api, "_internal_post", fake_post)

    bots = await list_bots()

    assert bots == [
        BotInfo(
            bot_id="bot-1",
            name="API Access Test",
            space_id="space-1",
            integration_id="integration-1",
            alive=True,
            capabilities={"read_comment": True},
        )
    ]


@pytest.mark.asyncio
async def test_update_bot_capabilities_posts_bot_record_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post(endpoint: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        calls.append((endpoint, body))
        return {}, 200

    monkeypatch.setattr(notion_internal_api, "_internal_post", fake_post)

    await update_bot_capabilities("bot-1", "space-1", {"read_comment": True})

    endpoint, body = calls[0]
    operations = body["transactions"][0]["operations"]
    assert endpoint == "saveTransactionsMain"
    assert operations[0] == {
        "pointer": {"id": "bot-1", "table": "bot", "spaceId": "space-1"},
        "path": ["capabilities"],
        "command": "set",
        "args": {"read_comment": True},
    }
    assert operations[1]["command"] == "update"
    assert operations[1]["args"]["last_edited_at"] > 0


@pytest.mark.asyncio
async def test_ensure_bot_required_capabilities_updates_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[tuple[str, str, dict[str, Any]]] = []
    bot = BotInfo(
        bot_id="bot-1",
        name="API Access Test",
        space_id="space-1",
        integration_id="integration-1",
        alive=True,
        capabilities={"read_content": True},
    )

    async def fake_update(
        bot_id: str,
        space_id: str,
        capabilities: dict[str, Any],
    ) -> None:
        updates.append((bot_id, space_id, capabilities))

    monkeypatch.setattr(notion_internal_api, "update_bot_capabilities", fake_update)

    result = await ensure_bot_required_capabilities(bot, include_comments=True)

    assert result.changed is True
    assert "read_comment" in result.missing
    assert "insert_comment" in result.missing
    assert updates[0][0:2] == ("bot-1", "space-1")
    for key in REQUIRED_BOT_CAPABILITIES:
        assert updates[0][2][key] is True
