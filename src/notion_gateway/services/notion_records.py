"""Notion database record parsing and status management."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from notion_gateway.config import get_config
from notion_gateway.services.notion_api import query_database, update_page_properties
from notion_gateway.types import RequestRecord

logger = logging.getLogger(__name__)

# Status constants
STATUS_REQUESTED = "Requested"
STATUS_PROCESSING = "Processing"
STATUS_ISSUED = "Issued"
STATUS_COMPLETED = "완료"
STATUS_FAILED = "Failed"
STATUS_ACTIVE = "Active"

MAX_RETRY_COUNT = 10

# Korean property names from the Notion form database
PROP_ORGANIZATION = "조직명"
PROP_PAGE_URL = "신청 페이지 링크"
PROP_CANONICAL_PAGE_ID = "정규 페이지 ID"
PROP_REQUESTER = "신청자"
PROP_STATUS = "상태"
PROP_TOKEN = "발급 토큰키"
PROP_INTEGRATION_NAME = "통합 이름"
PROP_ERROR = "처리 오류"
PROP_REQUEST_DATE = "신청일자"
PROP_COMPLETION_DATE = "처리 완료일시"
PROP_CONNECTION_STATUS = "연결 여부"
PROP_RETRY_COUNT = "재시도 횟수"


def _text_from_property(prop: dict[str, Any] | None) -> str | None:
    """Extract plain text from a Notion property value."""
    if not prop:
        return None

    prop_type = prop.get("type", "")

    if prop_type == "title":
        items = prop.get("title", [])
        return "".join(item.get("plain_text", "") for item in items) or None

    if prop_type == "rich_text":
        items = prop.get("rich_text", [])
        return "".join(item.get("plain_text", "") for item in items) or None

    if prop_type == "url":
        return prop.get("url")

    if prop_type in ("select", "status"):
        option = prop.get(prop_type)
        return option.get("name") if option else None

    if prop_type == "number":
        val = prop.get("number")
        return str(val) if val is not None else None

    return None


def _people_from_property(prop: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Extract (user_id, email) from a People property."""
    if not prop or prop.get("type") != "people":
        return None, None
    people = prop.get("people", [])
    if not people:
        return None, None
    person = people[0]
    user_id = person.get("id")
    email = person.get("person", {}).get("email")
    return user_id, email


def parse_request_record(page: dict[str, Any]) -> RequestRecord:
    """Parse a Notion page into a RequestRecord."""
    props = page.get("properties", {})
    requester_id, requester_email = _people_from_property(props.get(PROP_REQUESTER))
    retry_str = _text_from_property(props.get(PROP_RETRY_COUNT))

    return RequestRecord(
        id=page["id"],
        organization=_text_from_property(props.get(PROP_ORGANIZATION)) or "",
        page_url=_text_from_property(props.get(PROP_PAGE_URL)),
        canonical_page_id=_text_from_property(props.get(PROP_CANONICAL_PAGE_ID)),
        requester_id=requester_id,
        requester_email=requester_email,
        status=_text_from_property(props.get(PROP_STATUS)) or "",
        token=_text_from_property(props.get(PROP_TOKEN)),
        integration_name=_text_from_property(props.get(PROP_INTEGRATION_NAME)),
        connection_status=_text_from_property(props.get(PROP_CONNECTION_STATUS)),
        retry_count=int(retry_str) if retry_str and retry_str.isdigit() else 0,
        error_message=_text_from_property(props.get(PROP_ERROR)),
        raw=page,
    )


async def get_pending_requests(limit: int = 10) -> list[RequestRecord]:
    """Get requests with status 'Requested' or 'Failed'."""
    cfg = get_config()
    result = await query_database(
        cfg.notion_requests_database_id,
        {
            "filter": {
                "or": [
                    {"property": PROP_STATUS, "select": {"equals": STATUS_REQUESTED}},
                    {"property": PROP_STATUS, "select": {"equals": STATUS_FAILED}},
                ]
            },
            "sorts": [{"property": PROP_REQUEST_DATE, "direction": "ascending"}],
            "page_size": limit,
        },
    )
    return [parse_request_record(page) for page in result.get("results", [])]


async def get_issued_requests(limit: int = 10) -> list[RequestRecord]:
    """Get requests with status 'Issued' (possibly unconnected)."""
    cfg = get_config()
    result = await query_database(
        cfg.notion_requests_database_id,
        {
            "filter": {"property": PROP_STATUS, "select": {"equals": STATUS_ISSUED}},
            "sorts": [{"property": PROP_REQUEST_DATE, "direction": "ascending"}],
            "page_size": limit,
        },
    )
    return [parse_request_record(page) for page in result.get("results", [])]


async def get_existing_token_for_page(canonical_page_id: str) -> RequestRecord | None:
    """Check if a token already exists for this page."""
    cfg = get_config()
    result = await query_database(
        cfg.notion_requests_database_id,
        {
            "filter": {
                "and": [
                    {
                        "property": PROP_CANONICAL_PAGE_ID,
                        "rich_text": {"equals": canonical_page_id},
                    },
                    {"property": PROP_STATUS, "select": {"equals": STATUS_COMPLETED}},
                ]
            },
            "page_size": 1,
        },
    )
    results = result.get("results", [])
    return parse_request_record(results[0]) if results else None


def _rich_text(text: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def _status(name: str) -> dict[str, Any]:
    return {"select": {"name": name}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def mark_request_processing(request_id: str) -> None:
    await update_page_properties(request_id, {PROP_STATUS: _status(STATUS_PROCESSING)})


async def mark_request_failed(request_id: str, message: str, retry_count: int = 0) -> None:
    new_count = retry_count + 1
    status = STATUS_FAILED
    await update_page_properties(
        request_id,
        {
            PROP_STATUS: _status(status),
            PROP_ERROR: _rich_text(message[:2000]),
            PROP_RETRY_COUNT: _rich_text(str(new_count)),
        },
    )


async def mark_request_issued(
    request_id: str,
    token: str,
    integration_name: str,
    canonical_page_id: str,
) -> None:
    await update_page_properties(
        request_id,
        {
            PROP_STATUS: _status(STATUS_ISSUED),
            PROP_TOKEN: _rich_text(token),
            PROP_INTEGRATION_NAME: _rich_text(integration_name),
            PROP_CANONICAL_PAGE_ID: _rich_text(canonical_page_id),
            PROP_COMPLETION_DATE: {"date": {"start": _now_iso()}},
        },
    )


async def mark_request_connected(request_id: str) -> None:
    await update_page_properties(
        request_id,
        {PROP_CONNECTION_STATUS: _rich_text("Yes")},
    )


async def cleanup_max_retries() -> int:
    """Find 'Max Retries' records and change them to 'Failed'."""
    cfg = get_config()
    result = await query_database(
        cfg.notion_requests_database_id,
        {"filter": {"property": PROP_STATUS, "select": {"equals": "Max Retries"}}},
    )
    pages = result.get("results", [])
    for page in pages:
        page_id = page["id"]
        await update_page_properties(page_id, {PROP_STATUS: _status(STATUS_FAILED)})
        logger.info("Cleaned up Max Retries -> Failed: %s", page_id)
    return len(pages)


async def mark_request_completed(request_id: str) -> None:
    await update_page_properties(
        request_id,
        {PROP_STATUS: _status(STATUS_COMPLETED)},
    )


async def get_completed_without_connection(limit: int = 50) -> list[RequestRecord]:
    """Get completed records where connection status is not 'Yes'."""
    cfg = get_config()
    result = await query_database(
        cfg.notion_requests_database_id,
        {
            "filter": {
                "and": [
                    {"property": PROP_STATUS, "select": {"equals": STATUS_COMPLETED}},
                    {"property": PROP_CONNECTION_STATUS, "rich_text": {"does_not_equal": "Yes"}},
                ]
            },
            "sorts": [{"property": PROP_REQUEST_DATE, "direction": "ascending"}],
            "page_size": limit,
        },
    )
    return [parse_request_record(page) for page in result.get("results", [])]
