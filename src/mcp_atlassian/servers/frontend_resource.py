"""Frontend resource script support for FunctionAI plugin cards."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from starlette.requests import Request

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.jira import JiraFetcher

from .dependencies import _create_user_config_for_fetcher, _resolve_bearer_auth_type


def _stringify(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or default
    return str(value)


def _safe_preview(value: Any, default: str = "") -> str:
    return _stringify(value, default)[:240]


def _quoted_segment(value: str) -> str:
    return quote(value, safe="~")


def build_jira_fetcher_for_request(
    request: Request,
    global_config: Any,
) -> JiraFetcher:
    user_token = getattr(request.state, "user_atlassian_token", None)
    user_auth_type = getattr(request.state, "user_atlassian_auth_type", None)
    user_email = getattr(request.state, "user_atlassian_email", None)
    user_cloud_id = getattr(request.state, "user_atlassian_cloud_id", None)

    if not user_token or user_auth_type not in {"oauth", "pat"}:
        raise ValueError("Missing authenticated Jira session")

    resolved_auth_type = _resolve_bearer_auth_type(
        global_config,
        user_auth_type,
        user_cloud_id,
    )
    credentials = {"user_email_context": user_email}
    if resolved_auth_type == "oauth":
        credentials["oauth_access_token"] = user_token
    else:
        credentials["personal_access_token"] = user_token

    user_config = _create_user_config_for_fetcher(
        base_config=global_config,
        auth_type=resolved_auth_type,
        credentials=credentials,
        cloud_id=user_cloud_id,
    )
    return JiraFetcher(config=user_config)


def build_confluence_fetcher_for_request(
    request: Request,
    global_config: Any,
) -> ConfluenceFetcher:
    user_token = getattr(request.state, "user_atlassian_token", None)
    user_auth_type = getattr(request.state, "user_atlassian_auth_type", None)
    user_email = getattr(request.state, "user_atlassian_email", None)
    user_cloud_id = getattr(request.state, "user_atlassian_cloud_id", None)

    if not user_token or user_auth_type not in {"oauth", "pat"}:
        raise ValueError("Missing authenticated Confluence session")

    resolved_auth_type = _resolve_bearer_auth_type(
        global_config,
        user_auth_type,
        user_cloud_id,
    )
    credentials = {"user_email_context": user_email}
    if resolved_auth_type == "oauth":
        credentials["oauth_access_token"] = user_token
    else:
        credentials["personal_access_token"] = user_token

    user_config = _create_user_config_for_fetcher(
        base_config=global_config,
        auth_type=resolved_auth_type,
        credentials=credentials,
        cloud_id=user_cloud_id,
    )
    return ConfluenceFetcher(config=user_config)


def _summarize_issue(issue: dict[str, Any], project_key: str = "") -> dict[str, str]:
    status_data = issue.get("status") if isinstance(issue, dict) else None
    if isinstance(status_data, dict):
        status = _stringify(status_data.get("name"))
    else:
        status = _stringify(status_data, "Unknown")
    key = _stringify(issue.get("key"))
    return {
        "key": key,
        "summary": _stringify(issue.get("summary"), "No summary"),
        "status": status,
        "resourceUri": f"atlassian://jira/projects/{_quoted_segment(project_key)}/issues/{_quoted_segment(key)}"
        if project_key and key
        else "",
    }


def build_frontend_payload(
    jira_fetcher: JiraFetcher | None,
    confluence_fetcher: ConfluenceFetcher | None,
    *,
    selected_jira_project: str | None = None,
    show_confluence: bool = True,
    preferences_supported: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jira": {"available": False, "account": None, "projects": []},
        "confluence": {
            "available": False,
            "account": None,
            "spaces": [],
            "spacesSource": "direct",
            "emptyMessage": None,
            "hidden": not show_confluence,
        },
        "preferences": {
            "supported": preferences_supported,
            "selectedJiraProject": selected_jira_project,
            "showConfluence": show_confluence,
        },
    }

    if jira_fetcher is not None:
        account = None
        try:
            raw_user = jira_fetcher.jira.myself()
            account = {
                "name": _stringify(raw_user.get("displayName")),
                "email": _stringify(raw_user.get("emailAddress"), "Unavailable"),
                "site": _stringify(getattr(jira_fetcher.config, "url", None)),
            }
        except Exception:
            account = None

        projects: list[dict[str, Any]] = []
        for project in jira_fetcher.get_all_projects(include_archived=False)[:40]:
            if not isinstance(project, dict):
                continue
            key = _stringify(project.get("key"))
            issue_items: list[dict[str, Any]] = []
            try:
                issues_result = jira_fetcher.get_project_issues(key, limit=25)
                issues_data = issues_result.to_simplified_dict()
                issue_items = [
                    item
                    for item in issues_data.get("issues", [])[:25]
                    if isinstance(item, dict)
                ]
            except Exception:
                issue_items = []

            projects.append(
                {
                    "key": key,
                    "name": _stringify(project.get("name"), key),
                    "description": _safe_preview(
                        project.get("description"),
                        "No description",
                    ),
                    "type": _stringify(project.get("projectTypeKey"), "Unknown"),
                    "archived": bool(project.get("archived", False)),
                    "resourceUri": f"atlassian://jira/projects/{_quoted_segment(key)}",
                    "recentIssues": [
                        _summarize_issue(issue, key) for issue in issue_items
                    ],
                }
            )

        if selected_jira_project:
            normalized_selected = selected_jira_project.upper()
            projects.sort(
                key=lambda project: (
                    project.get("key", "").upper() != normalized_selected,
                    project.get("key", ""),
                )
            )

        payload["jira"] = {
            "available": True,
            "account": account,
            "projects": projects,
        }

    if show_confluence and confluence_fetcher is not None:
        try:
            user = confluence_fetcher.get_current_user_info()
        except Exception:
            user = {}

        spaces: list[dict[str, Any]] = []
        spaces_source = "direct"
        empty_message = None
        try:
            spaces_data = confluence_fetcher.get_spaces(start=0, limit=30)
            raw_spaces = (
                spaces_data.get("results", []) if isinstance(spaces_data, dict) else []
            )

            if not raw_spaces:
                contributed_spaces = confluence_fetcher.get_user_contributed_spaces(
                    limit=100
                )
                if contributed_spaces:
                    raw_spaces = list(contributed_spaces.values())
                    spaces_source = "contributed"
                else:
                    spaces_source = "empty"
                    empty_message = (
                        "No visible spaces were returned. This usually means your "
                        "Confluence session cannot list spaces yet, or you do not "
                        "have access to any visible spaces."
                    )

            for space in raw_spaces[:20]:
                if not isinstance(space, dict):
                    continue

                space_key = _stringify(space.get("key"))
                pages: list[dict[str, Any]] = []
                try:
                    tree = confluence_fetcher.get_space_page_tree(
                        space_key=space_key,
                        limit=80,
                    )
                    raw_pages = tree.get("pages", []) if isinstance(tree, dict) else []
                    for page in raw_pages[:80]:
                        if not isinstance(page, dict):
                            continue

                        page_id = _stringify(page.get("id"))
                        pages.append(
                            {
                                "id": page_id,
                                "title": _stringify(
                                    page.get("title"),
                                    f"Page {page_id}",
                                ),
                                "depth": int(page.get("depth", 0) or 0),
                                "resourceUri": (
                                    f"atlassian://confluence/pages/{_quoted_segment(page_id)}"
                                ),
                            }
                        )
                except Exception:
                    pages = []

                spaces.append(
                    {
                        "key": space_key,
                        "name": _stringify(space.get("name"), space_key),
                        "type": _stringify(space.get("type"), "Unknown"),
                        "status": _stringify(space.get("status"), "Unknown"),
                        "resourceUri": (
                            f"atlassian://confluence/spaces/{_quoted_segment(space_key)}"
                        ),
                        "pages": pages,
                    }
                )
        except Exception:
            spaces = []
            spaces_source = "error"
            empty_message = (
                "Confluence spaces could not be loaded for this session. "
                "Re-authentication or additional Confluence space-read access may be required."
            )

        payload["confluence"] = {
            "available": True,
            "account": {
                "name": _stringify(user.get("displayName") or user.get("username")),
                "email": _stringify(user.get("email"), "Unavailable"),
                "site": _stringify(getattr(confluence_fetcher.config, "url", None)),
            }
            if user
            else None,
            "spaces": spaces,
            "spacesSource": spaces_source,
            "emptyMessage": empty_message,
            "hidden": False,
        }

    return payload


def build_frontend_resource_script(payload: dict[str, Any], theme: str) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"))
    theme_json = json.dumps(theme)
    template = """
(() => {
  const payload = __PAYLOAD__;
  const theme = __THEME__ === 'dark' ? 'dark' : 'light';
  const bridge = window.__SOLITAIRE_MCP_BRIDGE || {};
  const serverId = typeof bridge.serverId === 'string' ? bridge.serverId : '';
  const accessToken = typeof bridge.accessToken === 'string' ? bridge.accessToken : '';
  const ensureViewport = () => {
    const existing = document.querySelector('meta[name="viewport"]');
    const viewport = existing || document.createElement('meta');
    viewport.setAttribute('name', 'viewport');
    viewport.setAttribute('content', 'width=device-width, initial-scale=1');
    if (!existing) {
      document.head.appendChild(viewport);
    }
  };
  const constrainDocument = () => {
    const html = document.documentElement;
    html.style.width = '100%';
    html.style.maxWidth = '100%';
    html.style.overflowX = 'hidden';
    if (document.body) {
      document.body.style.width = '100%';
      document.body.style.maxWidth = '100%';
      document.body.style.overflowX = 'hidden';
    }
  };
  ensureViewport();
  constrainDocument();
  const root = document.createElement('div');
  root.id = 'atlassian-resource-root';
  root.style.width = '100%';
  root.style.maxWidth = '100%';
  root.style.minWidth = '0';
  root.style.overflowX = 'hidden';
  document.body.appendChild(root);

  const state = {
    selectedProjectKey: payload.preferences?.selectedJiraProject || '',
    showConfluence: payload.preferences?.showConfluence !== false,
    preferencesSupported: payload.preferences?.supported === true,
    isSaving: false,
    statusMessage: '',
    openPanels: {},
    issueSearchByProject: {},
    issueVisibleByProject: {},
    pageSearchBySpace: {},
    pageVisibleBySpace: {},
  };

  const INITIAL_VISIBLE_ISSUES = 5;
  const ISSUE_PAGE_SIZE = 5;
  const INITIAL_VISIBLE_PAGES = 8;
  const PAGE_BATCH_SIZE = 8;

  const css = `
    :root {
      color-scheme: ${theme};
      --bg: ${theme === 'dark' ? '#09090b' : '#ffffff'};
      --panel: ${theme === 'dark' ? '#18181b' : '#f8fafc'};
      --panel-strong: ${theme === 'dark' ? '#27272a' : '#eef2ff'};
      --text: ${theme === 'dark' ? '#f4f4f5' : '#0f172a'};
      --muted: ${theme === 'dark' ? '#a1a1aa' : '#475569'};
      --border: ${theme === 'dark' ? '#3f3f46' : '#dbe4f0'};
      --accent: ${theme === 'dark' ? '#38bdf8' : '#0369a1'};
      --accent-soft: ${theme === 'dark' ? 'rgba(56,189,248,0.14)' : 'rgba(3,105,161,0.08)'};
      --ok: ${theme === 'dark' ? '#4ade80' : '#15803d'};
    }
    * { box-sizing: border-box; }
    html, body { width: 100%; max-width: 100%; overflow-x: hidden; }
    body { margin: 0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }
    #atlassian-resource-root { width: 100%; max-width: 100%; overflow-x: hidden; }
    .wrap { width: 100%; max-width: 100%; min-width: 0; padding: 8px; display: grid; gap: 12px; }
    .hero { padding: 16px; border: 1px solid var(--border); border-radius: 18px; background: var(--bg); }
    .collapse-arrow { display: inline-block; width: 7px; height: 7px; border-right: 2px solid var(--muted); border-bottom: 2px solid var(--muted); transform: rotate(45deg); transition: transform .2s ease; flex-shrink: 0; margin-left: auto; }
    .collapse-arrow.closed { transform: rotate(-45deg); }
    .hero summary { display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none; }
    .hero summary::-webkit-details-marker { display: none; }
    .hero h1 { margin: 0; font-size: 14px; }
    .hero .hero-body { display: grid; gap: 6px; padding-top: 10px; }
    .hero p, .muted { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
    .section-collapse { padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg); }
    .section-collapse summary { display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none; }
    .section-collapse h2 { margin: 0; font-size: 13px; }
    .section-collapse summary::-webkit-details-marker { display: none; }
    .section-collapse .section-body { display: grid; gap: 8px; padding-top: 8px; }
    .section { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--border); border-radius: 16px; background: var(--panel); }
    .section h2 { margin: 0; font-size: 14px; }
    .account { display: grid; gap: 2px; padding: 10px 12px; border-radius: 12px; background: var(--accent-soft); }
    .hero, .section, .account, .actions, details, summary, .body, .page { width: 100%; max-width: 100%; min-width: 0; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .actions > * { max-width: 100%; min-width: 0; }
    details { border: 1px solid var(--border); border-radius: 12px; background: rgba(255,255,255,0.02); overflow: hidden; }
    summary { list-style: none; cursor: pointer; padding: 12px 14px; display: flex; justify-content: space-between; gap: 10px; align-items: center; }
    summary::-webkit-details-marker { display: none; }
    summary > span { min-width: 0; }
    .title { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
    .meta { color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
    .body { padding: 0 14px 14px; display: grid; gap: 10px; align-items: stretch; }
    .body > * { width: 100%; max-width: 100%; }
    .pill { display: inline-flex; padding: 3px 8px; border-radius: 999px; font-size: 11px; background: var(--accent-soft); color: var(--accent); border: 1px solid var(--border); margin-right: 6px; }
    ul { margin: 0; padding-left: 18px; }
    li { margin: 4px 0; }
    code { display: block; width: 100%; max-width: 100%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; background: rgba(127,127,127,0.14); padding: 2px 6px; border-radius: 8px; overflow-wrap: anywhere; white-space: pre-wrap; }
    .copy-icon { appearance: none; border: none; background: transparent; cursor: pointer; padding: 2px; flex-shrink: 0; color: var(--muted); display: inline-flex; align-items: center; vertical-align: middle; }
    .copy-icon:hover { color: var(--accent); }
    .copy-icon svg { width: 14px; height: 14px; }
    .uri-row { display: flex; align-items: center; gap: 6px; min-width: 0; }
    button.action { appearance: none; border: 1px solid var(--border); background: transparent; color: var(--text); padding: 8px 10px; border-radius: 10px; font-size: 11px; cursor: pointer; max-width: 100%; }
    button.action:hover { background: var(--accent-soft); }
    button.action[disabled] { opacity: 0.6; cursor: wait; }
    .toggle { display: inline-flex; align-items: center; gap: 8px; color: var(--text); font-size: 11px; }
    .toggle input { accent-color: var(--accent); }
    .status { min-height: 16px; color: var(--muted); font-size: 11px; }
    .search-row { display: grid; gap: 8px; }
    .search-input { width: 100%; min-width: 0; border: 1px solid var(--border); border-radius: 10px; background: ${theme === 'dark' ? 'rgba(24,24,27,0.92)' : 'rgba(255,255,255,0.96)'}; color: var(--text); padding: 9px 10px; font: inherit; font-size: 12px; }
    .search-input::placeholder { color: var(--muted); }
    .list-meta { color: var(--muted); font-size: 11px; }
    .issue-list { display: grid; gap: 8px; margin: 0; padding: 0; width: 100%; list-style: none; }
    .issue-card { display: grid; gap: 4px; width: 100%; max-width: 100%; align-self: stretch; padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: ${theme === 'dark' ? 'rgba(39,39,42,0.72)' : 'rgba(255,255,255,0.92)'}; }
    .issue-card-title { font-size: 11px; line-height: 1.4; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .issue-card-meta { color: var(--muted); font-size: 10px; line-height: 1.3; }
    .show-more-row { display: flex; justify-content: flex-start; }
    .page { width: 100%; max-width: 100%; align-self: stretch; padding: 10px 12px 10px min(calc(var(--depth) * 14px + 12px), 68px); display: grid; gap: 6px; margin: 8px 0; border: 1px solid var(--border); border-radius: 12px; background: ${theme === 'dark' ? 'rgba(39,39,42,0.72)' : 'rgba(255,255,255,0.92)'}; }
    .page-title { font-size: 12px; font-weight: 600; }
    .footer { color: var(--ok); font-size: 12px; }
    .tips-banner { padding: 10px 12px; border: 1px solid var(--border); border-radius: 12px; background: var(--accent-soft); }
    .tips-banner summary { padding: 6px 0; display: flex; align-items: center; gap: 8px; cursor: pointer; list-style: none; font-size: 12px; font-weight: 600; color: var(--accent); }
    .tips-banner summary::-webkit-details-marker { display: none; }
    .tips-banner .tips-body { padding-top: 8px; display: grid; gap: 6px; }
    .tips-banner .tip-item { display: flex; gap: 6px; align-items: baseline; font-size: 11px; color: var(--text); line-height: 1.5; }
    .tips-banner .tip-bullet { color: var(--accent); font-weight: 700; flex-shrink: 0; }
    .tips-banner .tip-example { font-style: italic; color: var(--muted); }
  `;

  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const copyUri = async (uri) => {
    try {
      await navigator.clipboard.writeText(uri);
    } catch (_error) {
      const textarea = document.createElement('textarea');
      textarea.value = uri;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      textarea.remove();
    }
  };

  const escapeHtml = (value) => String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  const formatSite = (url) => {
    if (!url) return '';
    try {
      const h = new URL(url).hostname;
      if (h === 'api.atlassian.com') return url.split('/').pop() ? url.match(/atlassian\\.com\\/ex\\/\\w+\\/(.+)/)?.[1] || 'Atlassian Cloud' : 'Atlassian Cloud';
      return h;
    } catch { return url; }
  };

  const normalizeSearch = (value) => String(value || '').trim().toLowerCase();

  const matchesSearch = (values, query) => {
    if (!query) {
      return true;
    }
    return values.some((value) => normalizeSearch(value).includes(query));
  };

  const getIssueSearch = (projectKey) => state.issueSearchByProject[projectKey] || '';
  const getPageSearch = (spaceKey) => state.pageSearchBySpace[spaceKey] || '';

  const getIssueVisibleCount = (projectKey) => {
    const current = state.issueVisibleByProject[projectKey];
    return typeof current === 'number' ? current : INITIAL_VISIBLE_ISSUES;
  };

  const getPageVisibleCount = (spaceKey) => {
    const current = state.pageVisibleBySpace[spaceKey];
    return typeof current === 'number' ? current : INITIAL_VISIBLE_PAGES;
  };

  const filterIssues = (project) => {
    const query = normalizeSearch(getIssueSearch(project.key));
    return (project.recentIssues || []).filter((issue) => matchesSearch([
      issue.key,
      issue.summary,
      issue.status,
      issue.resourceUri,
    ], query));
  };

  const filterPages = (space) => {
    const query = normalizeSearch(getPageSearch(space.key));
    return (space.pages || []).filter((page) => matchesSearch([
      page.title,
      page.id,
      page.resourceUri,
    ], query));
  };

  const captureOpenPanels = () => {
    root.querySelectorAll('details[data-panel-id]').forEach((panel) => {
      const panelId = panel.getAttribute('data-panel-id');
      if (!panelId) {
        return;
      }
      state.openPanels[panelId] = panel.open;
    });
    ['hero-panel', 'jira-panel', 'confluence-panel', 'tips-panel'].forEach((id) => {
      const el = root.querySelector('#' + id);
      if (el) { state.openPanels[id] = el.open; }
    });
  };

  const isPanelOpen = (panelId) => state.openPanels[panelId] === true;

  const captureRenderContext = () => {
    const scrollingElement = document.scrollingElement || document.documentElement;
    const activeElement = document.activeElement;
    const context = {
      scrollTop: scrollingElement ? scrollingElement.scrollTop : 0,
      focusSelector: null,
      selectionStart: null,
      selectionEnd: null,
    };

    if (!activeElement || !root.contains(activeElement)) {
      return context;
    }

    if (activeElement.matches('[data-issue-search]')) {
      const projectKey = activeElement.getAttribute('data-issue-search');
      if (projectKey) {
        context.focusSelector = `[data-issue-search="${projectKey}"]`;
      }
    } else if (activeElement.matches('[data-page-search]')) {
      const spaceKey = activeElement.getAttribute('data-page-search');
      if (spaceKey) {
        context.focusSelector = `[data-page-search="${spaceKey}"]`;
      }
    }

    if (typeof activeElement.selectionStart === 'number') {
      context.selectionStart = activeElement.selectionStart;
      context.selectionEnd = activeElement.selectionEnd;
    }

    return context;
  };

  const restoreRenderContext = (context) => {
    const scrollingElement = document.scrollingElement || document.documentElement;
    if (scrollingElement && typeof context.scrollTop === 'number') {
      scrollingElement.scrollTop = context.scrollTop;
    }

    if (!context.focusSelector) {
      return;
    }

    const nextInput = root.querySelector(context.focusSelector);
    if (!nextInput) {
      return;
    }

    nextInput.focus({ preventScroll: true });
    if (
      typeof context.selectionStart === 'number' &&
      typeof nextInput.setSelectionRange === 'function'
    ) {
      nextInput.setSelectionRange(
        context.selectionStart,
        typeof context.selectionEnd === 'number'
          ? context.selectionEnd
          : context.selectionStart,
      );
    }
  };

  const getSelectedProject = () => payload.jira.projects.find(
    (project) => String(project.key || '').toUpperCase() === state.selectedProjectKey.toUpperCase()
  );

  const getPreferencesEndpoint = () => {
    if (!serverId) {
      return null;
    }
    return `/api/mcp/frontend/preferences?server_id=${encodeURIComponent(serverId)}`;
  };

  const savePreferences = async (patch) => {
    const endpoint = getPreferencesEndpoint();
    if (!endpoint || !state.preferencesSupported) {
      return;
    }

    state.isSaving = true;
    state.statusMessage = 'Saving session context...';
    render();

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (accessToken) {
        headers.Authorization = `Bearer ${accessToken}`;
      }

      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers,
        body: JSON.stringify(patch),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || 'Failed to save session context');
      }

      state.selectedProjectKey = data.selected_jira_project || '';
      state.showConfluence = data.show_confluence !== false;
      state.statusMessage = 'Session context updated';
    } catch (error) {
      state.statusMessage = error instanceof Error ? error.message : 'Failed to save session context';
    } finally {
      state.isSaving = false;
      render();
      if (state.statusMessage) {
        setTimeout(() => {
          state.statusMessage = '';
          render();
        }, 1800);
      }
    }
  };

  const renderProject = (project) => {
    const isSelected = String(project.key || '').toUpperCase() === state.selectedProjectKey.toUpperCase();
    const panelId = `jira:${project.key}`;
    const filteredIssues = filterIssues(project);
    const visibleIssues = filteredIssues.slice(0, getIssueVisibleCount(project.key));
    const issueSearch = getIssueSearch(project.key);
    const hasMoreIssues = visibleIssues.length < filteredIssues.length;
    return `
    <details data-panel-id="${escapeHtml(panelId)}" ${isPanelOpen(panelId) ? 'open' : ''}>
      <summary>
        <span>
          <span class="title">${escapeHtml(project.key)} · ${escapeHtml(project.name)}</span><br>
          <span class="meta">${escapeHtml(project.type)}${project.archived ? ' · archived' : ''}${isSelected ? ' · selected for chat' : ''}</span>
        </span>
        <span class="collapse-arrow ${isPanelOpen(panelId) ? '' : 'closed'}"></span>
      </summary>
      <div class="body">
        <div class="muted">${escapeHtml(project.description)}</div>
        <div class="uri-row"><span class="pill">MCP project resource</span> <code>${escapeHtml(project.resourceUri)}</code><button class="copy-icon" data-copy="${escapeHtml(project.resourceUri)}" title="Copy URI"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button></div>
        <div class="actions">
          ${state.preferencesSupported ? `<button class="action" data-select-project="${escapeHtml(project.key)}" ${state.isSaving ? 'disabled' : ''}>${isSelected ? 'Selected for chat' : 'Use for chat context'}</button>` : ''}
        </div>
        <div>
          <div class="title" style="margin-bottom:6px;">Tickets</div>
          <div class="search-row">
            <input class="search-input" type="search" data-issue-search="${escapeHtml(project.key)}" value="${escapeHtml(issueSearch)}" placeholder="Search tickets by key, summary, or status">
            <div class="list-meta">Showing ${visibleIssues.length} of ${filteredIssues.length} loaded tickets${issueSearch ? ` for \"${escapeHtml(issueSearch)}\"` : ''}</div>
          </div>
          <ul class="issue-list">
            ${visibleIssues.length ? visibleIssues.map((issue) => `<li class="issue-card"><div class="issue-card-title">${escapeHtml(issue.key)} · ${escapeHtml(issue.summary)}</div><div class="issue-card-meta">${escapeHtml(issue.status)}</div>${issue.resourceUri ? `<div class="uri-row"><code>${escapeHtml(issue.resourceUri)}</code><button class="copy-icon" data-copy="${escapeHtml(issue.resourceUri)}" title="Copy URI"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button></div>` : ''}</li>`).join('') : '<li class="muted">No matching tickets in the loaded set</li>'}
          </ul>
          ${hasMoreIssues ? `<div class="show-more-row"><button class="action" data-show-more-issues="${escapeHtml(project.key)}">Show more tickets</button></div>` : ''}
        </div>
      </div>
    </details>`;
  };

  const renderSpace = (space) => {
    const panelId = `confluence:${space.key}`;
    const filteredPages = filterPages(space);
    const visiblePages = filteredPages.slice(0, getPageVisibleCount(space.key));
    const pageSearch = getPageSearch(space.key);
    const hasMorePages = visiblePages.length < filteredPages.length;
    return `
    <details data-panel-id="${escapeHtml(panelId)}" ${isPanelOpen(panelId) ? 'open' : ''}>
      <summary>
        <span>
          <span class="title">${escapeHtml(space.key)} · ${escapeHtml(space.name)}</span><br>
          <span class="meta">${escapeHtml(space.type)} · ${escapeHtml(space.status)} · ${space.pages.length} pages</span>
        </span>
        <span class="collapse-arrow ${isPanelOpen(panelId) ? '' : 'closed'}"></span>
      </summary>
      <div class="body">
        <div class="uri-row"><span class="pill">MCP space resource</span> <code>${escapeHtml(space.resourceUri)}</code><button class="copy-icon" data-copy="${escapeHtml(space.resourceUri)}" title="Copy URI"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button></div>
        <div>
          <div class="title" style="margin-bottom:6px;">Pages</div>
          <div class="search-row">
            <input class="search-input" type="search" data-page-search="${escapeHtml(space.key)}" value="${escapeHtml(pageSearch)}" placeholder="Search loaded pages by title or ID">
            <div class="list-meta">Showing ${visiblePages.length} of ${filteredPages.length} loaded pages${pageSearch ? ` for \"${escapeHtml(pageSearch)}\"` : ''}</div>
          </div>
          ${visiblePages.length ? visiblePages.map((page) => `
            <div class="page" style="--depth:${page.depth};">
              <div class="page-title">${escapeHtml(page.title)}</div>
              <div class="uri-row"><code>${escapeHtml(page.resourceUri)}</code><button class="copy-icon" data-copy="${escapeHtml(page.resourceUri)}" title="Copy URI"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button></div>
            </div>`).join('') : '<div class="muted">No matching pages in the loaded set</div>'}
          ${hasMorePages ? `<div class="show-more-row"><button class="action" data-show-more-pages="${escapeHtml(space.key)}">Show more pages</button></div>` : ''}
        </div>
      </div>
    </details>`;
  };

  const render = () => {
    const renderContext = captureRenderContext();
    if (root.childElementCount) {
      captureOpenPanels();
    }

    const selectedProject = getSelectedProject();
    const selectedSummary = selectedProject
      ? `${escapeHtml(selectedProject.key)} · ${escapeHtml(selectedProject.name)}`
      : 'No Jira project selected';

    root.innerHTML = `
      <div class="wrap">
        ${payload.jira.available && payload.jira.account ? `
          <div class="account">
            <div><strong>${escapeHtml(payload.jira.account.name)}</strong></div>
            <div class="muted">${escapeHtml(payload.jira.account.email)}</div>
            ${payload.jira.account.site && payload.jira.account.site !== 'Unknown' ? `<div class="muted">${escapeHtml(formatSite(payload.jira.account.site))}</div>` : ''}
          </div>
        ` : (payload.confluence.available && payload.confluence.account ? `
          <div class="account">
            <div><strong>${escapeHtml(payload.confluence.account.name)}</strong></div>
            <div class="muted">${escapeHtml(payload.confluence.account.email)}</div>
            ${payload.confluence.account.site && payload.confluence.account.site !== 'Unknown' ? `<div class="muted">${escapeHtml(formatSite(payload.confluence.account.site))}</div>` : ''}
          </div>
        ` : '')}
        ${(() => { const nonPersonal = (payload.confluence.spaces || []).filter(s => !s.key.startsWith('~')); return payload.confluence.available && nonPersonal.length ? `
          <div class="section-collapse" style="padding:10px 12px; border:1px solid var(--border); border-radius:12px;">
            <h2 style="margin:0 0 8px; font-size:13px;">Connected Spaces</h2>
            <div style="display:grid; gap:4px;">
              ${nonPersonal.map((space) => `<div class="muted" style="font-size:12px;">• ${escapeHtml(space.name)}${space.key ? ' (' + escapeHtml(space.key) + ')' : ''}</div>`).join('')}
            </div>
          </div>
        ` : ''; })()}
        <div class="footer">Connected to Atlassian. Use Jira and Confluence tools via chat.</div>
      </div>`;

    root.querySelectorAll('[data-copy]').forEach((button) => {
      button.addEventListener('click', async () => {
        const uri = button.getAttribute('data-copy');
        if (!uri) return;
        await copyUri(uri);
        button.style.color = 'var(--ok)';
        setTimeout(() => { button.style.color = ''; }, 1200);
      });
    });

    root.querySelectorAll('details[data-panel-id]').forEach((panel) => {
      panel.addEventListener('toggle', () => {
        const panelId = panel.getAttribute('data-panel-id');
        if (!panelId) {
          return;
        }
        state.openPanels[panelId] = panel.open;
        const arrow = panel.querySelector(':scope > summary > .collapse-arrow');
        if (arrow) { arrow.classList.toggle('closed', !panel.open); }
      });
    });

    root.querySelectorAll('details.hero, details.section-collapse').forEach((panel) => {
      panel.addEventListener('toggle', () => {
        const arrow = panel.querySelector(':scope > summary > .collapse-arrow');
        if (arrow) { arrow.classList.toggle('closed', !panel.open); }
      });
    });

    root.querySelectorAll('[data-select-project]').forEach((button) => {
      button.addEventListener('click', async () => {
        const projectKey = button.getAttribute('data-select-project');
        if (!projectKey) return;
        await savePreferences({ selected_jira_project: projectKey });
      });
    });

    const clearButton = root.querySelector('[data-clear-project="true"]');
    if (clearButton) {
      clearButton.addEventListener('click', async () => {
        await savePreferences({ selected_jira_project: null });
      });
    }

    const confluenceToggle = root.querySelector('[data-toggle-confluence="true"]');
    if (confluenceToggle) {
      confluenceToggle.addEventListener('change', async (event) => {
        const nextValue = Boolean(event.target.checked);
        await savePreferences({ show_confluence: nextValue });
      });
    }

    root.querySelectorAll('[data-issue-search]').forEach((input) => {
      input.addEventListener('input', (event) => {
        const projectKey = input.getAttribute('data-issue-search');
        if (!projectKey) return;
        state.issueSearchByProject[projectKey] = event.target.value || '';
        state.issueVisibleByProject[projectKey] = INITIAL_VISIBLE_ISSUES;
        render();
      });
    });

    root.querySelectorAll('[data-page-search]').forEach((input) => {
      input.addEventListener('input', (event) => {
        const spaceKey = input.getAttribute('data-page-search');
        if (!spaceKey) return;
        state.pageSearchBySpace[spaceKey] = event.target.value || '';
        state.pageVisibleBySpace[spaceKey] = INITIAL_VISIBLE_PAGES;
        render();
      });
    });

    root.querySelectorAll('[data-show-more-issues]').forEach((button) => {
      button.addEventListener('click', () => {
        const projectKey = button.getAttribute('data-show-more-issues');
        if (!projectKey) return;
        state.issueVisibleByProject[projectKey] = getIssueVisibleCount(projectKey) + ISSUE_PAGE_SIZE;
        render();
      });
    });

    root.querySelectorAll('[data-show-more-pages]').forEach((button) => {
      button.addEventListener('click', () => {
        const spaceKey = button.getAttribute('data-show-more-pages');
        if (!spaceKey) return;
        state.pageVisibleBySpace[spaceKey] = getPageVisibleCount(spaceKey) + PAGE_BATCH_SIZE;
        render();
      });
    });

    restoreRenderContext(renderContext);
  };

  render();
})();
""".strip()
    return template.replace("__PAYLOAD__", payload_json).replace(
        "__THEME__", theme_json
    )
