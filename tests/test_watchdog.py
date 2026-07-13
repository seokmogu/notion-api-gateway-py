"""Tests for external watchdog checks."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from notion_gateway.config import AppConfig
from notion_gateway.services import watchdog


def test_is_poll_command_matches_poll_worker() -> None:
    assert watchdog.is_poll_command("/opt/homebrew/bin/uv run notion-gateway poll")
    assert watchdog.is_poll_command("python -m notion_gateway.__main__ poll")
    assert not watchdog.is_poll_command("/opt/homebrew/bin/uv run notion-gateway watchdog")


def test_parse_ps_output_filters_current_pid() -> None:
    output = """
      100 /opt/homebrew/bin/uv run notion-gateway poll
      101 /opt/homebrew/bin/uv run notion-gateway watchdog
      102 zsh
    """

    assert watchdog.parse_ps_output(output, current_pid=100) == []
    assert watchdog.parse_ps_output(output, current_pid=999) == [
        (100, "/opt/homebrew/bin/uv run notion-gateway poll")
    ]


def test_collect_watchdog_issues_reports_missing_process_and_stale_log(tmp_path: Path) -> None:
    log_path = tmp_path / "poll.err.log"
    log_path.write_text("old log\n", encoding="utf-8")
    old_timestamp = 1000.0
    os.utime(log_path, (old_timestamp, old_timestamp))
    cfg = AppConfig(
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        watchdog_poll_log_path=str(log_path),
        watchdog_poll_stale_seconds=60,
    )

    issues = watchdog.collect_watchdog_issues(cfg, processes=[], now=old_timestamp + 120)

    assert [issue.code for issue in issues] == ["poll_process_missing", "poll_log_stale"]


def test_collect_watchdog_issues_passes_when_process_and_log_are_fresh(tmp_path: Path) -> None:
    log_path = tmp_path / "poll.err.log"
    log_path.write_text("fresh log\n", encoding="utf-8")
    cfg = AppConfig(
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        watchdog_poll_log_path=str(log_path),
        watchdog_poll_stale_seconds=60,
    )
    age = watchdog.get_log_age_seconds(log_path)
    assert age is not None

    issues = watchdog.collect_watchdog_issues(
        cfg,
        processes=[(123, "/opt/homebrew/bin/uv run notion-gateway poll")],
    )

    assert issues == []


@pytest.mark.asyncio
async def test_run_watchdog_sends_recovery_once_after_successful_alert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AppConfig(
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        watchdog_state_path=str(tmp_path / "watchdog-state.json"),
    )
    current_issues = [watchdog.WatchdogIssue("poll_log_stale", "poll log has not changed for 600s")]
    messages: list[str] = []

    async def fake_send_slack_dm(recipient: str, message: str) -> bool:
        assert recipient == cfg.watchdog_admin_email
        messages.append(message)
        return True

    monkeypatch.setattr(watchdog, "get_config", lambda: cfg)
    monkeypatch.setattr(
        watchdog,
        "collect_watchdog_issues",
        lambda _cfg: list(current_issues),
    )
    monkeypatch.setattr(watchdog, "is_slack_configured", lambda: True)
    monkeypatch.setattr(watchdog, "send_slack_dm", fake_send_slack_dm)

    alerted = await watchdog.run_watchdog()
    current_issues.clear()
    recovered = await watchdog.run_watchdog()
    healthy_again = await watchdog.run_watchdog()

    assert alerted.alerted is True
    assert recovered.ok is True
    assert recovered.recovered is True
    assert healthy_again.recovered is False
    assert len(messages) == 2
    assert "watchdog alert" in messages[0]
    assert "정상 상태로 복구" in messages[1]


@pytest.mark.asyncio
async def test_run_watchdog_does_not_recover_failed_alert_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AppConfig(
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        watchdog_state_path=str(tmp_path / "watchdog-state.json"),
    )
    current_issues = [watchdog.WatchdogIssue("poll_process_missing", "poll worker is missing")]
    messages: list[str] = []

    async def fake_send_slack_dm(recipient: str, message: str) -> bool:
        assert recipient == cfg.watchdog_admin_email
        messages.append(message)
        return False

    monkeypatch.setattr(watchdog, "get_config", lambda: cfg)
    monkeypatch.setattr(
        watchdog,
        "collect_watchdog_issues",
        lambda _cfg: list(current_issues),
    )
    monkeypatch.setattr(watchdog, "is_slack_configured", lambda: True)
    monkeypatch.setattr(watchdog, "send_slack_dm", fake_send_slack_dm)

    alerted = await watchdog.run_watchdog()
    current_issues.clear()
    healthy = await watchdog.run_watchdog()

    assert alerted.alerted is False
    assert healthy.recovered is False
    assert len(messages) == 1


@pytest.mark.asyncio
async def test_run_watchdog_retries_failed_recovery_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = AppConfig(
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        watchdog_state_path=str(tmp_path / "watchdog-state.json"),
    )
    current_issues = [watchdog.WatchdogIssue("poll_log_stale", "stale poll log")]
    send_results = iter([True, False, True])

    async def fake_send_slack_dm(_recipient: str, _message: str) -> bool:
        return next(send_results)

    monkeypatch.setattr(watchdog, "get_config", lambda: cfg)
    monkeypatch.setattr(
        watchdog,
        "collect_watchdog_issues",
        lambda _cfg: list(current_issues),
    )
    monkeypatch.setattr(watchdog, "is_slack_configured", lambda: True)
    monkeypatch.setattr(watchdog, "send_slack_dm", fake_send_slack_dm)

    assert (await watchdog.run_watchdog()).alerted is True
    current_issues.clear()
    assert (await watchdog.run_watchdog()).recovered is False
    assert watchdog._recovery_pending(cfg) is True
    assert (await watchdog.run_watchdog()).recovered is True
    assert watchdog._recovery_pending(cfg) is False


def test_alert_cooldown_preserves_pending_recovery(tmp_path: Path) -> None:
    cfg = AppConfig(
        notion_token="ntn_test",
        notion_requests_database_id="db-123",
        watchdog_alert_cooldown_seconds=60,
        watchdog_state_path=str(tmp_path / "watchdog-state.json"),
    )
    issues = [watchdog.WatchdogIssue("poll_log_stale", "stale poll log")]
    watchdog._write_state(
        cfg.watchdog_state_file,
        {
            "last_alert_at": 100.0,
            "last_fingerprint": "poll_log_stale",
            "last_issues": ["stale poll log"],
            "recovery_pending": True,
        },
    )

    assert watchdog._should_alert(cfg, issues, now=150.0) is False
    assert watchdog._recovery_pending(cfg) is True
    assert watchdog._should_alert(cfg, issues, now=161.0) is True
    assert watchdog._recovery_pending(cfg) is True
