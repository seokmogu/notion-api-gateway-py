"""Tests for request processing retry behavior."""

from __future__ import annotations

import logging

import pytest

from notion_gateway.services import notion_internal_api, request_processor
from notion_gateway.types import ProvisioningResult, RequestRecord


@pytest.mark.asyncio
async def test_long_backoff_emits_poll_liveness_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_sleep(seconds: float) -> None:
        assert seconds <= 1.0

    request_processor._shutdown_requested = False
    monkeypatch.setattr(request_processor.asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.INFO, logger=request_processor.__name__):
        await request_processor._sleep_interruptible(5, progress_log_interval=2)

    heartbeat_messages = [
        record.message
        for record in caplog.records
        if "alive during network backoff" in record.message
    ]
    assert heartbeat_messages == [
        "Poll worker alive during network backoff; retrying in 3s",
        "Poll worker alive during network backoff; retrying in 1s",
    ]


@pytest.mark.asyncio
async def test_normal_poll_sleep_does_not_emit_backoff_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_sleep(seconds: float) -> None:
        assert seconds <= 1.0

    request_processor._shutdown_requested = False
    monkeypatch.setattr(request_processor.asyncio, "sleep", fake_sleep)

    with caplog.at_level(logging.INFO, logger=request_processor.__name__):
        await request_processor._sleep_interruptible(3)

    assert not [
        record for record in caplog.records if "alive during network backoff" in record.message
    ]


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
        automation_permission_confirmed=True,
        comment_permission_requested=False,
        retry_count=0,
        error_message=None,
    )
    events: list[str] = []

    async def fake_get_issued_requests(limit: int = 10) -> list[RequestRecord]:
        return [record]

    async def fake_connect_integration_to_page(
        page_url: str,
        integration_name: str,
        include_comment_capabilities: bool = False,
    ) -> bool:
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


@pytest.mark.asyncio
async def test_invalid_external_share_notifies_requester_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RequestRecord(
        id="request-1",
        organization="Jobplanet",
        page_url="https://jobplanet.notion.site/Page-abc123",
        canonical_page_id=None,
        requester_id="user-1",
        requester_email="requester@example.com",
        status="Requested",
        token=None,
        integration_name=None,
        connection_status=None,
        automation_permission_confirmed=True,
        comment_permission_requested=False,
        retry_count=0,
        error_message=None,
    )
    events: list[tuple[str, str, str | None]] = []

    async def fake_mark_request_processing(request_id: str) -> None:
        events.append(("processing", request_id, None))

    async def fake_notify_requested(request_id: str) -> None:
        events.append(("requested", request_id, None))

    async def fake_mark_request_failed(
        request_id: str,
        message: str,
        retry_count: int = 0,
    ) -> None:
        events.append(("failed", message, str(retry_count)))

    async def fake_notify_failure(
        request_page_id: str,
        error_message: str,
        integration_name: str | None = None,
    ) -> None:
        events.append(("notify_failure", error_message, integration_name))

    monkeypatch.setattr(request_processor, "mark_request_processing", fake_mark_request_processing)
    monkeypatch.setattr(request_processor, "notify_requested", fake_notify_requested)
    monkeypatch.setattr(request_processor, "mark_request_failed", fake_mark_request_failed)
    monkeypatch.setattr(request_processor, "notify_failure", fake_notify_failure)

    await request_processor.process_one_request(record)

    assert events[0] == ("processing", "request-1", None)
    assert events[1] == ("requested", "request-1", None)
    assert events[2][0] == "failed"
    assert "notion.site URLs" in events[2][1]
    assert events[3][0] == "notify_failure"
    assert "notion.site URLs" in events[3][1]


@pytest.mark.asyncio
async def test_permission_denied_notifies_requester_with_integration_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = RequestRecord(
        id="request-1",
        organization="Private Page",
        page_url="https://www.notion.so/private-dcb0af6c35814925b62f669d6c07aebb",
        canonical_page_id=None,
        requester_id="user-1",
        requester_email="requester@example.com",
        status="Requested",
        token=None,
        integration_name=None,
        connection_status=None,
        automation_permission_confirmed=True,
        comment_permission_requested=True,
        retry_count=0,
        error_message=None,
    )
    events: list[tuple[str, str | bool | None, str | None]] = []

    async def fake_mark_request_processing(request_id: str) -> None:
        events.append(("processing", request_id, None))

    async def fake_notify_requested(request_id: str) -> None:
        events.append(("requested", request_id, None))

    async def fake_get_existing_token_for_page(canonical_page_id: str) -> None:
        return None

    async def fake_get_page_space_id(page_id: str) -> str:
        return "space-1"

    async def fake_provision_token_for_page(
        integration_name: str,
        target_space_id: str | None = None,
        include_comment_capabilities: bool = False,
    ) -> ProvisioningResult:
        events.append(("provision_comments", include_comment_capabilities, integration_name))
        return ProvisioningResult(
            token="ntn_test",
            integration_name=integration_name,
            bot_id="bot-1",
            space_id=target_space_id,
        )

    async def fake_mark_request_issued(
        request_id: str,
        token: str,
        integration_name: str,
        canonical_page_id: str,
    ) -> None:
        events.append(("issued", request_id, integration_name))

    async def fake_connect_integration_to_page(
        page_url: str,
        integration_name: str,
        bot_id: str | None = None,
        space_id: str | None = None,
        include_comment_capabilities: bool = False,
    ) -> bool:
        events.append(("connect_comments", include_comment_capabilities, integration_name))
        raise RuntimeError("페이지 관리자 권한 없음: 자동 연결할 수 없음")

    async def fake_mark_request_failed(
        request_id: str,
        message: str,
        retry_count: int = 0,
    ) -> None:
        events.append(("failed", message, str(retry_count)))

    async def fake_notify_failure(
        request_page_id: str,
        error_message: str,
        integration_name: str | None = None,
    ) -> None:
        events.append(("notify_failure", error_message, integration_name))

    monkeypatch.setattr(request_processor, "mark_request_processing", fake_mark_request_processing)
    monkeypatch.setattr(request_processor, "notify_requested", fake_notify_requested)
    monkeypatch.setattr(
        request_processor,
        "get_existing_token_for_page",
        fake_get_existing_token_for_page,
    )
    monkeypatch.setattr(notion_internal_api, "get_page_space_id", fake_get_page_space_id)
    monkeypatch.setattr(
        request_processor,
        "provision_token_for_page",
        fake_provision_token_for_page,
    )
    monkeypatch.setattr(request_processor, "mark_request_issued", fake_mark_request_issued)
    monkeypatch.setattr(
        request_processor,
        "connect_integration_to_page",
        fake_connect_integration_to_page,
    )
    monkeypatch.setattr(request_processor, "mark_request_failed", fake_mark_request_failed)
    monkeypatch.setattr(request_processor, "notify_failure", fake_notify_failure)

    await request_processor.process_one_request(record)

    assert ("provision_comments", True, "API Access Private Page 6c07aebb") in events
    assert ("connect_comments", True, "API Access Private Page 6c07aebb") in events
    assert events[-2][0] == "failed"
    assert events[-1] == (
        "notify_failure",
        "페이지 관리자 권한 없음: 자동 연결할 수 없음",
        "API Access Private Page 6c07aebb",
    )
