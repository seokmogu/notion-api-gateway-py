"""Slack API integration for DM notifications."""

from __future__ import annotations

import logging

import httpx

from notion_gateway.config import get_config

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"

# Domain aliases for email lookup fallback
DOMAIN_ALIASES: dict[str, str] = {
    "worxphere.ai": "jobkorea.co.kr",
    "jobkorea.co.kr": "worxphere.ai",
}


def is_slack_configured() -> bool:
    return get_config().slack_bot_token is not None


async def _slack_api(method: str, body: dict[str, str | bool]) -> dict:
    """Call a Slack Web API method."""
    cfg = get_config()
    if not cfg.slack_bot_token:
        raise RuntimeError("SLACK_BOT_TOKEN is not configured")

    async with httpx.AsyncClient(timeout=15.0, verify=not cfg.no_ssl_verify) as client:
        response = await client.post(
            f"{SLACK_API_BASE}/{method}",
            headers={"Authorization": f"Bearer {cfg.slack_bot_token}"},
            data=body,
        )
        data = response.json()
        if not data.get("ok"):
            error = data.get("error", "unknown_error")
            logger.error("Slack API %s failed: %s", method, error)
            raise RuntimeError(f"Slack API error: {error}")
        return data


async def _lookup_by_email(email: str) -> str | None:
    """Look up a Slack user ID by email."""
    try:
        data = await _slack_api("users.lookupByEmail", {"email": email})
        return data.get("user", {}).get("id")
    except RuntimeError:
        return None


async def lookup_slack_user_by_email(email: str) -> str | None:
    """Look up a Slack user by email, trying domain aliases if needed."""
    user_id = await _lookup_by_email(email)
    if user_id:
        return user_id

    # Try domain alias
    local, _, domain = email.partition("@")
    alias_domain = DOMAIN_ALIASES.get(domain)
    if alias_domain:
        alias_email = f"{local}@{alias_domain}"
        logger.debug("Trying alias email: %s", alias_email)
        user_id = await _lookup_by_email(alias_email)
        if user_id:
            return user_id

    logger.warning("Slack user not found for email: %s", email)
    return None


async def send_slack_dm(email: str, message: str) -> bool:
    """Send a Slack DM to a user identified by email."""
    if not is_slack_configured():
        logger.debug("Slack not configured, skipping DM")
        return False

    user_id = await lookup_slack_user_by_email(email)
    if not user_id:
        return False

    try:
        await _slack_api(
            "chat.postMessage",
            {"channel": user_id, "text": message, "unfurl_links": False},
        )
        logger.info("Slack DM sent to %s", email)
        return True
    except RuntimeError as e:
        logger.error("Failed to send Slack DM to %s: %s", email, e)
        return False


def format_token_issued_message(title: str, token: str, page_url: str) -> str:
    return (
        f":white_check_mark: *Notion API 토큰 발급 완료*\n\n"
        f"*조직명:* {title}\n"
        f"*토큰:* `{token}`\n"
        f"*페이지:* {page_url}\n\n"
        f"해당 페이지에 대한 API 접근 권한이 부여되었습니다."
    )


def format_token_failed_message(title: str, error: str, page_url: str) -> str:
    return (
        f":warning: *Notion API 토큰 발급 실패*\n\n"
        f"*조직명:* {title}\n"
        f"*오류:* {error}\n"
        f"*페이지:* {page_url}\n\n"
        f"관리자가 확인 후 다시 처리할 예정입니다."
    )
