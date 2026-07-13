"""External watchdog for the long-running poll worker."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from notion_gateway.config import AppConfig, get_config
from notion_gateway.services.slack_notifier import is_slack_configured, send_slack_dm

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchdogIssue:
    code: str
    message: str


@dataclass(frozen=True)
class WatchdogResult:
    ok: bool
    issues: list[WatchdogIssue]
    alerted: bool
    recovered: bool = False


def is_poll_command(command: str) -> bool:
    """Return True when a process command line looks like the poll worker."""
    normalized = " ".join(command.split())
    if not normalized or "watchdog" in normalized:
        return False
    return "notion-gateway poll" in normalized or (
        "notion_gateway" in normalized and " poll" in f" {normalized} "
    )


def parse_ps_output(output: str, current_pid: int | None = None) -> list[tuple[int, str]]:
    """Parse `ps -axo pid=,command=` output into matching poll processes."""
    matches: list[tuple[int, str]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pid_text, _, command = line.partition(" ")
        if not pid_text.isdigit():
            continue
        pid = int(pid_text)
        if current_pid is not None and pid == current_pid:
            continue
        if is_poll_command(command):
            matches.append((pid, command.strip()))
    return matches


def find_poll_processes() -> list[tuple[int, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("ps failed with exit %d: %s", result.returncode, result.stderr.strip())
        return []
    return parse_ps_output(result.stdout, current_pid=os.getpid())


def get_log_age_seconds(path: Path, now: float | None = None) -> float | None:
    if not path.exists():
        return None
    return (now or time.time()) - path.stat().st_mtime


def collect_watchdog_issues(
    cfg: AppConfig | None = None,
    *,
    processes: list[tuple[int, str]] | None = None,
    now: float | None = None,
) -> list[WatchdogIssue]:
    cfg = cfg or get_config()
    processes = find_poll_processes() if processes is None else processes
    issues: list[WatchdogIssue] = []

    if not processes:
        issues.append(
            WatchdogIssue(
                code="poll_process_missing",
                message="notion-gateway poll process is not running",
            )
        )

    age = get_log_age_seconds(cfg.watchdog_poll_log_file, now=now)
    if age is None:
        issues.append(
            WatchdogIssue(
                code="poll_log_missing",
                message=f"poll log is missing: {cfg.watchdog_poll_log_file}",
            )
        )
    elif age > cfg.watchdog_poll_stale_seconds:
        issues.append(
            WatchdogIssue(
                code="poll_log_stale",
                message=(
                    f"poll log has not changed for {int(age)}s "
                    f"(threshold={cfg.watchdog_poll_stale_seconds}s)"
                ),
            )
        )

    return issues


def _load_state(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to read watchdog state %s: %s", path, exc)
        return {}


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _issue_fingerprint(issues: list[WatchdogIssue]) -> str:
    return "|".join(issue.code for issue in issues)


def _should_alert(cfg: AppConfig, issues: list[WatchdogIssue], now: float) -> bool:
    state = _load_state(cfg.watchdog_state_file)
    fingerprint = _issue_fingerprint(issues)
    last_fingerprint = str(state.get("last_fingerprint", ""))
    last_alert_at = float(state.get("last_alert_at", 0) or 0)
    if (
        fingerprint == last_fingerprint
        and now - last_alert_at < cfg.watchdog_alert_cooldown_seconds
    ):
        return False
    state.update(
        {
            "last_alert_at": now,
            "last_fingerprint": fingerprint,
            "last_issues": [issue.message for issue in issues],
        }
    )
    _write_state(cfg.watchdog_state_file, state)
    return True


def _recovery_pending(cfg: AppConfig) -> bool:
    state = _load_state(cfg.watchdog_state_file)
    return state.get("recovery_pending") is True


def _mark_recovery_pending(cfg: AppConfig) -> None:
    state = _load_state(cfg.watchdog_state_file)
    state["recovery_pending"] = True
    _write_state(cfg.watchdog_state_file, state)


def _mark_recovered(cfg: AppConfig, now: float) -> None:
    state = _load_state(cfg.watchdog_state_file)
    state.update(
        {
            "recovery_pending": False,
            "last_recovered_at": now,
        }
    )
    _write_state(cfg.watchdog_state_file, state)


def format_watchdog_alert(issues: list[WatchdogIssue], cfg: AppConfig) -> str:
    issue_lines = "\n".join(f"- `{issue.code}`: {issue.message}" for issue in issues)
    return (
        ":rotating_light: *Notion API Gateway watchdog alert*\n\n"
        "토큰 발급 폴링 워커가 정상 상태가 아닙니다.\n\n"
        f"*Host:* `{socket.gethostname()}`\n"
        f"*Working dir:* `{Path.cwd()}`\n"
        f"*Poll log:* `{cfg.watchdog_poll_log_file}`\n"
        f"*Issues:*\n{issue_lines}\n\n"
        "맥미니에서 `notion-gateway doctor`와 `notion-gateway poll` 상태를 확인해 주세요."
    )


def format_watchdog_recovery(cfg: AppConfig) -> str:
    """Format a recovery notice scoped to the checks the watchdog actually performs."""
    return (
        ":white_check_mark: *Notion API Gateway watchdog recovery*\n\n"
        "토큰 발급 폴링 워커가 watchdog 기준 정상 상태로 복구되었습니다.\n\n"
        f"*Host:* `{socket.gethostname()}`\n"
        f"*Working dir:* `{Path.cwd()}`\n"
        f"*Poll log:* `{cfg.watchdog_poll_log_file}`\n\n"
        "poll 프로세스가 실행 중이고 로그가 다시 정상적으로 갱신되고 있습니다."
    )


async def run_watchdog() -> WatchdogResult:
    cfg = get_config()
    issues = collect_watchdog_issues(cfg)
    if not issues:
        logger.info("Watchdog healthy: poll worker is running and log is fresh")
        if not _recovery_pending(cfg):
            return WatchdogResult(ok=True, issues=[], alerted=False)
        if not is_slack_configured():
            logger.warning("Slack is not configured; watchdog recovery was not sent")
            return WatchdogResult(ok=True, issues=[], alerted=False)

        recovered = await send_slack_dm(
            cfg.watchdog_admin_email,
            format_watchdog_recovery(cfg),
        )
        if recovered:
            _mark_recovered(cfg, time.time())
            logger.info("Watchdog recovery sent to %s", cfg.watchdog_admin_email)
        else:
            logger.warning("Watchdog recovery could not be sent to %s", cfg.watchdog_admin_email)
        return WatchdogResult(ok=True, issues=[], alerted=False, recovered=recovered)

    for issue in issues:
        logger.error("Watchdog issue: %s", issue.message)

    now = time.time()
    if not is_slack_configured():
        logger.warning("Slack is not configured; watchdog alert was not sent")
        return WatchdogResult(ok=False, issues=issues, alerted=False)
    if not _should_alert(cfg, issues, now):
        logger.info("Watchdog alert suppressed by cooldown")
        return WatchdogResult(ok=False, issues=issues, alerted=False)

    alerted = await send_slack_dm(cfg.watchdog_admin_email, format_watchdog_alert(issues, cfg))
    if alerted:
        _mark_recovery_pending(cfg)
        logger.info("Watchdog alert sent to %s", cfg.watchdog_admin_email)
    else:
        logger.warning("Watchdog alert could not be sent to %s", cfg.watchdog_admin_email)
    return WatchdogResult(ok=False, issues=issues, alerted=alerted)
