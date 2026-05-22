"""Tests for request processing retry behavior."""

from __future__ import annotations

import pytest

from notion_gateway.services import request_processor
from notion_gateway.types import RequestRecord


@pytest.mark.asyncio
async def test_retry_issued_connects_before_token_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RequestRecord(
        id="request-1",
        organization="Jobplanet",
        page_url="https://www.notion.so/private-dcb0af6c35814925b62f669d6c07aebb",
        canonical_page_id="dcb0af6c-3581-4925-b62f-669d6c07aebb",
        requester_id=None,
        requester_email=None,
        status="Issued",
        token="ntn_test",
        integration_name="API Access Jobplanet 6c07aebb",
        connection_status=None,
        retry_count=0,
        error_message=None,
    )
    events: list[str] = []

    async def fake_get_issued_requests(limit: int = 10) -> list[RequestRecord]:
        return [record]

    async def fake_connect_integration_to_page(page_url: str, integration_name: str) -> bool:
        events.append("connect")
        return True

    async def fake_mark_request_connected(request_id: str) -> None:
        events.append("mark_connected")

    async def fake_verify_page_access(page_id: str, token: str) -> bool:
        events.append("verify")
        return True

    async def fake_notify_and_complete(request_id: str) -> None:
        events.append("complete")

    async def fake_mark_request_failed(
        request_id: str,
        message: str,
        retry_count: int = 0,
    ) -> None:
        events.append("fail")

    async def fake_notify_failure(
        request_page_id: str,
        error_message: str,
        integration_name: str | None = None,
    ) -> None:
        events.append("notify_failure")

    monkeypatch.setattr(request_processor, "get_issued_requests", fake_get_issued_requests)
    monkeypatch.setattr(
        request_processor,
        "connect_integration_to_page",
        fake_connect_integration_to_page,
    )
    monkeypatch.setattr(request_processor, "mark_request_connected", fake_mark_request_connected)
    monkeypatch.setattr(request_processor, "verify_page_access", fake_verify_page_access)
    monkeypatch.setattr(request_processor, "_notify_and_complete", fake_notify_and_complete)
    monkeypatch.setattr(request_processor, "mark_request_failed", fake_mark_request_failed)
    monkeypatch.setattr(request_processor, "notify_failure", fake_notify_failure)

    retried = await request_processor.retry_issued_requests()

    assert retried == 1
    assert events == ["connect", "mark_connected", "verify", "complete"]
