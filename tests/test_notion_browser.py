"""Tests for Notion integration provisioning behavior."""

from __future__ import annotations

import pytest

from notion_gateway.services import notion_internal_api
from notion_gateway.services.notion_browser import provision_token_for_page
from notion_gateway.services.notion_internal_api import BotCapabilityStatus, BotInfo, CreatedBot


def _bot(name: str = "API Access Test") -> BotInfo:
    return BotInfo(
        bot_id="bot-1",
        name=name,
        space_id="space-1",
        integration_id="integration-1",
        alive=True,
        capabilities={"read_content": True},
    )


@pytest.mark.asyncio
async def test_provision_reused_integration_uses_base_capabilities_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def fake_find_bot_by_name(name: str) -> BotInfo:
        events.append("find")
        return _bot(name)

    async def fake_ensure_bot_required_capabilities(
        bot: BotInfo,
        *,
        include_comments: bool = False,
    ) -> BotCapabilityStatus:
        events.append(f"ensure:{include_comments}")
        return BotCapabilityStatus(bot.bot_id, bot.name, ["insert_content"], changed=True)

    async def fake_get_bot_token(bot_id: str) -> str:
        events.append("token")
        return "ntn_test"

    monkeypatch.setattr(notion_internal_api, "find_bot_by_name", fake_find_bot_by_name)
    monkeypatch.setattr(
        notion_internal_api,
        "ensure_bot_required_capabilities",
        fake_ensure_bot_required_capabilities,
    )
    monkeypatch.setattr(notion_internal_api, "get_bot_token", fake_get_bot_token)

    result = await provision_token_for_page("API Access Test", target_space_id="space-1")

    assert result.token == "ntn_test"
    assert events == ["find", "ensure:False", "token"]


@pytest.mark.asyncio
async def test_provision_new_integration_repairs_comment_capabilities_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    find_calls = 0

    async def fake_find_bot_by_name(name: str) -> BotInfo | None:
        nonlocal find_calls
        find_calls += 1
        events.append(f"find:{find_calls}")
        return None if find_calls == 1 else _bot(name)

    async def fake_get_available_spaces() -> list[str]:
        events.append("spaces")
        return ["space-1"]

    async def fake_create_integration(
        name: str,
        space_id: str,
        *,
        include_comments: bool = False,
    ) -> CreatedBot:
        events.append(f"create:{include_comments}")
        return CreatedBot(bot_id="bot-1", space_id=space_id)

    async def fake_ensure_bot_required_capabilities(
        bot: BotInfo,
        *,
        include_comments: bool = False,
    ) -> BotCapabilityStatus:
        events.append(f"ensure:{include_comments}")
        return BotCapabilityStatus(bot.bot_id, bot.name, ["read_comment"], changed=True)

    async def fake_get_bot_token(bot_id: str) -> str:
        events.append("token")
        return "ntn_test"

    monkeypatch.setattr(notion_internal_api, "find_bot_by_name", fake_find_bot_by_name)
    monkeypatch.setattr(notion_internal_api, "get_available_spaces", fake_get_available_spaces)
    monkeypatch.setattr(notion_internal_api, "create_integration", fake_create_integration)
    monkeypatch.setattr(
        notion_internal_api,
        "ensure_bot_required_capabilities",
        fake_ensure_bot_required_capabilities,
    )
    monkeypatch.setattr(notion_internal_api, "get_bot_token", fake_get_bot_token)

    result = await provision_token_for_page(
        "API Access Test",
        target_space_id="space-1",
        include_comment_capabilities=True,
    )

    assert result.token == "ntn_test"
    assert events == ["find:1", "spaces", "create:True", "find:2", "ensure:True", "token"]
