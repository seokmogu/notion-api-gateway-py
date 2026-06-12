"""Tests for the self-healing watchdog."""

from __future__ import annotations

import pytest

from notion_gateway.config import AppConfig
from notion_gateway.services import self_healing
from notion_gateway.services.self_healing import (
    SelfHealingAgent,
    format_self_healing_alert_message,
)


def _cfg() -> AppConfig:
    return AppConfig(  # type: ignore[call-arg]
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        slack_bot_token="xoxb-test",
        self_healing_alert_cooldown_seconds=60,
    )


def test_format_self_healing_alert_message() -> None:
    msg = format_self_healing_alert_message(
        {"session": "ok", "getSpaces": "fail: unauthorized", "listBots": "fail: unauthorized"},
        "refresh failed",
    )
    assert "자동 복구 실패" in msg
    assert "getSpaces: fail: unauthorized" in msg
    assert "refresh failed" in msg


async def test_repairs_without_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    health_results = iter(
        [
            {"session": "ok", "getSpaces": "fail: unauthorized", "listBots": "fail: unauthorized"},
            {"session": "ok", "getSpaces": "ok (1 spaces)", "listBots": "ok (2 bots)"},
        ]
    )
    alerts: list[str] = []

    async def fake_health_check() -> dict[str, str]:
        return next(health_results)

    async def fake_refresh_session() -> bool:
        return True

    async def fake_repair_saved_session_from_profile() -> bool:
        raise AssertionError("profile repair should not run after refresh success")

    async def fake_send_slack_dm(email: str, message: str) -> bool:
        alerts.append(message)
        return True

    monkeypatch.setattr(self_healing, "health_check", fake_health_check)
    monkeypatch.setattr(self_healing, "refresh_session", fake_refresh_session)
    monkeypatch.setattr(
        self_healing, "repair_saved_session_from_profile", fake_repair_saved_session_from_profile
    )
    monkeypatch.setattr(self_healing, "send_slack_dm", fake_send_slack_dm)

    assert await SelfHealingAgent(_cfg()).ensure_internal_api_ready() is True
    assert alerts == []


async def test_alerts_when_repair_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    alerts: list[tuple[str, str]] = []

    async def fake_health_check() -> dict[str, str]:
        return {
            "session": "ok",
            "getSpaces": "fail: unauthorized",
            "listBots": "fail: unauthorized",
        }

    async def fake_refresh_session() -> bool:
        return False

    async def fake_repair_saved_session_from_profile() -> bool:
        return False

    async def fake_send_slack_dm(email: str, message: str) -> bool:
        alerts.append((email, message))
        return True

    monkeypatch.setattr(self_healing, "health_check", fake_health_check)
    monkeypatch.setattr(self_healing, "refresh_session", fake_refresh_session)
    monkeypatch.setattr(
        self_healing, "repair_saved_session_from_profile", fake_repair_saved_session_from_profile
    )
    monkeypatch.setattr(self_healing, "send_slack_dm", fake_send_slack_dm)

    assert await SelfHealingAgent(_cfg()).ensure_internal_api_ready() is False
    assert alerts
    assert alerts[0][0] == "seokmogu@worxphere.ai"
    assert "자동 복구 실패" in alerts[0][1]
