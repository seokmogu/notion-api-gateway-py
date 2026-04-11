"""Data models for Notion API Gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestRecord:
    """A parsed request from the Notion form database."""

    id: str
    organization: str
    page_url: str | None
    canonical_page_id: str | None
    requester_id: str | None
    requester_email: str | None
    status: str
    token: str | None
    integration_name: str | None
    connection_status: str | None
    retry_count: int
    error_message: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class TokenRecord:
    """An issued token record."""

    page_id: str
    token: str
    integration_name: str
    request_id: str


@dataclass
class ProvisioningResult:
    """Result of token provisioning (via internal API or browser)."""

    token: str
    integration_name: str
    bot_id: str | None = None
    space_id: str | None = None


class NotionApiError(Exception):
    """Error from Notion API."""

    def __init__(
        self,
        message: str,
        status: int = 0,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.request_id = request_id
        self.details = details or {}
