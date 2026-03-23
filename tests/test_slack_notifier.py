"""Tests for Slack notification formatting."""

from __future__ import annotations

from notion_gateway.services.slack_notifier import (
    DOMAIN_ALIASES,
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
        assert "Issued" in msg


class TestFormatTokenFailedMessage:
    def test_contains_error(self) -> None:
        msg = format_token_failed_message("Test Page", "Something went wrong", "https://notion.so/page")
        assert "Test Page" in msg
        assert "Something went wrong" in msg
        assert "Failed" in msg
