"""Tests for Notion API client."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from notion_gateway.config import reset_config
from notion_gateway.services.notion_api import notion_fetch, verify_token
from notion_gateway.types import NotionApiError


@pytest.fixture(autouse=True)
def _setup_config(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_config()
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test_token")
    monkeypatch.setenv("NOTION_REQUESTS_DATABASE_ID", "db-123")


class TestNotionFetch:
    @pytest.mark.asyncio
    async def test_success(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="https://api.notion.com/v1/users/me",
            json={"id": "user-1", "name": "Test Bot"},
            headers={"x-request-id": "req-123"},
        )
        data, request_id = await notion_fetch("users/me")
        assert data["name"] == "Test Bot"
        assert request_id == "req-123"

    @pytest.mark.asyncio
    async def test_api_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="https://api.notion.com/v1/pages/bad-id",
            status_code=404,
            json={"message": "Not found", "code": "object_not_found"},
        )
        with pytest.raises(NotionApiError, match="Not found"):
            await notion_fetch("pages/bad-id")

    @pytest.mark.asyncio
    async def test_retry_on_429(self, httpx_mock: HTTPXMock) -> None:
        # First call returns 429, second succeeds
        httpx_mock.add_response(
            url="https://api.notion.com/v1/users/me",
            status_code=429,
            headers={"Retry-After": "0"},
        )
        httpx_mock.add_response(
            url="https://api.notion.com/v1/users/me",
            json={"id": "user-1"},
        )
        data, _ = await notion_fetch("users/me", max_retries=2)
        assert data["id"] == "user-1"


class TestVerifyToken:
    @pytest.mark.asyncio
    async def test_valid_token(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="https://api.notion.com/v1/users/me",
            json={"id": "user-1"},
        )
        assert await verify_token("ntn_valid") is True

    @pytest.mark.asyncio
    async def test_invalid_token(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url="https://api.notion.com/v1/users/me",
            status_code=401,
            json={"message": "Unauthorized"},
        )
        assert await verify_token("ntn_invalid") is False
