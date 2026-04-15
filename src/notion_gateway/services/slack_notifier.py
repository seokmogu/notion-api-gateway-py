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


ADMIN_CONTACT = "seokmogu@worxphere.ai"


def classify_user_error(error: str, integration_name: str | None = None) -> str:
    """Translate a raw exception message into a user-facing Korean explanation.

    Returns an actionable sentence the requester can act on. Falls back to the
    original message when no pattern matches so nothing is lost.
    """
    lower = error.lower()
    integ = integration_name or "(생성된 통합)"

    if "관리자 권한 없음" in error or "non-admin" in lower or "lacks admin" in lower:
        return (
            "이 페이지는 개인 페이지이거나 현재 요청자가 페이지 관리자가 아니라 "
            "자동 연결이 불가합니다. 페이지 소유자가 페이지 우측 상단 "
            "‘...’ → ‘연결’ 메뉴에서 통합 "
            f"`{integ}` 을(를) 직접 추가해 주세요."
        )
    if "does not have edit access" in lower:
        return (
            "요청자에게 해당 페이지의 편집 권한이 없습니다. "
            "페이지 소유자에게 ‘편집 가능’ 권한을 받은 뒤 다시 요청해 주세요."
        )
    if "different workspace" in lower or "cannot add bot permission" in lower:
        return (
            "페이지가 다른 워크스페이스에 있어 자동 연결이 불가합니다. "
            f"관리자({ADMIN_CONTACT})에게 문의해 주세요."
        )
    if "session expired" in lower or "unauthorized" in lower:
        return "시스템 점검 중입니다. 잠시 후 자동으로 재처리됩니다."
    if "aws_region" in lower or "aws_default_region" in lower:
        return "일시적 시스템 설정 오류입니다. 관리자에게 자동 전달되었습니다."
    if "token input was not found" in lower or "could not retrieve integration token" in lower:
        return (
            "Notion 페이지 구조 변경으로 인한 일시적인 오류입니다. "
            "관리자에게 자동 전달되었습니다."
        )
    return error


def format_token_failed_message(
    title: str,
    error: str,
    page_url: str,
    integration_name: str | None = None,
) -> str:
    user_msg = classify_user_error(error, integration_name)
    return (
        f":warning: *Notion API 토큰 발급 실패*\n\n"
        f"*조직명:* {title}\n"
        f"*사유:* {user_msg}\n"
        f"*페이지:* {page_url}\n\n"
        f"해결되지 않는 경우 관리자({ADMIN_CONTACT})에게 문의해 주세요."
    )
