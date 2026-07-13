"""Self-healing checks for the long-running gateway worker."""

from __future__ import annotations

import logging
import time
from typing import Any

from notion_gateway.config import AppConfig, get_config
from notion_gateway.services.notion_browser import (
    refresh_session,
    repair_saved_session_from_profile,
)
from notion_gateway.services.notion_internal_api import health_check
from notion_gateway.services.slack_notifier import send_slack_dm

logger = logging.getLogger(__name__)


def _is_ok(status: Any) -> bool:
    return str(status).startswith("ok")


def _is_healthy(results: dict[str, Any]) -> bool:
    required = ("session", "getSpaces", "listBots")
    return all(_is_ok(results.get(key, "")) for key in required)


def format_self_healing_alert_message(results: dict[str, Any], error: str | None = None) -> str:
    lines = [
        ":rotating_light: *Notion API Gateway 자동 복구 실패*",
        "",
        "토큰 발급 워커가 Notion 내부 API 세션 장애를 감지했고 자동 복구에 실패했습니다.",
        "맥미니에서 `notion-gateway auth`를 다시 실행해 브라우저 세션을 갱신해야 합니다.",
        "",
        "*진단 결과:*",
    ]
    for key in ("session", "getSpaces", "listBots"):
        if key in results:
            lines.append(f"- {key}: {results[key]}")
    if error:
        lines.extend(["", f"*복구 오류:* {error[:500]}"])
    return "\n".join(lines)


def format_self_healing_recovery_message(results: dict[str, Any]) -> str:
    lines = [
        ":white_check_mark: *Notion API Gateway 정상 복구*",
        "",
        "Notion 내부 API 상태가 정상으로 확인되어 토큰 발급 폴링을 재개했습니다.",
        "",
        "*진단 결과:*",
    ]
    for key in ("session", "getSpaces", "listBots"):
        if key in results:
            lines.append(f"- {key}: {results[key]}")
    return "\n".join(lines)


class SelfHealingAgent:
    """Attempts local repair before the poller mutates Notion request records."""

    def __init__(self, cfg: AppConfig | None = None) -> None:
        self.cfg = cfg or get_config()
        self._last_alert_at = 0.0
        self._consecutive_failures = 0
        self._alert_active = False

    async def ensure_internal_api_ready(self) -> bool:
        """Return True when internal API session is usable or was repaired."""
        if not self.cfg.self_healing_enabled:
            return True

        try:
            results = await health_check()
        except Exception as exc:
            results = {"health_check": f"fail: {exc}"}
        if _is_healthy(results):
            self._consecutive_failures = 0
            await self._notify_recovery(results)
            return True

        logger.warning("Internal API health check failed: %s", results)
        error: str | None = None
        for repair_name, repair in (
            ("refresh saved storage-state", refresh_session),
            ("rebuild storage-state from persistent profile", repair_saved_session_from_profile),
        ):
            try:
                logger.info("Self-healing: attempting to %s", repair_name)
                if await repair():
                    results = await health_check()
                    if _is_healthy(results):
                        logger.info("Self-healing repaired Notion internal API session")
                        self._consecutive_failures = 0
                        await self._notify_recovery(results)
                        return True
            except Exception as exc:
                error = f"{repair_name}: {exc}"
                logger.warning("Self-healing repair failed (%s): %s", repair_name, exc)

        try:
            results = await health_check()
        except Exception as exc:
            results = {"health_check": f"fail: {exc}"}
            error = str(exc)

        # Defer escalation: a single failed cycle is usually a transient glitch that
        # the next poll recovers (which resets the counter). Only page a human once
        # we have seen enough *consecutive* failures to call it a real outage.
        self._consecutive_failures += 1
        threshold = self.cfg.self_healing_alert_min_consecutive_failures
        if self._consecutive_failures < threshold:
            logger.warning(
                "Self-healing repair failed (%d/%d consecutive); deferring alert pending retry",
                self._consecutive_failures,
                threshold,
            )
            return False

        await self._alert(results, error)
        return False

    async def _alert(self, results: dict[str, Any], error: str | None = None) -> None:
        now = time.monotonic()
        if now - self._last_alert_at < self.cfg.self_healing_alert_cooldown_seconds:
            logger.info("Self-healing alert suppressed by cooldown")
            return
        self._last_alert_at = now

        sent = await send_slack_dm(
            self.cfg.self_healing_admin_email,
            format_self_healing_alert_message(results, error),
        )
        if sent:
            self._alert_active = True
            logger.info("Self-healing alert sent to %s", self.cfg.self_healing_admin_email)
        else:
            logger.warning("Self-healing alert could not be sent")

    async def _notify_recovery(self, results: dict[str, Any]) -> None:
        if not self._alert_active:
            return

        sent = await send_slack_dm(
            self.cfg.self_healing_admin_email,
            format_self_healing_recovery_message(results),
        )
        if sent:
            self._alert_active = False
            logger.info("Self-healing recovery sent to %s", self.cfg.self_healing_admin_email)
        else:
            logger.warning("Self-healing recovery could not be sent")
