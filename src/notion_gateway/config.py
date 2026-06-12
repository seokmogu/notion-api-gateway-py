"""Application configuration via environment variables with Pydantic validation.

Mirrors the TypeScript version's behavior: .env file values take priority
over system environment variables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor all relative paths to the project root (where pyproject.toml lives)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file, stripping quotes. Returns empty dict if file missing."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Required
    notion_token: str = Field(min_length=1)
    notion_requests_database_id: str = Field(min_length=1)

    # Notion API
    notion_api_version: str = "2022-06-28"

    # Browser automation
    notion_browser_profile_dir: str = ""
    notion_headless: bool = True
    notion_integration_name_prefix: str = "API Access"
    notion_workspace_name: str | None = None
    notion_email: str | None = None
    notion_password: str | None = None
    notion_login_code: str | None = None

    # Slack
    slack_bot_token: str | None = None

    # Self-healing / admin escalation
    self_healing_enabled: bool = True
    self_healing_admin_email: str = "seokmogu@worxphere.ai"
    self_healing_alert_cooldown_seconds: int = Field(default=900, ge=60)

    # SSL
    no_ssl_verify: bool = False
    ssl_ca_file: str | None = None

    # Polling
    request_poll_interval_ms: int = Field(default=60000, ge=1000)
    request_poll_limit: int = Field(default=10, ge=1, le=100)

    # Network retry
    network_max_retries: int = Field(default=3, ge=1, le=10)
    network_backoff_seconds: int = Field(default=3600, ge=60)

    @field_validator("notion_browser_profile_dir")
    @classmethod
    def ensure_profile_dir(cls, v: str) -> str:
        if not v:
            v = str(_PROJECT_ROOT / "data" / "notion-browser-profile")
        elif not Path(v).is_absolute():
            v = str(_PROJECT_ROOT / v)
        Path(v).mkdir(parents=True, exist_ok=True)
        return v

    @property
    def poll_interval_seconds(self) -> float:
        return self.request_poll_interval_ms / 1000.0

    @property
    def storage_state_path(self) -> Path:
        return Path(self.notion_browser_profile_dir).parent / "storage-state.json"


_config: AppConfig | None = None


def get_config(env_file: str = ".env") -> AppConfig:
    global _config
    if _config is None:
        # Layer 1: .env.shared (committed defaults), Layer 2: .env (gitignored secrets)
        env_path = _PROJECT_ROOT / env_file
        shared_path = _PROJECT_ROOT / ".env.shared"
        init_kwargs: dict[str, Any] = {}
        for key, value in _load_dotenv(shared_path).items():
            init_kwargs[key.lower()] = value
        for key, value in _load_dotenv(env_path).items():
            init_kwargs[key.lower()] = value
        _config = AppConfig(**init_kwargs)  # type: ignore[arg-type]
    return _config


def reset_config() -> None:
    global _config
    _config = None
