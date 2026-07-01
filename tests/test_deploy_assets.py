"""Tests for launchd deployment assets."""

from __future__ import annotations

import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHD = ROOT / "deploy" / "launchd"


def _load_plist(name: str) -> dict[str, object]:
    with (LAUNCHD / name).open("rb") as fh:
        return plistlib.load(fh)


def test_poll_launchdaemon_runs_as_agent_with_keepalive() -> None:
    plist = _load_plist("com.worxphere.notion-api-gateway.plist")

    assert plist["Label"] == "com.worxphere.notion-api-gateway"
    assert plist["UserName"] == "agent"
    assert plist["WorkingDirectory"] == "/Users/agent/project/notion-api-gateway-py"
    assert plist["ProgramArguments"] == ["/opt/homebrew/bin/uv", "run", "notion-gateway", "poll"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["ThrottleInterval"] == 30


def test_watchdog_launchdaemon_runs_periodically() -> None:
    plist = _load_plist("com.worxphere.notion-api-gateway-watchdog.plist")

    assert plist["Label"] == "com.worxphere.notion-api-gateway-watchdog"
    assert plist["UserName"] == "agent"
    assert plist["WorkingDirectory"] == "/Users/agent/project/notion-api-gateway-py"
    assert plist["ProgramArguments"] == [
        "/opt/homebrew/bin/uv",
        "run",
        "notion-gateway",
        "watchdog",
    ]
    assert plist["RunAtLoad"] is True
    assert plist["StartInterval"] == 300
    assert plist["ThrottleInterval"] == 30
