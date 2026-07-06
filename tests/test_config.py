"""Tests for configuration module."""

from __future__ import annotations

import pytest

from notion_gateway.config import AppConfig, _apply_env_layer


@pytest.fixture(autouse=True)
def _clear_gateway_specific_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "NOTION_GATEWAY_TOKEN",
        "NOTION_GATEWAY_REQUESTS_DATABASE_ID",
        "NOTION_GATEWAY_EMAIL",
        "NOTION_GATEWAY_PASSWORD",
        "NOTION_GATEWAY_LOGIN_CODE",
        "NOTION_GATEWAY_SLACK_BOT_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)


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
        assert cfg.self_healing_enabled is True
        assert cfg.self_healing_admin_email == "seokmogu@worxphere.ai"
        assert cfg.self_healing_alert_cooldown_seconds == 900
        assert cfg.watchdog_admin_email == "seokmogu@worxphere.ai"
        assert cfg.watchdog_alert_cooldown_seconds == 900
        assert cfg.watchdog_poll_stale_seconds == 300

    def test_poll_interval_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
        monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "db-123")
        monkeypatch.setenv("REQUEST_POLL_INTERVAL_MS", "30000")
        cfg = AppConfig()  # type: ignore[call-arg]
        assert cfg.poll_interval_seconds == 30.0

    def test_missing_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        monkeypatch.delenv("NOTION_GATEWAY_TOKEN", raising=False)
        monkeypatch.delenv("NOTION_REQUESTS_DATABASE_ID", raising=False)
        monkeypatch.delenv("NOTION_GATEWAY_REQUESTS_DATABASE_ID", raising=False)
        with pytest.raises(Exception):
            AppConfig()  # type: ignore[call-arg]

    def test_gateway_specific_env_aliases_take_precedence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOTION_TOKEN", "ntn_wdc_or_legacy")
        monkeypatch.setenv("NOTION_GATEWAY_TOKEN", "ntn_gateway")
        monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "legacy-db")
        monkeypatch.setenv("NOTION_GATEWAY_REQUESTS_DATABASE_ID", "gateway-db")
        monkeypatch.setenv("NOTION_EMAIL", "seokmogu@worxphere.ai")
        monkeypatch.setenv("NOTION_GATEWAY_EMAIL", "notion-automation@worxphere.ai")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-legacy")
        monkeypatch.setenv("NOTION_GATEWAY_SLACK_BOT_TOKEN", "xoxb-gateway")

        cfg = AppConfig()  # type: ignore[call-arg]

        assert cfg.notion_token == "ntn_gateway"
        assert cfg.notion_requests_database_id == "gateway-db"
        assert cfg.notion_email == "notion-automation@worxphere.ai"
        assert cfg.slack_bot_token == "xoxb-gateway"

    def test_dotenv_gateway_aliases_override_legacy_names(self) -> None:
        target: dict[str, str] = {}

        _apply_env_layer(
            target,
            {
                "NOTION_TOKEN": "ntn_wdc_or_legacy",
                "NOTION_GATEWAY_TOKEN": "ntn_gateway",
                "NOTION_REQUESTS_DATABASE_ID": "legacy-db",
                "NOTION_GATEWAY_REQUESTS_DATABASE_ID": "gateway-db",
            },
        )

        assert target["notion_token"] == "ntn_gateway"
        assert target["notion_requests_database_id"] == "gateway-db"

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
