"""Tests for Slack notification formatting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from notion_gateway.services import slack_notifier
from notion_gateway.services.slack_notifier import (
    ADMIN_CONTACT,
    DOMAIN_ALIASES,
    _record_slack_audit,
    classify_user_error,
    format_token_failed_message,
    format_token_issued_message,
)


class TestDomainAliases:
    def test_bidirectional(self) -> None:
        assert DOMAIN_ALIASES["worxphere.ai"] == "jobkorea.co.kr"
        assert DOMAIN_ALIASES["jobkorea.co.kr"] == "worxphere.ai"


class TestFormatTokenIssuedMessage:
    def test_contains_key_info(self) -> None:
        msg = format_token_issued_message("Test Page", "ntn_abc123", "https://notion.so/page")
        assert "Test Page" in msg
        assert "ntn_abc123" in msg
        assert "https://notion.so/page" in msg
        assert "완료" in msg


class TestFormatTokenFailedMessage:
    def test_unknown_error_falls_back_to_raw_message(self) -> None:
        msg = format_token_failed_message(
            "Test Page", "Something went wrong", "https://notion.so/page"
        )
        assert "Test Page" in msg
        assert "Something went wrong" in msg
        assert "실패" in msg
        assert ADMIN_CONTACT in msg

    def test_includes_integration_name_on_permission_error(self) -> None:
        msg = format_token_failed_message(
            "개인 페이지",
            "페이지 관리자 권한 없음: ...",
            "https://notion.so/page",
            integration_name="API Access 개인 페이지 e6d599ef",
        )
        assert "API Access 개인 페이지 e6d599ef" in msg
        assert "개인 페이지이거나" in msg


class TestClassifyUserError:
    def test_admin_permission_denied_korean(self) -> None:
        msg = classify_user_error(
            "페이지 관리자 권한 없음: 자동 연결할 수 없음", integration_name="API Access foo"
        )
        assert "개인 페이지이거나" in msg
        assert "API Access foo" in msg

    def test_admin_permission_denied_english(self) -> None:
        msg = classify_user_error("Non-admin user cannot add integration")
        assert "개인 페이지이거나" in msg

    def test_lacks_admin_rights(self) -> None:
        msg = classify_user_error("user lacks admin rights on the page")
        assert "개인 페이지이거나" in msg

    def test_no_edit_access(self) -> None:
        msg = classify_user_error("target page does not have edit access for user")
        assert "편집 권한" in msg

    def test_different_workspace(self) -> None:
        msg = classify_user_error("Cannot add bot permission for a bot from a different workspace")
        assert "워크스페이스" in msg
        assert ADMIN_CONTACT in msg

    def test_session_expired(self) -> None:
        msg = classify_user_error("Session expired or unauthorized")
        assert "시스템 점검" in msg

    def test_token_input_not_found(self) -> None:
        msg = classify_user_error(
            "Could not retrieve integration token. "
            "The token input was not found after Show button click."
        )
        assert "Notion 페이지 구조 변경" in msg

    def test_unknown_falls_back(self) -> None:
        msg = classify_user_error("Some totally new error")
        assert msg == "Some totally new error"


class TestSlackAudit:
    @staticmethod
    def _patch_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        class _Cfg:
            slack_audit_log_path = path

        monkeypatch.setattr(slack_notifier, "get_config", lambda: _Cfg())

    def test_records_success_with_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit = tmp_path / "logs" / "slack_sent.jsonl"
        self._patch_path(monkeypatch, audit)

        _record_slack_audit(
            "seokmogu@worxphere.ai",
            ":white_check_mark: *Notion API 토큰 발급 완료*\n\n*조직명:* X",
            ok=True,
        )

        entry = json.loads(audit.read_text(encoding="utf-8").strip())
        assert entry["recipient"] == "seokmogu@worxphere.ai"
        assert entry["ok"] is True
        assert entry["summary"] == ":white_check_mark: *Notion API 토큰 발급 완료*"
        assert entry["chars"] > 0
        assert "reason" not in entry
        assert entry["ts"].endswith("+00:00")  # UTC ISO

    def test_records_failure_with_reason_and_appends(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        audit = tmp_path / "logs" / "slack_sent.jsonl"
        self._patch_path(monkeypatch, audit)

        _record_slack_audit("a@b.com", ":x: 실패", ok=False, reason="user_not_found")
        _record_slack_audit(
            "c@d.com", ":x: 실패2", ok=False, reason="slack_error:channel_not_found"
        )

        lines = audit.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # append-only
        first = json.loads(lines[0])
        assert first["ok"] is False
        assert first["reason"] == "user_not_found"
        assert json.loads(lines[1])["reason"] == "slack_error:channel_not_found"

    def test_never_raises_on_bad_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pointing the audit path at a file-under-a-file makes mkdir/open fail;
        # the helper must swallow it so notifications are never blocked.
        bad = Path(__file__) / "nope" / "slack_sent.jsonl"
        self._patch_path(monkeypatch, bad)
        _record_slack_audit("a@b.com", "msg", ok=True)  # must not raise
