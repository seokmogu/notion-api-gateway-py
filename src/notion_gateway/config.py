"""Application configuration via environment variables with Pydantic validation."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required
    notion_token: str = Field(min_length=1)
    notion_requests_database_id: str = Field(min_length=1)

    # Notion API
    notion_api_version: str = "2022-06-28"

    # Browser automation
    notion_browser_profile_dir: str = "./data/notion-browser-profile"
    notion_headless: bool = True
    notion_integration_name_prefix: str = "API Access"
    notion_workspace_name: str | None = None
    notion_email: str | None = None
    notion_password: str | None = None
    notion_login_code: str | None = None

    # Slack
    slack_bot_token: str | None = None

    # Polling
    request_poll_interval_ms: int = Field(default=15000, ge=1000)
    request_poll_limit: int = Field(default=10, ge=1, le=100)

    @field_validator("notion_browser_profile_dir")
    @classmethod
    def ensure_profile_dir(cls, v: str) -> str:
        Path(v).mkdir(parents=True, exist_ok=True)
        return v

    @property
    def poll_interval_seconds(self) -> float:
        return self.request_poll_interval_ms / 1000.0

    @property
    def storage_state_path(self) -> Path:
        return Path(self.notion_browser_profile_dir).parent / "storage-state.json"


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()  # type: ignore[call-arg]
    return _config


def reset_config() -> None:
    global _config
    _config = None
