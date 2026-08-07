"""Tests for Notion integration provisioning behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

from notion_gateway.services import notion_api, notion_browser, notion_internal_api
from notion_gateway.services.notion_browser import provision_token_for_page
from notion_gateway.services.notion_internal_api import (
    BotCapabilityStatus,
    BotInfo,
    CreatedBot,
    NotionInternalApiError,
)


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


@pytest.mark.asyncio
async def test_existing_token_comment_upgrade_uses_exact_bot_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exact_bot = BotInfo(
        bot_id="bot-exact",
        name="API Access Duplicate",
        space_id="space-exact",
        integration_id="integration-exact",
        alive=True,
        capabilities={
            **notion_internal_api.REQUIRED_BOT_CAPABILITIES,
            **notion_internal_api.COMMENT_BOT_CAPABILITIES,
        },
    )

    async def fake_get_token_bot_id(token: str) -> str:
        assert token == "ntn_existing"
        return "bot-exact"

    async def fake_find_bot_by_id(bot_id: str) -> BotInfo:
        assert bot_id == "bot-exact"
        return exact_bot

    async def fail_update(*args: object, **kwargs: object) -> None:
        pytest.fail("already-enabled capabilities must remain a no-op")

    monkeypatch.setattr(notion_api, "get_token_bot_id", fake_get_token_bot_id, raising=False)
    monkeypatch.setattr(notion_internal_api, "find_bot_by_id", fake_find_bot_by_id, raising=False)
    monkeypatch.setattr(notion_internal_api, "update_bot_capabilities", fail_update)

    result = await notion_browser.ensure_existing_token_comment_capabilities(
        "ntn_existing",
        "API Access Duplicate",
    )

    assert result == notion_browser.ProvisioningResult(
        token="ntn_existing",
        integration_name="API Access Duplicate",
        bot_id="bot-exact",
        space_id="space-exact",
    )


@pytest.mark.asyncio
async def test_existing_token_comment_upgrade_rechecks_both_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_bot = _bot("API Access Exact")
    updated_bot = replace(
        missing_bot,
        capabilities={
            **notion_internal_api.REQUIRED_BOT_CAPABILITIES,
            **notion_internal_api.COMMENT_BOT_CAPABILITIES,
        },
    )
    lookups = iter([missing_bot, updated_bot])
    lookup_count = 0
    updates: list[dict[str, object]] = []

    async def fake_get_token_bot_id(token: str) -> str:
        return "bot-1"

    async def fake_find_bot_by_id(bot_id: str) -> BotInfo:
        nonlocal lookup_count
        lookup_count += 1
        return next(lookups)

    async def fake_update(
        bot_id: str,
        space_id: str,
        capabilities: dict[str, object],
    ) -> None:
        updates.append(capabilities)

    monkeypatch.setattr(notion_api, "get_token_bot_id", fake_get_token_bot_id, raising=False)
    monkeypatch.setattr(notion_internal_api, "find_bot_by_id", fake_find_bot_by_id, raising=False)
    monkeypatch.setattr(notion_internal_api, "update_bot_capabilities", fake_update)

    result = await notion_browser.ensure_existing_token_comment_capabilities(
        "ntn_existing",
        "API Access Exact",
    )

    assert result.bot_id == "bot-1"
    assert lookup_count == 2
    assert len(updates) == 1
    assert updates[0]["read_comment"] is True
    assert updates[0]["insert_comment"] is True


@pytest.mark.asyncio
async def test_exact_bot_connection_never_falls_back_to_duplicate_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_internal_connection(*args: object, **kwargs: object) -> None:
        raise NotionInternalApiError("temporary internal failure", endpoint="saveTransactions")

    async def fail_browser_fallback(*args: object, **kwargs: object) -> bool:
        pytest.fail("exact bot connection must not fall back to a duplicate integration name")

    monkeypatch.setattr(
        notion_internal_api,
        "connect_bot_to_page",
        fail_internal_connection,
    )
    monkeypatch.setattr(notion_browser, "_connect_via_browser", fail_browser_fallback)

    with pytest.raises(RuntimeError, match="Cannot connect exact integration bot bot-exact"):
        await notion_browser.connect_integration_to_page(
            "https://www.notion.so/dcb0af6c35814925b62f669d6c07aebb",
            "API Access Duplicate",
            bot_id="bot-exact",
            space_id="space-exact",
            include_comment_capabilities=True,
            allow_name_fallback=False,
        )
