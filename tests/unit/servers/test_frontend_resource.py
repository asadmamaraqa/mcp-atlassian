"""Unit tests for Atlassian frontend resource payload generation."""

from unittest.mock import MagicMock

from mcp_atlassian.servers.frontend_resource import (
    build_frontend_payload,
    build_frontend_resource_script,
)


def _make_confluence_fetcher() -> MagicMock:
    fetcher = MagicMock()
    fetcher.get_current_user_info.return_value = {
        "displayName": "Test User",
        "email": "test.user@example.com",
    }
    fetcher.config.url = "https://example.atlassian.net/wiki"
    return fetcher


def test_build_frontend_payload_falls_back_to_contributed_spaces() -> None:
    """Use contributed spaces when direct space listing returns none."""
    fetcher = _make_confluence_fetcher()
    fetcher.get_spaces.return_value = {"results": []}
    fetcher.get_user_contributed_spaces.return_value = {
        "ENG": {"key": "ENG", "name": "Engineering"}
    }
    fetcher.get_space_page_tree.return_value = {"pages": []}

    payload = build_frontend_payload(
        jira_fetcher=None,
        confluence_fetcher=fetcher,
        show_confluence=True,
    )

    assert payload["confluence"]["spacesSource"] == "contributed"
    assert payload["confluence"]["spaces"][0]["key"] == "ENG"
    assert payload["confluence"]["emptyMessage"] is None


def test_build_frontend_payload_sets_empty_message_for_no_spaces() -> None:
    """Set a helpful message when neither direct nor fallback spaces exist."""
    fetcher = _make_confluence_fetcher()
    fetcher.get_spaces.return_value = {"results": []}
    fetcher.get_user_contributed_spaces.return_value = {}

    payload = build_frontend_payload(
        jira_fetcher=None,
        confluence_fetcher=fetcher,
        show_confluence=True,
    )

    assert payload["confluence"]["spaces"] == []
    assert payload["confluence"]["spacesSource"] == "empty"
    assert "No visible spaces were returned" in payload["confluence"]["emptyMessage"]


def test_build_frontend_resource_script_constrains_iframe_layout() -> None:
    """Generated iframe content should stay within the plugin card width."""
    script = build_frontend_resource_script(
        payload={
            "jira": {"available": False, "account": None, "projects": []},
            "confluence": {
                "available": False,
                "account": None,
                "spaces": [],
                "spacesSource": "direct",
                "emptyMessage": None,
                "hidden": False,
            },
            "preferences": {
                "supported": False,
                "selectedJiraProject": None,
                "showConfluence": True,
            },
        },
        theme="light",
    )

    assert "viewport.setAttribute('content', 'width=device-width, initial-scale=1');" in script
    assert "html.style.width = '100%';" in script
    assert "document.body.style.maxWidth = '100%';" in script
    assert "root.style.maxWidth = '100%';" in script
    assert "html, body { width: 100%; max-width: 100%; overflow-x: hidden; }" in script
    assert "#atlassian-resource-root { width: 100%; max-width: 100%; overflow-x: hidden; }" in script
    assert "summary > span { min-width: 0; }" in script
    assert ".issue-card { display: grid; gap: 4px; width: 100%;" in script
    assert "padding: 10px 12px 10px min(calc(var(--depth) * 14px + 12px), 68px);" in script


def test_build_frontend_resource_script_supports_search_and_show_more() -> None:
    """Generated iframe content should support filtering and incremental reveal."""
    script = build_frontend_resource_script(
        payload={
            "jira": {"available": False, "account": None, "projects": []},
            "confluence": {
                "available": False,
                "account": None,
                "spaces": [],
                "spacesSource": "direct",
                "emptyMessage": None,
                "hidden": False,
            },
            "preferences": {
                "supported": False,
                "selectedJiraProject": None,
                "showConfluence": True,
            },
        },
        theme="light",
    )

    assert "data-issue-search" in script
    assert "data-page-search" in script
    assert "data-show-more-issues" in script
    assert "data-show-more-pages" in script
    assert "INITIAL_VISIBLE_ISSUES = 5" in script
    assert "INITIAL_VISIBLE_PAGES = 8" in script
    assert "Search tickets by key, summary, or status" in script
    assert "Search loaded pages by title or ID" in script


def test_build_frontend_resource_script_preserves_open_panels() -> None:
    """Generated iframe content should preserve expanded state across rerenders."""
    script = build_frontend_resource_script(
        payload={
            "jira": {"available": False, "account": None, "projects": []},
            "confluence": {
                "available": False,
                "account": None,
                "spaces": [],
                "spacesSource": "direct",
                "emptyMessage": None,
                "hidden": False,
            },
            "preferences": {
                "supported": False,
                "selectedJiraProject": None,
                "showConfluence": True,
            },
        },
        theme="light",
    )

    assert "openPanels: {}" in script
    assert "captureOpenPanels" in script
    assert "details[data-panel-id]" in script
    assert "data-panel-id=\"${escapeHtml(panelId)}\"" in script
    assert "panel.addEventListener('toggle'" in script


def test_build_frontend_resource_script_preserves_focus_and_scroll() -> None:
    """Generated iframe content should restore search focus and scroll on rerender."""
    script = build_frontend_resource_script(
        payload={
            "jira": {"available": False, "account": None, "projects": []},
            "confluence": {
                "available": False,
                "account": None,
                "spaces": [],
                "spacesSource": "direct",
                "emptyMessage": None,
                "hidden": False,
            },
            "preferences": {
                "supported": False,
                "selectedJiraProject": None,
                "showConfluence": True,
            },
        },
        theme="light",
    )

    assert "captureRenderContext" in script
    assert "restoreRenderContext" in script
    assert "document.scrollingElement || document.documentElement" in script
    assert "focus({ preventScroll: true })" in script
    assert "setSelectionRange" in script
