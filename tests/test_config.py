"""Tests for configuration module."""

from __future__ import annotations

import pytest

from notion_gateway.config import AppConfig


class TestAppConfig:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "ntn_test_token_123")
        monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "db-123")
        cfg = AppConfig()  # type: ignore[call-arg]
        assert cfg.notion_token == "ntn_test_token_123"
        assert cfg.notion_requests_database_id == "db-123"
        assert cfg.notion_api_version == "2022-06-28"
        assert cfg.notion_headless is True
        assert cfg.notion_integration_name_prefix == "API Access"
        assert cfg.request_poll_interval_ms == 60000
        assert cfg.request_poll_limit == 10
        assert cfg.network_max_retries == 3
        assert cfg.network_backoff_seconds == 3600

    def test_poll_interval_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
        monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "db-123")
        monkeypatch.setenv("REQUEST_POLL_INTERVAL_MS", "30000")
        cfg = AppConfig()  # type: ignore[call-arg]
        assert cfg.poll_interval_seconds == 30.0

    def test_missing_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        monkeypatch.delenv("NOTION_REQUESTS_DATABASE_ID", raising=False)
        with pytest.raises(Exception):
            AppConfig()  # type: ignore[call-arg]

    def test_optional_slack(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
        monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "db-123")
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        cfg = AppConfig()  # type: ignore[call-arg]
        assert cfg.slack_bot_token is None

    def test_poll_interval_min(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
        monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "db-123")
        monkeypatch.setenv("REQUEST_POLL_INTERVAL_MS", "500")
        with pytest.raises(Exception):
            AppConfig()  # type: ignore[call-arg]
