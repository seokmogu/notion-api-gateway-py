"""Notification service: Notion comments + Slack DMs."""

from __future__ import annotations

import logging

from notion_gateway.services.notion_api import create_comment, retrieve_page
from notion_gateway.services.notion_records import (
    PROP_ORGANIZATION,
    PROP_PAGE_URL,
    PROP_REQUESTER,
    PROP_TOKEN,
)
from notion_gateway.services.slack_notifier import (
    format_token_failed_message,
    format_token_issued_message,
    is_slack_configured,
    send_slack_dm,
)
from notion_gateway.types import NotionApiError

logger = logging.getLogger(__name__)

ADMIN_EMAIL = "seokmogu@worxphere.ai"


def _text_from_prop(props: dict, name: str) -> str:
    """Extract text from properties dict."""
    prop = props.get(name, {})
    prop_type = prop.get("type", "")
    if prop_type == "title":
        return "".join(i.get("plain_text", "") for i in prop.get("title", []))
    if prop_type == "rich_text":
        return "".join(i.get("plain_text", "") for i in prop.get("rich_text", []))
    if prop_type == "url":
        return prop.get("url") or ""
    return ""


def _requester_info(props: dict) -> tuple[str | None, str | None]:
    """Get (user_id, email) from requester property."""
    prop = props.get(PROP_REQUESTER, {})
    people = prop.get("people", [])
    if not people:
        return None, None
    person = people[0]
    return person.get("id"), person.get("person", {}).get("email")


async def notify_requester(request_page_id: str) -> None:
    """Send notifications after successful token issuance."""
    try:
        page = await retrieve_page(request_page_id)
        props = page.get("properties", {})

        title = _text_from_prop(props, PROP_ORGANIZATION)
        token = _text_from_prop(props, PROP_TOKEN)
        page_url = _text_from_prop(props, PROP_PAGE_URL) or ""
        requester_id, requester_email = _requester_info(props)

        # Post Notion comment with @mention
        if requester_id:
            rich_text = [
                {"type": "mention", "mention": {"user": {"id": requester_id}}},
                {
                    "type": "text",
                    "text": {"content": " API 토큰이 발급되었습니다. 페이지의 '발급 토큰키' 항목을 확인해 주세요."},
                },
            ]
            try:
                await create_comment(request_page_id, rich_text)
                logger.info("Notion comment posted for request %s", request_page_id)
            except NotionApiError as e:
                logger.warning("Failed to post Notion comment: %s", e)

        # Send Slack DM
        if requester_email and is_slack_configured():
            message = format_token_issued_message(title, token, page_url)
            await send_slack_dm(requester_email, message)

    except Exception as e:
        logger.error("Failed to send notifications for %s: %s", request_page_id, e)


async def notify_failure(request_page_id: str, error_message: str) -> None:
    """Send failure notifications."""
    try:
        page = await retrieve_page(request_page_id)
        props = page.get("properties", {})

        title = _text_from_prop(props, PROP_ORGANIZATION)
        page_url = _text_from_prop(props, PROP_PAGE_URL) or ""
        requester_id, requester_email = _requester_info(props)

        # Post Notion comment
        if requester_id:
            truncated_error = error_message[:200]
            rich_text = [
                {"type": "mention", "mention": {"user": {"id": requester_id}}},
                {
                    "type": "text",
                    "text": {"content": f" 토큰 발급에 실패했습니다: {truncated_error}"},
                },
            ]
            try:
                await create_comment(request_page_id, rich_text)
            except NotionApiError as e:
                logger.warning("Failed to post failure comment: %s", e)

        # Slack DM to requester
        if requester_email and is_slack_configured():
            message = format_token_failed_message(title, error_message, page_url)
            await send_slack_dm(requester_email, message)

        # Admin notification
        if is_slack_configured():
            admin_msg = (
                f":warning: *Token request error*\n"
                f"*Request:* {title} ({request_page_id})\n"
                f"*Error:* {error_message[:500]}"
            )
            await send_slack_dm(ADMIN_EMAIL, admin_msg)

    except Exception as e:
        logger.error("Failed to send failure notifications for %s: %s", request_page_id, e)
