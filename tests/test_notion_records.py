"""Tests for Notion record parsing."""

from __future__ import annotations

from notion_gateway.services.notion_records import (
    PROP_AUTOMATION_PERMISSION_CONFIRMED,
    PROP_COMMENT_PERMISSION_REQUESTED,
    PROP_ERROR,
    PROP_RETRY_COUNT,
    _checkbox_from_property,
    _text_from_property,
    get_pending_requests,
    mark_request_issued,
    parse_request_record,
)


class TestTextFromProperty:
    def test_title(self) -> None:
        prop = {"type": "title", "title": [{"plain_text": "Hello"}]}
        assert _text_from_property(prop) == "Hello"

    def test_rich_text(self) -> None:
        prop = {"type": "rich_text", "rich_text": [{"plain_text": "World"}]}
        assert _text_from_property(prop) == "World"

    def test_rich_text_multiple_segments(self) -> None:
        prop = {
            "type": "rich_text",
            "rich_text": [{"plain_text": "Hello "}, {"plain_text": "World"}],
        }
        assert _text_from_property(prop) == "Hello World"

    def test_url(self) -> None:
        prop = {"type": "url", "url": "https://example.com"}
        assert _text_from_property(prop) == "https://example.com"

    def test_status(self) -> None:
        prop = {"type": "status", "status": {"name": "Requested"}}
        assert _text_from_property(prop) == "Requested"

    def test_select(self) -> None:
        prop = {"type": "select", "select": {"name": "Option A"}}
        assert _text_from_property(prop) == "Option A"

    def test_none(self) -> None:
        assert _text_from_property(None) is None

    def test_empty_rich_text(self) -> None:
        prop = {"type": "rich_text", "rich_text": []}
        assert _text_from_property(prop) is None

    def test_number(self) -> None:
        prop = {"type": "number", "number": 42}
        assert _text_from_property(prop) == "42"


class TestCheckboxFromProperty:
    def test_checked(self) -> None:
        prop = {"type": "checkbox", "checkbox": True}
        assert _checkbox_from_property(prop) is True

    def test_unchecked(self) -> None:
        prop = {"type": "checkbox", "checkbox": False}
        assert _checkbox_from_property(prop) is False

    def test_missing(self) -> None:
        assert _checkbox_from_property(None) is None


class TestParseRequestRecord:
    def test_full_record(self) -> None:
        page = {
            "id": "page-123",
            "properties": {
                "조직명": {"type": "title", "title": [{"plain_text": "Test Org"}]},
                "신청 페이지 링크": {
                    "type": "url",
                    "url": "https://www.notion.so/test-page-abc123",
                },
                "정규 페이지 ID": {"type": "rich_text", "rich_text": [{"plain_text": "abc-123"}]},
                "신청자": {
                    "type": "people",
                    "people": [{"id": "user-1", "person": {"email": "test@example.com"}}],
                },
                "상태": {"type": "status", "status": {"name": "Requested"}},
                "발급 토큰키": {"type": "rich_text", "rich_text": []},
                "통합 이름": {"type": "rich_text", "rich_text": []},
                "연결 여부": {"type": "rich_text", "rich_text": []},
                "자동화 계정 권한 확인": {"type": "checkbox", "checkbox": True},
                PROP_COMMENT_PERMISSION_REQUESTED: {"type": "checkbox", "checkbox": True},
                "재시도 횟수": {"type": "rich_text", "rich_text": [{"plain_text": "2"}]},
                "처리 오류": {"type": "rich_text", "rich_text": []},
            },
        }
        record = parse_request_record(page)
        assert record.id == "page-123"
        assert record.organization == "Test Org"
        assert record.page_url == "https://www.notion.so/test-page-abc123"
        assert record.canonical_page_id == "abc-123"
        assert record.requester_id == "user-1"
        assert record.requester_email == "test@example.com"
        assert record.status == "Requested"
        assert record.automation_permission_confirmed is True
        assert record.comment_permission_requested is True
        assert record.retry_count == 2

    def test_minimal_record(self) -> None:
        page = {"id": "page-456", "properties": {}}
        record = parse_request_record(page)
        assert record.id == "page-456"
        assert record.organization == ""
        assert record.page_url is None
        assert record.requester_id is None
        assert record.automation_permission_confirmed is None
        assert record.comment_permission_requested is None
        assert record.retry_count == 0


class TestMarkRequestIssued:
    async def test_clears_stale_error_and_retry_count(self, monkeypatch) -> None:
        captured: dict = {}

        async def fake_update_page_properties(page_id: str, properties: dict) -> dict:
            captured["page_id"] = page_id
            captured["properties"] = properties
            return {}

        monkeypatch.setattr(
            "notion_gateway.services.notion_records.update_page_properties",
            fake_update_page_properties,
        )

        await mark_request_issued("request-1", "ntn_test", "API Access Test", "page-1")

        props = captured["properties"]
        assert captured["page_id"] == "request-1"
        assert props[PROP_ERROR] == {"rich_text": []}
        assert props[PROP_RETRY_COUNT]["rich_text"][0]["text"]["content"] == "0"


class TestGetPendingRequests:
    async def test_skips_unconfirmed_permission_checkbox(self, monkeypatch) -> None:
        pages = [
            {
                "id": "unchecked",
                "properties": {
                    "상태": {"type": "select", "select": {"name": "Requested"}},
                    PROP_AUTOMATION_PERMISSION_CONFIRMED: {
                        "type": "checkbox",
                        "checkbox": False,
                    },
                    "재시도 횟수": {"type": "rich_text", "rich_text": [{"plain_text": "0"}]},
                },
            },
            {
                "id": "checked",
                "properties": {
                    "상태": {"type": "select", "select": {"name": "Requested"}},
                    PROP_AUTOMATION_PERMISSION_CONFIRMED: {
                        "type": "checkbox",
                        "checkbox": True,
                    },
                    "재시도 횟수": {"type": "rich_text", "rich_text": [{"plain_text": "0"}]},
                },
            },
        ]

        class FakeConfig:
            notion_requests_database_id = "db-123"

        async def fake_query_database(database_id: str, body: dict) -> dict:
            assert database_id == "db-123"
            return {"results": pages, "has_more": False}

        monkeypatch.setattr("notion_gateway.services.notion_records.get_config", FakeConfig)
        monkeypatch.setattr(
            "notion_gateway.services.notion_records.query_database", fake_query_database
        )

        records = await get_pending_requests(limit=10)

        assert [r.id for r in records] == ["checked"]

    async def test_treats_missing_checkbox_as_backward_compatible(self, monkeypatch) -> None:
        pages = [
            {
                "id": "legacy",
                "properties": {
                    "상태": {"type": "select", "select": {"name": "Requested"}},
                    "재시도 횟수": {"type": "rich_text", "rich_text": [{"plain_text": "0"}]},
                },
            }
        ]

        class FakeConfig:
            notion_requests_database_id = "db-123"

        async def fake_query_database(database_id: str, body: dict) -> dict:
            return {"results": pages, "has_more": False}

        monkeypatch.setattr("notion_gateway.services.notion_records.get_config", FakeConfig)
        monkeypatch.setattr(
            "notion_gateway.services.notion_records.query_database", fake_query_database
        )

        records = await get_pending_requests(limit=10)

        assert [r.id for r in records] == ["legacy"]
