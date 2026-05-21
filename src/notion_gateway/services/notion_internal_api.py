"""Notion internal API client (v3) for integration management.

These endpoints use browser session cookies (token_v2) for authentication,
not the public Notion API token. They replicate browser UI actions via direct
API calls, eliminating fragile Playwright selectors.

API change detection: each call validates response shape and raises
NotionInternalApiError with a descriptive message when the expected
fields are missing, so callers know immediately when Notion changes
their internal API contract.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from notion_gateway.config import get_config

logger = logging.getLogger(__name__)

INTERNAL_API_BASE = "https://www.notion.so/api/v3"


class NotionInternalApiError(Exception):
    """Error from Notion internal API."""

    def __init__(self, message: str, endpoint: str = "", status: int = 0, body: Any = None):
        super().__init__(message)
        self.endpoint = endpoint
        self.status = status
        self.body = body


@dataclass
class CreatedBot:
    bot_id: str
    space_id: str


@dataclass
class BotInfo:
    bot_id: str
    name: str
    space_id: str
    integration_id: str
    alive: bool


def _load_session_headers() -> dict[str, str]:
    """Build request headers from saved browser session cookies."""
    cfg = get_config()
    storage_path = cfg.storage_state_path
    if not storage_path.exists():
        raise NotionInternalApiError(
            "No browser session found. Run 'notion-gateway auth' first.",
            endpoint="session",
        )
    state = json.loads(storage_path.read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in state.get("cookies", [])}
    token_v2 = cookies.get("token_v2", "")
    notion_user = cookies.get("notion_user_id", "")
    if not token_v2:
        raise NotionInternalApiError(
            "Browser session expired (no token_v2). Run 'notion-gateway auth'.",
            endpoint="session",
        )
    return {
        "Content-Type": "application/json",
        "Cookie": f"token_v2={token_v2}; notion_user_id={notion_user}",
        "x-notion-active-user-header": notion_user,
    }


def _validate_response(
    endpoint: str, data: Any, required_keys: list[str], status_code: int
) -> None:
    """Raise if response is missing expected keys — API contract change detection."""
    if status_code == 401:
        raise NotionInternalApiError(
            "Session expired or unauthorized. Run 'notion-gateway auth'.",
            endpoint=endpoint,
            status=status_code,
            body=data,
        )
    if status_code >= 400:
        msg = ""
        if isinstance(data, dict):
            msg = data.get("debugMessage", data.get("message", ""))
        raise NotionInternalApiError(
            f"Internal API error: {endpoint} returned {status_code}: {msg}",
            endpoint=endpoint,
            status=status_code,
            body=data,
        )
    if not isinstance(data, dict):
        raise NotionInternalApiError(
            f"API contract changed: {endpoint} returned non-dict response",
            endpoint=endpoint,
            body=data,
        )
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise NotionInternalApiError(
            f"API contract changed: {endpoint} missing keys {missing}. "
            f"Notion may have updated their internal API. "
            f"Got keys: {list(data.keys())}",
            endpoint=endpoint,
            body=data,
        )


async def _internal_post(endpoint: str, body: dict[str, Any]) -> tuple[Any, int]:
    """POST to Notion internal API with session auth."""
    cfg = get_config()
    headers = _load_session_headers()
    url = f"{INTERNAL_API_BASE}/{endpoint}"

    async with httpx.AsyncClient(timeout=30.0, verify=not cfg.no_ssl_verify) as client:
        response = await client.post(url, headers=headers, json=body)
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:500]}
        return data, response.status_code


async def create_integration(name: str, space_id: str) -> CreatedBot:
    """Create a new internal integration (bot).

    Equivalent to: browser form submit on /profile/integrations/form/new-integration
    API: POST /api/v3/createDeveloperIntegrationV2
    """
    endpoint = "createDeveloperIntegrationV2"
    data, status = await _internal_post(
        endpoint,
        {"type": "create-bot", "name": name, "spaceId": space_id},
    )
    _validate_response(endpoint, data, ["pointer"], status)
    pointer = data["pointer"]
    _validate_response(f"{endpoint}.pointer", pointer, ["id", "spaceId"], status)

    bot_id = pointer["id"]
    logger.info("Created integration '%s' (botId=%s)", name, bot_id)
    return CreatedBot(bot_id=bot_id, space_id=pointer["spaceId"])


async def get_bot_token(bot_id: str) -> str:
    """Retrieve the integration token for a bot.

    Equivalent to: clicking Show button on integration settings page
    API: POST /api/v3/getBotToken
    """
    endpoint = "getBotToken"
    data, status = await _internal_post(endpoint, {"botId": bot_id})
    _validate_response(endpoint, data, ["token"], status)

    token = data["token"]
    if not token.startswith(("ntn_", "secret_")):
        raise NotionInternalApiError(
            f"API contract changed: {endpoint} returned unexpected token format: {token[:10]}...",
            endpoint=endpoint,
        )
    logger.info("Retrieved token for bot %s", bot_id)
    return token


async def delete_bot(bot_id: str) -> None:
    """Delete an integration (bot).

    API: POST /api/v3/deleteBot
    """
    endpoint = "deleteBot"
    data, status = await _internal_post(endpoint, {"botId": bot_id})
    if status >= 400:
        _validate_response(endpoint, data, [], status)
    logger.info("Deleted bot %s", bot_id)


async def connect_bot_to_page(bot_id: str, page_id: str, space_id: str) -> None:
    """Grant a bot access to a specific page.

    Equivalent to: Actions > Connections > Add connection in the Notion UI.
    API: POST /api/v3/saveTransactionsFanout with setPermissionItem command.

    Args:
        bot_id: The integration bot ID (from create_integration)
        page_id: The canonical Notion page ID (UUID format)
        space_id: The workspace ID where the page lives
    """
    import time
    import uuid

    endpoint = "saveTransactionsFanout"
    now_ms = int(time.time() * 1000)
    body = {
        "requestId": str(uuid.uuid4()),
        "transactions": [
            {
                "id": str(uuid.uuid4()),
                "spaceId": space_id,
                "debug": {"userAction": "NotionGateway.connectBotToPage"},
                "operations": [
                    {
                        "pointer": {
                            "id": page_id,
                            "table": "block",
                            "spaceId": space_id,
                        },
                        "command": "setPermissionItem",
                        "path": ["permissions"],
                        "args": {
                            "type": "bot_permission",
                            "bot_id": bot_id,
                            "parent_id": space_id,
                            "parent_table": "space",
                            "role": {
                                "read_comment": True,
                                "read_content": True,
                                "insert_comment": True,
                                "insert_content": True,
                                "update_content": True,
                            },
                        },
                    },
                    {
                        "pointer": {
                            "id": page_id,
                            "table": "block",
                            "spaceId": space_id,
                        },
                        "path": [],
                        "command": "update",
                        "args": {
                            "last_edited_time": now_ms,
                        },
                    },
                ],
            }
        ],
        "unretryable_error_behavior": "continue",
    }

    data, status = await _internal_post(endpoint, body)
    if status >= 400:
        _validate_response(endpoint, data, [], status)
    logger.info("Connected bot %s to page %s", bot_id, page_id)


async def get_page_space_id(page_id: str) -> str | None:
    """Look up the space_id for a given page via getPublicPageData.

    API: POST /api/v3/getPublicPageData
    """
    endpoint = "getPublicPageData"
    data, status = await _internal_post(endpoint, {"type": "block-space", "blockId": page_id})
    if status >= 400:
        return None
    if not isinstance(data, dict):
        return None
    return data.get("spaceId")


async def get_available_spaces() -> list[str]:
    """Get space IDs where the user can create integrations.

    API: POST /api/v3/getSpacesWhereUserCanCreateIntegrations
    """
    endpoint = "getSpacesWhereUserCanCreateIntegrations"
    data, status = await _internal_post(endpoint, {})
    _validate_response(endpoint, data, ["spaceIds"], status)
    return data["spaceIds"]


async def list_bots() -> list[BotInfo]:
    """List all developer bots/integrations.

    API: POST /api/v3/getDeveloperBotsAndIntegrations
    """
    endpoint = "getDeveloperBotsAndIntegrations"
    data, status = await _internal_post(endpoint, {})
    _validate_response(endpoint, data, ["botIds", "recordMap"], status)

    record_map = data["recordMap"]
    bot_records = record_map.get("bot", {})

    result = []
    for bot_id in data["botIds"]:
        raw = bot_records.get(bot_id, {}).get("value", {})
        # Notion wraps records as {value: {value: {...}, role: ...}} — unwrap if nested
        record = raw.get("value", raw) if isinstance(raw, dict) and "value" in raw else raw
        if not isinstance(record, dict):
            continue
        result.append(
            BotInfo(
                bot_id=bot_id,
                name=record.get("name", ""),
                space_id=record.get("space_id", ""),
                integration_id=record.get("integration_id", ""),
                alive=record.get("alive", False),
            )
        )
    return result


async def find_bot_by_name(name: str) -> BotInfo | None:
    """Find a bot by exact name match."""
    bots = await list_bots()
    for bot in bots:
        if bot.name == name:
            return bot
    return None


async def health_check() -> dict[str, Any]:
    """Verify internal API connectivity and session validity.

    Returns a dict with status of each endpoint.
    """
    results: dict[str, Any] = {}

    # 1. Session validity
    try:
        _load_session_headers()
        results["session"] = "ok"
    except NotionInternalApiError as e:
        results["session"] = f"fail: {e}"
        return results

    # 2. List spaces
    try:
        spaces = await get_available_spaces()
        results["getSpaces"] = f"ok ({len(spaces)} spaces)"
    except NotionInternalApiError as e:
        results["getSpaces"] = f"fail: {e}"

    # 3. List bots
    try:
        bots = await list_bots()
        results["listBots"] = f"ok ({len(bots)} bots)"
    except NotionInternalApiError as e:
        results["listBots"] = f"fail: {e}"

    return results
