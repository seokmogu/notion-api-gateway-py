"""Page ID parsing and normalization utilities."""

from __future__ import annotations

import re

_UUID_RE = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_TRAILING_ID_RE = re.compile(r"-([0-9a-f]{32})(?:\?|$)", re.IGNORECASE)
_NOTION_PAGE_RE = re.compile(
    r"https?://(?:www\.)?notion\.so/(?:[^/]+/)?(?:[^/]+-)?([0-9a-f]{32})(?:\?|$)",
    re.IGNORECASE,
)


def normalize_page_id(page_id: str) -> str:
    """Convert a 32-char hex string or UUID to standard dash format.

    Example: '3197d8322b04802eaf59e199b1c7d23f' -> '3197d832-2b04-802e-af59-e199b1c7d23f'
    """
    cleaned = page_id.strip().replace("-", "").lower()
    if not _HEX32_RE.match(cleaned):
        raise ValueError(f"Invalid page ID format: {page_id}")
    return f"{cleaned[0:8]}-{cleaned[8:12]}-{cleaned[12:16]}-{cleaned[16:20]}-{cleaned[20:32]}"


def extract_canonical_page_id(input_str: str) -> str:
    """Extract and normalize a Notion page ID from a URL or raw ID.

    Raises ValueError if the input is not a valid Notion page reference.
    """
    text = input_str.strip()

    if ".notion.site" in text:
        raise ValueError(
            "notion.site URLs (external shares) are not supported. "
            "Please use an internal Notion page URL: "
            "https://www.notion.so/workspace/Page-Name-<id>"
        )

    # Direct UUID
    if _UUID_RE.match(text):
        return normalize_page_id(text)

    # Direct 32-char hex
    if _HEX32_RE.match(text):
        return normalize_page_id(text)

    # Notion page URL
    m = _NOTION_PAGE_RE.search(text)
    if m:
        return normalize_page_id(m.group(1))

    # Trailing ID pattern: ...-<32hex>
    m = _TRAILING_ID_RE.search(text)
    if m:
        return normalize_page_id(m.group(1))

    raise ValueError(
        f"Cannot extract page ID from: {text}\n"
        "Valid formats:\n"
        "  - UUID: 3197d832-2b04-802e-af59-e199b1c7d23f\n"
        "  - Hex: 3197d8322b04802eaf59e199b1c7d23f\n"
        "  - URL: https://www.notion.so/workspace/Page-Name-3197d8322b04802eaf59e199b1c7d23f"
    )


def build_deterministic_integration_name(
    prefix: str, page_id: str, page_title: str | None = None
) -> str:
    """Build a deterministic integration name from prefix, title and page ID.

    Example: 'API Access Data Platform Tribe 3197d832'
    """
    title_part = (page_title or "").strip()[:40].strip()
    id_suffix = page_id.replace("-", "")[-8:]
    parts = [prefix, title_part, id_suffix] if title_part else [prefix, id_suffix]
    return " ".join(parts)
