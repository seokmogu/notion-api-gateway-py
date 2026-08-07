"""Tests for request processing retry behavior."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from notion_gateway.services import notion_internal_api, request_processor
from notion_gateway.types import NotionApiError, ProvisioningResult, RequestRecord

_PAGE_ID = "dcb0af6c-3581-4925-b62f-669d6c07aebb"
_PAGE_URL = f"https://www.notion.so/private-{_PAGE_ID.replace('-', '')}"


def _reuse_request(*, comments: bool) -> RequestRecord:
    return RequestRecord(
        id="request-new",
        organization="Private Page",
        page_url=_PAGE_URL,
        canonical_page_id=None,
        requester_id="user-1",
        requester_email="requester@example.com",
        status="Requested",
        token=None,
        integration_name=None,
        connection_status=None,
        automation_permission_confirmed=True,
        comment_permission_requested=comments,
        retry_count=0,
        error_message=None,
    )


def _existing_request() -> RequestRecord:
    return RequestRecord(
        id="request-existing",
        organization="Private Page",
        page_url=_PAGE_URL,
        canonical_page_id=_PAGE_ID,
        requester_id="user-old",
        requester_email="old@example.com",
        status="완료",
        token="ntn_existing",
        integration_name="API Access Private Page 6c07aebb",
        connection_status="Yes",
        automation_permission_confirmed=True,
        comment_permission_requested=False,
        retry_count=0,
        error_message=None,
    )


class _ExistingTokenHarness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, comments: bool) -> None:
        self.record = _reuse_request(comments=comments)
        self.existing = _existing_request()
        self.events: list[tuple[object, ...]] = []
        self.page_access: bool | Exception = True
        self.capability_error: Exception | None = None
        self.connect_error: Exception | None = None
        self.connected = True
        self.comment_access: bool | Exception = True
        self.provision_result: ProvisioningResult | None = None

        monkeypatch.setattr(
            request_processor,
            "get_config",
            lambda: SimpleNamespace(notion_integration_name_prefix="API Access"),
        )
        monkeypatch.setattr(request_processor, "mark_request_processing", self.mark_processing)
        monkeypatch.setattr(request_processor, "notify_requested", self.notify_requested)
        monkeypatch.setattr(
            request_processor,
            "get_existing_token_for_page",
            self.get_existing_token,
        )
        monkeypatch.setattr(request_processor, "verify_page_access", self.verify_page_access)
        monkeypatch.setattr(notion_internal_api, "get_page_space_id", self.get_page_space_id)
        monkeypatch.setattr(
            request_processor,
            "provision_token_for_page",
            self.provision_new_token,
        )
        monkeypatch.setattr(
            request_processor,
            "ensure_existing_token_comment_capabilities",
            self.ensure_comment_capabilities,
            raising=False,
        )
        monkeypatch.setattr(
            request_processor,
            "connect_integration_to_page",
            self.connect_integration,
        )
        monkeypatch.setattr(
            request_processor,
            "verify_comment_access",
            self.verify_comment_access,
            raising=False,
        )
        monkeypatch.setattr(request_processor, "mark_request_issued", self.mark_issued)
        monkeypatch.setattr(request_processor, "mark_request_connected", self.mark_connected)
        monkeypatch.setattr(request_processor, "mark_request_completed", self.mark_completed)
        monkeypatch.setattr(request_processor, "_notify_and_complete", self.notify_and_complete)
        monkeypatch.setattr(request_processor, "mark_request_failed", self.mark_failed)
        monkeypatch.setattr(request_processor, "notify_failure", self.notify_failure)
        monkeypatch.setattr(request_processor, "get_pending_requests", self.get_pending_requests)

    async def mark_processing(self, request_id: str) -> None:
        self.events.append(("processing", request_id))

    async def notify_requested(self, request_id: str) -> None:
        self.events.append(("requested", request_id))

    async def get_existing_token(self, canonical_page_id: str) -> RequestRecord:
        self.events.append(("find_existing", canonical_page_id))
        return self.existing

    async def verify_page_access(self, page_id: str, token: str) -> bool:
        self.events.append(("verify_page", page_id, token))
        if isinstance(self.page_access, Exception):
            raise self.page_access
        return self.page_access

    async def get_page_space_id(self, page_id: str) -> str:
        if self.provision_result is None:
            pytest.fail("valid existing-token tests must never enter live provisioning")
        return "space-new"

    async def provision_new_token(
        self,
        integration_name: str,
        target_space_id: str | None = None,
        include_comment_capabilities: bool = False,
    ) -> ProvisioningResult:
        if self.provision_result is None:
            pytest.fail("valid existing-token tests must never enter live provisioning")
        self.events.append(
            (
                "provision",
                integration_name,
                target_space_id,
                include_comment_capabilities,
            )
        )
        return self.provision_result

    async def ensure_comment_capabilities(
        self,
        token: str,
        integration_name: str,
    ) -> ProvisioningResult:
        self.events.append(("ensure_comments", token, integration_name))
        if self.capability_error and token == self.existing.token:
            raise self.capability_error
        return ProvisioningResult(
            token=token,
            integration_name=integration_name,
            bot_id="bot-exact",
            space_id="space-exact",
        )

    async def connect_integration(
        self,
        page_url: str,
        integration_name: str,
        bot_id: str | None = None,
        space_id: str | None = None,
        include_comment_capabilities: bool = False,
        allow_name_fallback: bool = True,
    ) -> bool:
        self.events.append(
            (
                "connect",
                page_url,
                integration_name,
                bot_id,
                space_id,
                include_comment_capabilities,
                allow_name_fallback,
            )
        )
        if self.connect_error:
            raise self.connect_error
        return self.connected

    async def verify_comment_access(self, page_id: str, token: str) -> bool:
        self.events.append(("verify_comments", page_id, token))
        if isinstance(self.comment_access, Exception):
            raise self.comment_access
        return self.comment_access

    async def mark_issued(
        self,
        request_id: str,
        token: str,
        integration_name: str,
        canonical_page_id: str,
    ) -> None:
        self.events.append(("issued", request_id, token, integration_name, canonical_page_id))

    async def mark_connected(self, request_id: str) -> None:
        self.events.append(("connected", request_id))

    async def mark_completed(self, request_id: str) -> None:
        self.events.append(("completed", request_id))

    async def notify_and_complete(
        self,
        request_id: str,
        existing_requester_email: str | None = None,
    ) -> None:
        self.events.append(("notify_complete", request_id, existing_requester_email))

    async def mark_failed(
        self,
        request_id: str,
        message: str,
        retry_count: int = 0,
    ) -> None:
        self.events.append(("failed", request_id, message, retry_count))

    async def notify_failure(
        self,
        request_id: str,
        message: str,
        integration_name: str | None = None,
    ) -> None:
        self.events.append(("notify_failure", request_id, message, integration_name))

    async def get_pending_requests(self, limit: int = 10) -> list[RequestRecord]:
        return [self.record]


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
        allow_name_fallback: bool = True,
    ) -> bool:
        events.append(
            (
                "connect_comments",
                include_comment_capabilities,
                integration_name,
                allow_name_fallback,
            )
        )
        raise RuntimeError("페이지 관리자 권한 없음: 자동 연결할 수 없음")

    async def fake_ensure_existing_comments(
        token: str,
        integration_name: str,
    ) -> ProvisioningResult:
        return ProvisioningResult(
            token=token,
            integration_name=integration_name,
            bot_id="bot-1",
            space_id="space-1",
        )

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
        "ensure_existing_token_comment_capabilities",
        fake_ensure_existing_comments,
    )
    monkeypatch.setattr(
        request_processor,
        "connect_integration_to_page",
        fake_connect_integration_to_page,
    )
    monkeypatch.setattr(request_processor, "mark_request_failed", fake_mark_request_failed)
    monkeypatch.setattr(request_processor, "notify_failure", fake_notify_failure)

    await request_processor.process_one_request(record)

    assert ("provision_comments", True, "API Access Private Page 6c07aebb") in events
    assert (
        "connect_comments",
        True,
        "API Access Private Page 6c07aebb",
        False,
    ) in events
    assert events[-2][0] == "failed"
    assert events[-1] == (
        "notify_failure",
        "페이지 관리자 권한 없음: 자동 연결할 수 없음",
        "API Access Private Page 6c07aebb",
    )


@pytest.mark.asyncio
async def test_existing_token_comment_request_upgrades_exact_bot_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    harness.page_access = False

    await request_processor.process_one_request(harness.record)

    assert ("ensure_comments", "ntn_existing", "API Access Private Page 6c07aebb") in (
        harness.events
    )
    assert (
        "connect",
        _PAGE_URL,
        "API Access Private Page 6c07aebb",
        "bot-exact",
        "space-exact",
        True,
        False,
    ) in harness.events
    assert ("verify_comments", _PAGE_ID, "ntn_existing") in harness.events
    assert (
        "issued",
        "request-new",
        "ntn_existing",
        "API Access Private Page 6c07aebb",
        _PAGE_ID,
    ) in harness.events
    event_names = [event[0] for event in harness.events]
    assert event_names.index("ensure_comments") < event_names.index("connect")
    assert event_names.index("connect") < event_names.index("verify_comments")
    assert event_names.index("verify_comments") < event_names.index("issued")
    assert event_names[-2:] == ["connected", "notify_complete"]
    assert harness.events[-1] == ("notify_complete", "request-new", "old@example.com")


@pytest.mark.asyncio
async def test_existing_token_without_comment_request_keeps_minimum_permission_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=False)

    await request_processor.process_one_request(harness.record)

    event_names = [event[0] for event in harness.events]
    assert "ensure_comments" not in event_names
    assert "connect" not in event_names
    assert "verify_comments" not in event_names
    assert "connected" not in event_names
    assert (
        "issued",
        "request-new",
        "ntn_existing",
        "API Access Private Page 6c07aebb",
        _PAGE_ID,
    ) in harness.events
    assert event_names[-1] == "notify_complete"
    assert harness.events[-1] == ("notify_complete", "request-new", "old@example.com")


@pytest.mark.asyncio
async def test_existing_token_capability_upgrade_failure_is_not_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    harness.capability_error = RuntimeError("cannot update exact bot")

    processed = await request_processor.process_pending_requests()

    assert processed == 0
    event_names = [event[0] for event in harness.events]
    assert "failed" in event_names
    assert "connect" not in event_names
    assert "issued" not in event_names
    assert "completed" not in event_names
    assert "notify_complete" not in event_names


@pytest.mark.asyncio
async def test_existing_token_page_permission_denied_keeps_requester_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    message = "페이지 관리자 권한 없음: 자동 연결할 수 없음"
    harness.connect_error = RuntimeError(message)

    await request_processor.process_one_request(harness.record)

    assert (
        "notify_failure",
        "request-new",
        message,
        "API Access Private Page 6c07aebb",
    ) in harness.events
    event_names = [event[0] for event in harness.events]
    assert "issued" not in event_names
    assert "notify_complete" not in event_names


@pytest.mark.asyncio
async def test_existing_token_comment_request_network_error_is_not_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    harness.page_access = NotionApiError("temporary network failure")

    processed = await request_processor.process_pending_requests()

    assert processed == 0
    event_names = [event[0] for event in harness.events]
    assert "failed" in event_names
    assert "ensure_comments" not in event_names
    assert "issued" not in event_names
    assert "notify_complete" not in event_names


@pytest.mark.asyncio
async def test_existing_token_comment_api_must_succeed_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    harness.comment_access = False

    processed = await request_processor.process_pending_requests()

    assert processed == 0
    event_names = [event[0] for event in harness.events]
    assert "ensure_comments" in event_names
    assert "connect" in event_names
    assert "verify_comments" in event_names
    assert "failed" in event_names
    assert "issued" not in event_names
    assert "notify_complete" not in event_names


@pytest.mark.asyncio
async def test_invalid_existing_token_is_reprovisioned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    harness.capability_error = NotionApiError("unauthorized", status=401)
    harness.provision_result = ProvisioningResult(
        token="ntn_new",
        integration_name="API Access Private Page 6c07aebb",
        bot_id="bot-new",
        space_id="space-new",
    )

    await request_processor.process_one_request(harness.record)

    assert (
        "provision",
        "API Access Private Page 6c07aebb",
        "space-new",
        True,
    ) in harness.events
    issued = [event for event in harness.events if event[0] == "issued"]
    assert issued[-1][2] == "ntn_new"


@pytest.mark.asyncio
async def test_failed_comment_request_resumes_its_issued_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    harness.record.status = "Failed"
    harness.record.token = "ntn_issued"
    harness.record.integration_name = "API Access Issued"
    harness.record.canonical_page_id = _PAGE_ID
    harness.record.retry_count = 1

    await request_processor.process_one_request(harness.record)

    assert ("ensure_comments", "ntn_issued", "API Access Issued") in harness.events
    assert not [event for event in harness.events if event[0] == "find_existing"]
    assert not [event for event in harness.events if event[0] == "provision"]
    issued = [event for event in harness.events if event[0] == "issued"]
    assert issued[-1][2] == "ntn_issued"


@pytest.mark.asyncio
async def test_invalid_current_token_falls_back_to_completed_same_page_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _ExistingTokenHarness(monkeypatch, comments=True)
    harness.record.status = "Failed"
    harness.record.token = "ntn_stale"
    harness.record.integration_name = "API Access Stale"
    harness.record.canonical_page_id = _PAGE_ID
    original_ensure = harness.ensure_comment_capabilities

    async def ensure_candidate(token: str, integration_name: str) -> ProvisioningResult:
        if token == "ntn_stale":
            harness.events.append(("ensure_comments", token, integration_name))
            raise NotionApiError("unauthorized", status=401)
        return await original_ensure(token, integration_name)

    monkeypatch.setattr(
        request_processor,
        "ensure_existing_token_comment_capabilities",
        ensure_candidate,
    )

    await request_processor.process_one_request(harness.record)

    ensure_events = [event for event in harness.events if event[0] == "ensure_comments"]
    assert ensure_events == [
        ("ensure_comments", "ntn_stale", "API Access Stale"),
        ("ensure_comments", "ntn_existing", "API Access Private Page 6c07aebb"),
    ]
    issued = [event for event in harness.events if event[0] == "issued"]
    assert issued[-1][2] == "ntn_existing"
    assert not [event for event in harness.events if event[0] == "provision"]


@pytest.mark.asyncio
async def test_new_comment_token_stays_issued_until_comment_api_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _reuse_request(comments=True)
    events: list[tuple[object, ...]] = []

    async def record_event(name: str, *args: object) -> None:
        events.append((name, *args))

    async def no_existing(canonical_page_id: str) -> None:
        return None

    async def get_page_space_id(page_id: str) -> str:
        return "space-exact"

    async def provision(
        integration_name: str,
        target_space_id: str | None = None,
        include_comment_capabilities: bool = False,
    ) -> ProvisioningResult:
        assert include_comment_capabilities is True
        return ProvisioningResult(
            token="ntn_new",
            integration_name=integration_name,
            bot_id="bot-exact",
            space_id=target_space_id,
        )

    async def connect(*args: object, **kwargs: object) -> bool:
        events.append(
            (
                "connect",
                kwargs.get("bot_id"),
                kwargs.get("include_comment_capabilities"),
                kwargs.get("allow_name_fallback"),
            )
        )
        return True

    async def verify_comments(page_id: str, token: str) -> bool:
        events.append(("verify_comments", page_id, token))
        return False

    async def ensure_comments(token: str, integration_name: str) -> ProvisioningResult:
        events.append(("ensure_comments", token, integration_name))
        return ProvisioningResult(
            token=token,
            integration_name=integration_name,
            bot_id="bot-exact",
            space_id="space-exact",
        )

    async def fail_page_verification(*args: object, **kwargs: object) -> bool:
        pytest.fail("comment requests must use the comments API for final verification")

    monkeypatch.setattr(
        request_processor,
        "get_config",
        lambda: SimpleNamespace(notion_integration_name_prefix="API Access"),
    )
    monkeypatch.setattr(
        request_processor,
        "mark_request_processing",
        lambda request_id: record_event("processing", request_id),
    )
    monkeypatch.setattr(
        request_processor,
        "notify_requested",
        lambda request_id: record_event("requested", request_id),
    )
    monkeypatch.setattr(request_processor, "get_existing_token_for_page", no_existing)
    monkeypatch.setattr(notion_internal_api, "get_page_space_id", get_page_space_id)
    monkeypatch.setattr(request_processor, "provision_token_for_page", provision)
    monkeypatch.setattr(
        request_processor,
        "mark_request_issued",
        lambda *args: record_event("issued", *args),
    )
    monkeypatch.setattr(request_processor, "connect_integration_to_page", connect)
    monkeypatch.setattr(
        request_processor,
        "ensure_existing_token_comment_capabilities",
        ensure_comments,
    )
    monkeypatch.setattr(request_processor, "verify_comment_access", verify_comments)
    monkeypatch.setattr(request_processor, "verify_page_access", fail_page_verification)
    monkeypatch.setattr(
        request_processor,
        "mark_request_connected",
        lambda request_id: record_event("connected", request_id),
    )
    monkeypatch.setattr(
        request_processor,
        "_notify_and_complete",
        lambda request_id: record_event("complete", request_id),
    )

    await request_processor.process_one_request(record)

    event_names = [event[0] for event in events]
    assert "issued" in event_names
    assert ("connect", "bot-exact", True, False) in events
    assert ("verify_comments", _PAGE_ID, "ntn_new") in events
    assert "connected" not in event_names
    assert "complete" not in event_names


@pytest.mark.asyncio
async def test_retry_issued_comment_request_revalidates_exact_bot_and_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _existing_request()
    record.status = "Issued"
    record.comment_permission_requested = True
    record.connection_status = None
    events: list[tuple[object, ...]] = []

    async def get_issued_requests(limit: int = 10) -> list[RequestRecord]:
        return [record]

    async def ensure_comments(token: str, integration_name: str) -> ProvisioningResult:
        events.append(("ensure_comments", token, integration_name))
        return ProvisioningResult(
            token=token,
            integration_name=integration_name,
            bot_id="bot-exact",
            space_id="space-exact",
        )

    async def connect(
        page_url: str,
        integration_name: str,
        bot_id: str | None = None,
        space_id: str | None = None,
        include_comment_capabilities: bool = False,
        allow_name_fallback: bool = True,
    ) -> bool:
        events.append(
            (
                "connect",
                bot_id,
                space_id,
                include_comment_capabilities,
                allow_name_fallback,
            )
        )
        return True

    async def verify_comments(page_id: str, token: str) -> bool:
        events.append(("verify_comments", page_id, token))
        return True

    async def record_event(name: str, *args: object) -> None:
        events.append((name, *args))

    async def fail_page_verification(*args: object, **kwargs: object) -> bool:
        pytest.fail("comment retries must use the comments API for final verification")

    monkeypatch.setattr(request_processor, "get_issued_requests", get_issued_requests)
    monkeypatch.setattr(
        request_processor,
        "ensure_existing_token_comment_capabilities",
        ensure_comments,
    )
    monkeypatch.setattr(request_processor, "connect_integration_to_page", connect)
    monkeypatch.setattr(request_processor, "verify_comment_access", verify_comments)
    monkeypatch.setattr(request_processor, "verify_page_access", fail_page_verification)
    monkeypatch.setattr(
        request_processor,
        "mark_request_connected",
        lambda request_id: record_event("connected", request_id),
    )
    monkeypatch.setattr(
        request_processor,
        "_notify_and_complete",
        lambda request_id: record_event("complete", request_id),
    )

    retried = await request_processor.retry_issued_requests()

    assert retried == 1
    assert (
        "connect",
        "bot-exact",
        "space-exact",
        True,
        False,
    ) in events
    event_names = [event[0] for event in events]
    assert event_names == [
        "ensure_comments",
        "connect",
        "verify_comments",
        "connected",
        "complete",
    ]


@pytest.mark.asyncio
async def test_retry_issued_comment_permission_denied_preserves_requester_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _existing_request()
    record.status = "Issued"
    record.comment_permission_requested = True
    record.connection_status = None
    events: list[tuple[object, ...]] = []
    message = "페이지 관리자 권한 없음: 자동 연결할 수 없음"

    async def get_issued_requests(limit: int = 10) -> list[RequestRecord]:
        return [record]

    async def ensure_comments(token: str, integration_name: str) -> ProvisioningResult:
        events.append(("ensure_comments", token, integration_name))
        return ProvisioningResult(
            token=token,
            integration_name=integration_name,
            bot_id="bot-exact",
            space_id="space-exact",
        )

    async def deny_connection(*args: object, **kwargs: object) -> bool:
        events.append(("connect", kwargs.get("bot_id")))
        raise RuntimeError(message)

    async def mark_failed(
        request_id: str,
        error_message: str,
        retry_count: int = 0,
    ) -> None:
        events.append(("failed", request_id, error_message, retry_count))

    async def notify_failure(
        request_id: str,
        error_message: str,
        integration_name: str | None = None,
    ) -> None:
        events.append(("notify_failure", request_id, error_message, integration_name))

    monkeypatch.setattr(request_processor, "get_issued_requests", get_issued_requests)
    monkeypatch.setattr(
        request_processor,
        "ensure_existing_token_comment_capabilities",
        ensure_comments,
    )
    monkeypatch.setattr(request_processor, "connect_integration_to_page", deny_connection)
    monkeypatch.setattr(request_processor, "mark_request_failed", mark_failed)
    monkeypatch.setattr(request_processor, "notify_failure", notify_failure)

    retried = await request_processor.retry_issued_requests()

    assert retried == 0
    assert ("failed", "request-existing", message, 0) in events
    assert (
        "notify_failure",
        "request-existing",
        message,
        "API Access Private Page 6c07aebb",
    ) in events
