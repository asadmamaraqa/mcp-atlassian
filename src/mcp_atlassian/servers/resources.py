"""MCP resources for browseable Atlassian context."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastmcp import Context, FastMCP

from mcp_atlassian.jira.users import JiraUser

from .dependencies import get_confluence_fetcher, get_jira_fetcher

RESOURCE_MIME_TYPE = "text/markdown"


def _quoted_segment(value: str) -> str:
    return quote(value, safe="~")


def _stringify(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or default
    return str(value)


def _append_kv(
    lines: list[str], label: str, value: Any, default: str = "Unknown"
) -> None:
    lines.append(f"- {label}: {_stringify(value, default)}")


def _resource_hint(uri: str) -> str:
    return f"Resource URI: `{uri}`"


def _build_issue_lines(issues: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for issue in issues:
        key = _stringify(issue.get("key"))
        summary = _stringify(issue.get("summary"), "No summary")
        status_data = issue.get("status") if isinstance(issue, dict) else None
        status = "Unknown"
        if isinstance(status_data, dict):
            status = _stringify(status_data.get("name"))
        elif status_data is not None:
            status = _stringify(status_data)
        lines.append(f"- {key}: {summary} [{status}]")
    return lines


def register_atlassian_resources(server: FastMCP[Any]) -> None:
    @server.resource(
        "atlassian://overview",
        name="atlassian_overview",
        title="Atlassian Overview",
        description="Connected Atlassian account summary with entry points for Jira and Confluence resources.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"atlassian", "overview", "resources"},
    )
    async def atlassian_overview(ctx: Context) -> str:
        lines = [
            "# Atlassian Overview",
            "",
            "Browse these MCP resources to load account, Jira, and Confluence context into the session.",
            "",
            "## Available Resources",
            "- Jira account: `atlassian://jira/account`",
            "- Jira projects index: `atlassian://jira/projects`",
            "- Confluence account: `atlassian://confluence/account`",
            "- Confluence spaces index: `atlassian://confluence/spaces`",
            "",
        ]

        try:
            jira = await get_jira_fetcher(ctx)
            account_id = jira.get_current_user_account_id()
            user = jira.get_user_profile_by_identifier(account_id)
            user_data = user.to_simplified_dict()
            lines.extend(
                [
                    "## Jira Account",
                    f"- Name: {_stringify(user_data.get('display_name'))}",
                    f"- Email: {_stringify(user_data.get('email'), 'Unavailable')}",
                    f"- Site: {_stringify(getattr(jira.config, 'url', None))}",
                    "",
                ]
            )
        except Exception as exc:
            lines.extend(
                [
                    "## Jira Account",
                    f"- Status: Unavailable ({exc})",
                    "",
                ]
            )

        try:
            confluence = await get_confluence_fetcher(ctx)
            user_data = confluence.get_current_user_info()
            display_name = user_data.get("displayName") or user_data.get("username")
            lines.extend(
                [
                    "## Confluence Account",
                    f"- Name: {_stringify(display_name)}",
                    f"- Email: {_stringify(user_data.get('email'), 'Unavailable')}",
                    f"- Site: {_stringify(getattr(confluence.config, 'url', None))}",
                    "",
                ]
            )
        except Exception as exc:
            lines.extend(
                [
                    "## Confluence Account",
                    f"- Status: Unavailable ({exc})",
                    "",
                ]
            )

        lines.extend(
            [
                "## How To Use",
                "- Open the Jira projects resource to browse available projects.",
                "- Open a project resource to inspect details, components, versions, and recent issues.",
                "- Open the Confluence spaces resource to browse spaces.",
                "- Open a space resource, then a page resource, to load the page body as MCP context.",
            ]
        )
        return "\n".join(lines)

    @server.resource(
        "atlassian://jira/account",
        name="jira_account_resource",
        title="Jira Account",
        description="Current Jira account details for the authenticated user.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"jira", "account", "resources"},
    )
    async def jira_account_resource(ctx: Context) -> str:
        try:
            jira = await get_jira_fetcher(ctx)
            raw_user = jira.jira.myself()
            if isinstance(raw_user, dict):
                user = JiraUser.from_api_response(raw_user).to_simplified_dict()
            else:
                account_id = jira.get_current_user_account_id()
                user = jira.get_user_profile_by_identifier(
                    account_id
                ).to_simplified_dict()
            lines = [
                "# Jira Account",
                "",
                _resource_hint("atlassian://jira/account"),
                "",
            ]
            _append_kv(lines, "Name", user.get("display_name"))
            _append_kv(lines, "Email", user.get("email"), "Unavailable")
            _append_kv(lines, "Username", user.get("name"), "Unavailable")
            _append_kv(lines, "Key", user.get("key"), "Unavailable")
            _append_kv(lines, "Site", getattr(jira.config, "url", None))
            lines.extend(["", "See also: `atlassian://jira/projects`"])
            return "\n".join(lines)
        except Exception as exc:
            return "\n".join(
                [
                    "# Jira Account",
                    "",
                    _resource_hint("atlassian://jira/account"),
                    "",
                    f"- Status: Unavailable ({exc})",
                ]
            )

    @server.resource(
        "atlassian://jira/projects",
        name="jira_projects_resource",
        title="Jira Projects",
        description="Browse Jira projects available to the authenticated user.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"jira", "projects", "resources"},
    )
    async def jira_projects_resource(ctx: Context) -> str:
        try:
            jira = await get_jira_fetcher(ctx)
            projects = jira.get_all_projects(include_archived=False)
        except Exception as exc:
            return "\n".join(
                [
                    "# Jira Projects",
                    "",
                    _resource_hint("atlassian://jira/projects"),
                    "",
                    f"- Status: Unavailable ({exc})",
                ]
            )

        lines = [
            "# Jira Projects",
            "",
            _resource_hint("atlassian://jira/projects"),
            "",
            f"- Total projects: {len(projects)}",
            "",
            "## Project Resources",
        ]
        for project in projects:
            key = _stringify(project.get("key"))
            name = _stringify(project.get("name"), key)
            resource_uri = f"atlassian://jira/projects/{_quoted_segment(key)}"
            lines.append(f"- {key}: {name} -> `{resource_uri}`")
        return "\n".join(lines)

    @server.resource(
        "atlassian://jira/projects/{project_key}",
        name="jira_project_detail_resource",
        title="Jira Project Detail",
        description="Detailed Jira project context including issue types, components, versions, and recent issues.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"jira", "projects", "resources", "detail"},
    )
    async def jira_project_detail_resource(project_key: str, ctx: Context) -> str:
        jira = await get_jira_fetcher(ctx)
        normalized_key = project_key.upper()
        resource_uri = f"atlassian://jira/projects/{_quoted_segment(normalized_key)}"

        project = jira.get_project(normalized_key)
        if not project:
            return "\n".join(
                [
                    f"# Jira Project {normalized_key}",
                    "",
                    _resource_hint(resource_uri),
                    "",
                    f"- Status: Project not found or inaccessible ({normalized_key})",
                ]
            )

        components = jira.get_project_components(normalized_key)
        versions = jira.get_project_versions(normalized_key)
        issue_types = jira.get_project_issue_types(normalized_key)
        issues_result = jira.get_project_issues(normalized_key, limit=10)
        issues_data = issues_result.to_simplified_dict()
        issue_count = jira.get_project_issues_count(normalized_key)

        lines = [
            f"# Jira Project {normalized_key}",
            "",
            _resource_hint(resource_uri),
            "",
            "## Summary",
        ]
        _append_kv(lines, "Name", project.get("name"), normalized_key)
        _append_kv(lines, "Description", project.get("description"), "No description")
        _append_kv(lines, "Type", project.get("projectTypeKey"), "Unknown")
        lead = project.get("lead") if isinstance(project.get("lead"), dict) else None
        _append_kv(lines, "Lead", lead.get("displayName") if lead else None, "Unknown")
        _append_kv(lines, "Archived", project.get("archived"), "False")
        _append_kv(lines, "Issue count", issue_count, "0")

        if issue_types:
            lines.extend(["", "## Issue Types"])
            for issue_type in issue_types[:15]:
                lines.append(f"- {_stringify(issue_type.get('name'))}")

        if components:
            lines.extend(["", "## Components"])
            for component in components[:15]:
                lines.append(f"- {_stringify(component.get('name'))}")

        if versions:
            lines.extend(["", "## Versions"])
            for version in versions[:15]:
                version_name = _stringify(version.get("name"))
                released = version.get("released")
                suffix = " (released)" if released else ""
                lines.append(f"- {version_name}{suffix}")

        lines.extend(["", "## Recent Issues"])
        issue_lines = _build_issue_lines(issues_data.get("issues", []))
        lines.extend(issue_lines or ["- No recent issues available"])
        return "\n".join(lines)

    @server.resource(
        "atlassian://confluence/account",
        name="confluence_account_resource",
        title="Confluence Account",
        description="Current Confluence account details for the authenticated user.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"confluence", "account", "resources"},
    )
    async def confluence_account_resource(ctx: Context) -> str:
        try:
            confluence = await get_confluence_fetcher(ctx)
            user = confluence.get_current_user_info()
            lines = [
                "# Confluence Account",
                "",
                _resource_hint("atlassian://confluence/account"),
                "",
            ]
            _append_kv(lines, "Name", user.get("displayName") or user.get("username"))
            _append_kv(lines, "Email", user.get("email"), "Unavailable")
            _append_kv(lines, "Account ID", user.get("accountId"), "Unavailable")
            _append_kv(lines, "Site", getattr(confluence.config, "url", None))
            lines.extend(["", "See also: `atlassian://confluence/spaces`"])
            return "\n".join(lines)
        except Exception as exc:
            return "\n".join(
                [
                    "# Confluence Account",
                    "",
                    _resource_hint("atlassian://confluence/account"),
                    "",
                    f"- Status: Unavailable ({exc})",
                ]
            )

    @server.resource(
        "atlassian://confluence/spaces",
        name="confluence_spaces_resource",
        title="Confluence Spaces",
        description="Browse Confluence spaces available to the authenticated user.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"confluence", "spaces", "resources"},
    )
    async def confluence_spaces_resource(ctx: Context) -> str:
        try:
            confluence = await get_confluence_fetcher(ctx)
            spaces_data = confluence.get_spaces(start=0, limit=100)
            raw_spaces = (
                spaces_data.get("results", []) if isinstance(spaces_data, dict) else []
            )
            spaces = raw_spaces if isinstance(raw_spaces, list) else []
        except Exception as exc:
            return "\n".join(
                [
                    "# Confluence Spaces",
                    "",
                    _resource_hint("atlassian://confluence/spaces"),
                    "",
                    f"- Status: Unavailable ({exc})",
                ]
            )

        lines = [
            "# Confluence Spaces",
            "",
            _resource_hint("atlassian://confluence/spaces"),
            "",
            f"- Total spaces: {len(spaces)}",
            "",
            "## Space Resources",
        ]
        for space in spaces:
            key = _stringify(space.get("key"))
            name = _stringify(space.get("name"), key)
            resource_uri = f"atlassian://confluence/spaces/{_quoted_segment(key)}"
            lines.append(f"- {key}: {name} -> `{resource_uri}`")
        return "\n".join(lines)

    @server.resource(
        "atlassian://confluence/spaces/{space_key}",
        name="confluence_space_detail_resource",
        title="Confluence Space Detail",
        description="Detailed Confluence space context with page tree and page resource URIs.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"confluence", "spaces", "resources", "detail"},
    )
    async def confluence_space_detail_resource(space_key: str, ctx: Context) -> str:
        confluence = await get_confluence_fetcher(ctx)
        resource_uri = f"atlassian://confluence/spaces/{_quoted_segment(space_key)}"
        spaces_data = confluence.get_spaces(start=0, limit=100)
        raw_spaces = (
            spaces_data.get("results", []) if isinstance(spaces_data, dict) else []
        )
        spaces = raw_spaces if isinstance(raw_spaces, list) else []
        space = next(
            (
                item
                for item in spaces
                if _stringify(item.get("key")).upper() == space_key.upper()
            ),
            None,
        )

        tree = confluence.get_space_page_tree(space_key=space_key, limit=200)
        lines = [
            f"# Confluence Space {space_key}",
            "",
            _resource_hint(resource_uri),
            "",
            "## Summary",
        ]
        _append_kv(lines, "Name", space.get("name") if space else None, space_key)
        _append_kv(lines, "Type", space.get("type") if space else None, "Unknown")
        _append_kv(lines, "Status", space.get("status") if space else None, "Unknown")
        _append_kv(lines, "Total pages in tree", tree.get("total_pages"), "0")

        lines.extend(["", "## Page Resources"])
        pages = tree.get("pages", []) if isinstance(tree, dict) else []
        if isinstance(pages, list) and pages:
            for page in pages[:100]:
                if not isinstance(page, dict):
                    continue
                page_id = _stringify(page.get("id"))
                title = _stringify(page.get("title"), f"Page {page_id}")
                depth = page.get("depth") if isinstance(page.get("depth"), int) else 0
                indent = "  " * max(depth, 0)
                page_uri = f"atlassian://confluence/pages/{_quoted_segment(page_id)}"
                lines.append(f"{indent}- {title} ({page_id}) -> `{page_uri}`")
        else:
            lines.append("- No pages available")

        lines.extend(
            [
                "",
                "Selecting a page resource loads its content as MCP context for the session.",
            ]
        )
        return "\n".join(lines)

    @server.resource(
        "atlassian://confluence/pages/{page_id}",
        name="confluence_page_context_resource",
        title="Confluence Page Context",
        description="Confluence page content and metadata suitable for direct MCP context loading.",
        mime_type=RESOURCE_MIME_TYPE,
        tags={"confluence", "pages", "resources", "context"},
    )
    async def confluence_page_context_resource(page_id: str, ctx: Context) -> str:
        confluence = await get_confluence_fetcher(ctx)
        resource_uri = f"atlassian://confluence/pages/{_quoted_segment(page_id)}"
        page = confluence.get_page_content(page_id, convert_to_markdown=True)
        if page is None:
            return "\n".join(
                [
                    f"# Confluence Page {page_id}",
                    "",
                    _resource_hint(resource_uri),
                    "",
                    f"- Status: Page not found or inaccessible ({page_id})",
                ]
            )

        page_data = page.to_simplified_dict()
        metadata = page_data.get("metadata", page_data)
        content = page.content or ""
        lines = [
            f"# {_stringify(metadata.get('title'), f'Page {page_id}')}",
            "",
            _resource_hint(resource_uri),
            "",
            "## Metadata",
        ]
        _append_kv(lines, "Page ID", metadata.get("id"), page_id)
        space = (
            metadata.get("space") if isinstance(metadata.get("space"), dict) else None
        )
        _append_kv(lines, "Space", space.get("key") if space else None, "Unknown")
        _append_kv(lines, "URL", metadata.get("url"), "Unavailable")
        version = (
            metadata.get("version")
            if isinstance(metadata.get("version"), dict)
            else None
        )
        _append_kv(
            lines, "Version", version.get("number") if version else None, "Unknown"
        )

        lines.extend(["", "## Page Content", content or "_No page content available._"])
        return "\n".join(lines)
