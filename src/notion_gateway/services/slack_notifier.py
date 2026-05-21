"""Slack DM notifications via slack-native-toolkit."""

from __future__ import annotations

import logging

import httpx
from slack_native_toolkit import AsyncSlackClient, SlackError

from notion_gateway.config import get_config

logger = logging.getLogger(__name__)

ADMIN_CONTACT = "seokmogu@worxphere.ai"

# Domain aliases for email lookup fallback when the primary lookup fails.
DOMAIN_ALIASES: dict[str, str] = {
    "worxphere.ai": "jobkorea.co.kr",
    "jobkorea.co.kr": "worxphere.ai",
}


def is_slack_configured() -> bool:
    return get_config().slack_bot_token is not None


def _make_client() -> AsyncSlackClient:
    cfg = get_config()
    if not cfg.slack_bot_token:
        raise RuntimeError("SLACK_BOT_TOKEN is not configured")
    http = httpx.AsyncClient(
        base_url="https://slack.com/api",
        headers={"Authorization": f"Bearer {cfg.slack_bot_token}"},
        timeout=15.0,
        verify=not cfg.no_ssl_verify,
    )
    return AsyncSlackClient(cfg.slack_bot_token, client=http)


async def lookup_slack_user_by_email(email: str) -> str | None:
    """Return the Slack user id for an email, trying alias domains on miss."""
    async with _make_client() as cli:
        try:
            primary = await cli.users_lookup_by_email(email)
        except SlackError as exc:
            logger.error("users.lookupByEmail %s failed: %s", email, exc.code or exc)
            return None
        if primary and primary.get("user", {}).get("id"):
            return primary["user"]["id"]

        local, _, domain = email.partition("@")
        alias_domain = DOMAIN_ALIASES.get(domain)
        if not alias_domain:
            logger.warning("Slack user not found for email: %s", email)
            return None

        alias_email = f"{local}@{alias_domain}"
        logger.debug("Trying alias email: %s", alias_email)
        try:
            alias = await cli.users_lookup_by_email(alias_email)
        except SlackError as exc:
            logger.error("users.lookupByEmail %s failed: %s", alias_email, exc.code or exc)
            return None
        if alias and alias.get("user", {}).get("id"):
            return alias["user"]["id"]

        logger.warning("Slack user not found for email: %s", email)
        return None


async def send_slack_dm(email: str, message: str) -> bool:
    """Send a Slack DM to the user identified by `email`. Returns True on success."""
    if not is_slack_configured():
        logger.debug("Slack not configured, skipping DM")
        return False

    user_id = await lookup_slack_user_by_email(email)
    if not user_id:
        return False

    try:
        async with _make_client() as cli:
            await cli.post_message(user_id, text=message, unfurl_links=False)
    except SlackError as exc:
        logger.error("Failed to send Slack DM to %s: %s", email, exc.code or exc)
        return False

    logger.info("Slack DM sent to %s", email)
    return True


# --- message formatters -------------------------------------------------------


def format_token_issued_message(title: str, token: str, page_url: str) -> str:
    return (
        f":white_check_mark: *Notion API 토큰 발급 완료*\n\n"
        f"*조직명:* {title}\n"
        f"*토큰:* `{token}`\n"
        f"*페이지:* {page_url}\n\n"
        f"해당 페이지에 대한 API 접근 권한이 부여되었습니다."
    )


def classify_user_error(error: str, integration_name: str | None = None) -> str:
    """Translate a raw exception message into a user-facing Korean explanation."""
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
