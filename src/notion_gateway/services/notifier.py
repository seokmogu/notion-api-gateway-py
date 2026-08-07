"""Notification service: Slack DMs only."""

from __future__ import annotations

import logging
import os

from notion_gateway.services.notion_api import retrieve_page
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
    lookup_slack_user_by_email,
    send_slack_dm,
)

logger = logging.getLogger(__name__)

ADMIN_EMAIL = os.getenv("SELF_HEALING_ADMIN_EMAIL", "admin@example.com")


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


async def notify_requested(request_page_id: str) -> None:
    """Send Slack DM when a new request is received."""
    try:
        page = await retrieve_page(request_page_id)
        props = page.get("properties", {})

        title = _text_from_prop(props, PROP_ORGANIZATION)
        page_url = _text_from_prop(props, PROP_PAGE_URL) or ""
        _, requester_email = _requester_info(props)

        if requester_email and is_slack_configured():
            message = (
                f":inbox_tray: *Notion API 토큰 신청 접수*\n\n"
                f"*조직명:* {title}\n"
                f"*페이지:* {page_url}\n\n"
                f"요청이 접수되었습니다. 처리 완료 시 다시 안내드리겠습니다."
            )
            await send_slack_dm(requester_email, message)

    except Exception as e:
        logger.error("Failed to send request notification for %s: %s", request_page_id, e)


async def notify_requester(
    request_page_id: str,
    existing_requester_email: str | None = None,
) -> None:
    """Send Slack DM after successful token issuance."""
    try:
        page = await retrieve_page(request_page_id)
        props = page.get("properties", {})

        title = _text_from_prop(props, PROP_ORGANIZATION)
        token = _text_from_prop(props, PROP_TOKEN)
        page_url = _text_from_prop(props, PROP_PAGE_URL) or ""
        _, requester_email = _requester_info(props)

        if requester_email and is_slack_configured():
            existing_requester_mention = None
            if (
                existing_requester_email
                and existing_requester_email.casefold() != requester_email.casefold()
            ):
                try:
                    existing_requester_id = await lookup_slack_user_by_email(
                        existing_requester_email
                    )
                except Exception as exc:
                    logger.warning(
                        "Could not look up existing requester in Slack for reused token "
                        "request %s: %s",
                        request_page_id,
                        exc,
                    )
                    existing_requester_id = None
                if existing_requester_id:
                    existing_requester_mention = f"<@{existing_requester_id}>"
                else:
                    logger.warning(
                        "Could not resolve existing requester in Slack for reused token request %s",
                        request_page_id,
                    )
            message = format_token_issued_message(
                title,
                token,
                page_url,
                existing_requester_mention=existing_requester_mention,
            )
            await send_slack_dm(requester_email, message)

    except Exception as e:
        logger.error("Failed to send notifications for %s: %s", request_page_id, e)


async def notify_failure(
    request_page_id: str,
    error_message: str,
    integration_name: str | None = None,
) -> None:
    """Send failure notifications via Slack."""
    try:
        page = await retrieve_page(request_page_id)
        props = page.get("properties", {})

        title = _text_from_prop(props, PROP_ORGANIZATION)
        page_url = _text_from_prop(props, PROP_PAGE_URL) or ""
        _, requester_email = _requester_info(props)

        # Slack DM to requester
        if requester_email and is_slack_configured():
            message = format_token_failed_message(
                title, error_message, page_url, integration_name
            )
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
