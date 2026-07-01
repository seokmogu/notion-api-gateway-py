"""Tests for external watchdog checks."""

from __future__ import annotations

import os
from pathlib import Path

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
