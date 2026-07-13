"""Tests for the self-healing watchdog."""

from __future__ import annotations

import pytest

from notion_gateway.config import AppConfig
from notion_gateway.services import self_healing
from notion_gateway.services.self_healing import (
    SelfHealingAgent,
    format_self_healing_alert_message,
    format_self_healing_recovery_message,
)


def _cfg(min_consecutive_failures: int = 1) -> AppConfig:
    return AppConfig(  # type: ignore[call-arg]
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        slack_bot_token="xoxb-test",
        self_healing_alert_cooldown_seconds=60,
        self_healing_alert_min_consecutive_failures=min_consecutive_failures,
    )


def test_format_self_healing_alert_message() -> None:
    msg = format_self_healing_alert_message(
        {"session": "ok", "getSpaces": "fail: unauthorized", "listBots": "fail: unauthorized"},
        "refresh failed",
    )
    assert "자동 복구 실패" in msg
    assert "getSpaces: fail: unauthorized" in msg
    assert "refresh failed" in msg


def test_format_self_healing_recovery_message() -> None:
    msg = format_self_healing_recovery_message(
        {"session": "ok", "getSpaces": "ok (3 spaces)", "listBots": "ok (119 bots)"}
    )
    assert "정상 복구" in msg
    assert "listBots: ok (119 bots)" in msg


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


async def test_alert_then_healthy_sends_one_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    unhealthy = {
        "session": "ok",
        "getSpaces": "fail: upstream 502",
        "listBots": "fail: upstream 502",
    }
    healthy = {
        "session": "ok",
        "getSpaces": "ok (3 spaces)",
        "listBots": "ok (119 bots)",
    }
    health_results = iter([unhealthy, unhealthy, healthy, healthy])
    messages: list[str] = []

    async def fake_health_check() -> dict[str, str]:
        return next(health_results)

    async def fake_refresh_session() -> bool:
        return False

    async def fake_repair_saved_session_from_profile() -> bool:
        return False

    async def fake_send_slack_dm(_email: str, message: str) -> bool:
        messages.append(message)
        return True

    monkeypatch.setattr(self_healing, "health_check", fake_health_check)
    monkeypatch.setattr(self_healing, "refresh_session", fake_refresh_session)
    monkeypatch.setattr(
        self_healing,
        "repair_saved_session_from_profile",
        fake_repair_saved_session_from_profile,
    )
    monkeypatch.setattr(self_healing, "send_slack_dm", fake_send_slack_dm)

    agent = SelfHealingAgent(_cfg())
    assert await agent.ensure_internal_api_ready() is False
    assert await agent.ensure_internal_api_ready() is True
    assert await agent.ensure_internal_api_ready() is True

    assert len(messages) == 2
    assert "자동 복구 실패" in messages[0]
    assert "정상 복구" in messages[1]


async def test_defers_alert_until_consecutive_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single failed cycle must not page; only the Nth consecutive one does."""
    alerts: list[str] = []

    async def fake_health_check() -> dict[str, str]:
        return {"session": "ok", "getSpaces": "fail: x", "listBots": "fail: x"}

    async def fake_refresh_session() -> bool:
        return False

    async def fake_repair_saved_session_from_profile() -> bool:
        return False

    async def fake_send_slack_dm(_email: str, message: str) -> bool:
        alerts.append(message)
        return True

    monkeypatch.setattr(self_healing, "health_check", fake_health_check)
    monkeypatch.setattr(self_healing, "refresh_session", fake_refresh_session)
    monkeypatch.setattr(
        self_healing, "repair_saved_session_from_profile", fake_repair_saved_session_from_profile
    )
    monkeypatch.setattr(self_healing, "send_slack_dm", fake_send_slack_dm)

    agent = SelfHealingAgent(_cfg(min_consecutive_failures=3))
    assert await agent.ensure_internal_api_ready() is False
    assert alerts == []  # 1st failure: deferred
    assert await agent.ensure_internal_api_ready() is False
    assert alerts == []  # 2nd failure: still deferred
    assert await agent.ensure_internal_api_ready() is False
    assert len(alerts) == 1  # 3rd consecutive failure: escalate


async def test_transient_failure_then_recovery_resets_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure that recovers on the next cycle must not eventually page."""
    health_results = iter(
        [
            # cycle 1: unhealthy, repair fails -> deferred (1/3)
            {"session": "ok", "getSpaces": "fail: x", "listBots": "fail: x"},
            {"session": "ok", "getSpaces": "fail: x", "listBots": "fail: x"},
            # cycle 2: healthy again -> counter resets to 0
            {"session": "ok", "getSpaces": "ok (1 spaces)", "listBots": "ok (2 bots)"},
            # cycle 3: unhealthy again, repair fails -> deferred (1/3), NOT 2/3
            {"session": "ok", "getSpaces": "fail: x", "listBots": "fail: x"},
            {"session": "ok", "getSpaces": "fail: x", "listBots": "fail: x"},
        ]
    )
    alerts: list[str] = []

    async def fake_health_check() -> dict[str, str]:
        return next(health_results)

    async def fake_refresh_session() -> bool:
        return False

    async def fake_repair_saved_session_from_profile() -> bool:
        return False

    async def fake_send_slack_dm(_email: str, message: str) -> bool:
        alerts.append(message)
        return True

    monkeypatch.setattr(self_healing, "health_check", fake_health_check)
    monkeypatch.setattr(self_healing, "refresh_session", fake_refresh_session)
    monkeypatch.setattr(
        self_healing, "repair_saved_session_from_profile", fake_repair_saved_session_from_profile
    )
    monkeypatch.setattr(self_healing, "send_slack_dm", fake_send_slack_dm)

    agent = SelfHealingAgent(_cfg(min_consecutive_failures=3))
    assert await agent.ensure_internal_api_ready() is False  # fail 1/3
    assert await agent.ensure_internal_api_ready() is True  # recovered, reset
    assert await agent.ensure_internal_api_ready() is False  # fail 1/3 again
    assert alerts == []  # never reached threshold -> no false alarm
