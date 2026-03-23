"""Tests for page ID parsing and normalization utilities."""

from __future__ import annotations

import pytest

from notion_gateway.services.page_id import (
    build_deterministic_integration_name,
    extract_canonical_page_id,
    normalize_page_id,
)


class TestNormalizePageId:
    def test_hex32_to_uuid(self) -> None:
        result = normalize_page_id("3197d8322b04802eaf59e199b1c7d23f")
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_already_uuid(self) -> None:
        result = normalize_page_id("3197d832-2b04-802e-af59-e199b1c7d23f")
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_uppercase(self) -> None:
        result = normalize_page_id("3197D8322B04802EAF59E199B1C7D23F")
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_with_whitespace(self) -> None:
        result = normalize_page_id("  3197d8322b04802eaf59e199b1c7d23f  ")
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_invalid_length(self) -> None:
        with pytest.raises(ValueError, match="Invalid page ID format"):
            normalize_page_id("abc123")

    def test_invalid_chars(self) -> None:
        with pytest.raises(ValueError, match="Invalid page ID format"):
            normalize_page_id("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")


class TestExtractCanonicalPageId:
    def test_direct_uuid(self) -> None:
        result = extract_canonical_page_id("3197d832-2b04-802e-af59-e199b1c7d23f")
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_direct_hex32(self) -> None:
        result = extract_canonical_page_id("3197d8322b04802eaf59e199b1c7d23f")
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_notion_url(self) -> None:
        url = "https://www.notion.so/workspace/My-Page-3197d8322b04802eaf59e199b1c7d23f"
        result = extract_canonical_page_id(url)
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_notion_url_with_query(self) -> None:
        url = "https://www.notion.so/workspace/Page-3197d8322b04802eaf59e199b1c7d23f?v=abc"
        result = extract_canonical_page_id(url)
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_notion_url_no_workspace(self) -> None:
        url = "https://www.notion.so/My-Page-3197d8322b04802eaf59e199b1c7d23f"
        result = extract_canonical_page_id(url)
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"

    def test_notion_site_rejected(self) -> None:
        with pytest.raises(ValueError, match="notion.site"):
            extract_canonical_page_id("https://myworkspace.notion.site/page-abc123")

    def test_invalid_input(self) -> None:
        with pytest.raises(ValueError, match="Cannot extract page ID"):
            extract_canonical_page_id("not a valid id")

    def test_trailing_id_pattern(self) -> None:
        url = "https://example.com/some-path-3197d8322b04802eaf59e199b1c7d23f"
        result = extract_canonical_page_id(url)
        assert result == "3197d832-2b04-802e-af59-e199b1c7d23f"


class TestBuildDeterministicIntegrationName:
    def test_with_title(self) -> None:
        result = build_deterministic_integration_name(
            "API Access", "3197d832-2b04-802e-af59-e199b1c7d23f", "Data Platform Tribe"
        )
        assert result == "API Access Data Platform Tribe 3197d832"

    def test_without_title(self) -> None:
        result = build_deterministic_integration_name(
            "API Access", "3197d832-2b04-802e-af59-e199b1c7d23f"
        )
        assert result == "API Access 3197d832"

    def test_long_title_truncated(self) -> None:
        long_title = "A" * 60
        result = build_deterministic_integration_name(
            "API Access", "3197d832-2b04-802e-af59-e199b1c7d23f", long_title
        )
        assert len(result.split(" ")[1]) <= 40  # Title part truncated
        assert result.endswith("3197d832")

    def test_empty_title(self) -> None:
        result = build_deterministic_integration_name(
            "API Access", "3197d832-2b04-802e-af59-e199b1c7d23f", ""
        )
        assert result == "API Access 3197d832"
