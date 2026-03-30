"""Notion REST API client with retry logic."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from notion_gateway.config import get_config
from notion_gateway.types import NotionApiError

logger = logging.getLogger(__name__)

NOTION_BASE_URL = "https://api.notion.com/v1"


async def notion_fetch(
    path: str,
    token: str | None = None,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    query: dict[str, str] | None = None,
    max_retries: int = 3,
) -> tuple[Any, str | None]:
    """Make a Notion API request with automatic retry on transient errors.

    Returns (data, request_id) tuple.
    """
    cfg = get_config()
    actual_token = token or cfg.notion_token
    url = f"{NOTION_BASE_URL}/{path.lstrip('/')}"

    headers = {
        "Authorization": f"Bearer {actual_token}",
        "Notion-Version": cfg.notion_api_version,
        "Content-Type": "application/json",
    }

    retryable_statuses = {429, 502, 503, 504}

    verify: str | bool = True
    if cfg.no_ssl_verify:
        verify = False
    elif cfg.ssl_ca_file:
        verify = cfg.ssl_ca_file

    async with httpx.AsyncClient(timeout=30.0, verify=verify) as client:
        for attempt in range(max_retries):
            try:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    json=body if method != "GET" else None,
                    params=query,
                )
                request_id = response.headers.get("x-request-id")

                if response.status_code in retryable_statuses and attempt < max_retries - 1:
                    retry_after = float(response.headers.get("Retry-After", 1))
                    delay = max(retry_after, 1.0 * (2**attempt))
                    logger.warning(
                        "Notion API %s (attempt %d/%d), retrying in %.1fs",
                        response.status_code,
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if response.status_code >= 400:
                    data = response.json() if response.content else {}
                    raise NotionApiError(
                        message=data.get("message", f"HTTP {response.status_code}"),
                        status=response.status_code,
                        request_id=request_id,
                        details=data,
                    )

                data = response.json() if response.content else {}
                return data, request_id

            except httpx.HTTPError as exc:
                if attempt < max_retries - 1:
                    delay = 1.0 * (2**attempt)
                    logger.warning("HTTP error (attempt %d/%d): %s", attempt + 1, max_retries, exc)
                    await asyncio.sleep(delay)
                    continue
                raise NotionApiError(f"HTTP error after {max_retries} retries: {exc}") from exc

    raise NotionApiError("Exhausted retries")


async def query_database(
    database_id: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Query a Notion database."""
    data, _ = await notion_fetch(f"databases/{database_id}/query", method="POST", body=body or {})
    return data


async def retrieve_page(page_id: str, token: str | None = None) -> dict[str, Any]:
    """Retrieve a Notion page."""
    data, _ = await notion_fetch(f"pages/{page_id}", token=token)
    return data


async def update_page_properties(
    page_id: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    """Update properties on a Notion page."""
    data, _ = await notion_fetch(
        f"pages/{page_id}",
        method="PATCH",
        body={"properties": properties},
    )
    return data


async def create_comment(page_id: str, rich_text: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a comment on a Notion page."""
    data, _ = await notion_fetch(
        "comments",
        method="POST",
        body={"parent": {"page_id": page_id}, "rich_text": rich_text},
    )
    return data


async def verify_token(token: str) -> bool:
    """Verify a Notion token by calling users/me."""
    try:
        await notion_fetch("users/me", token=token)
        return True
    except NotionApiError as e:
        if e.status in (401, 403):
            return False
        raise


async def verify_page_access(page_id: str, token: str) -> bool:
    """Check if a token can access a specific page."""
    try:
        await retrieve_page(page_id, token=token)
        return True
    except NotionApiError as e:
        if e.status in (401, 403, 404):
            return False
        raise
