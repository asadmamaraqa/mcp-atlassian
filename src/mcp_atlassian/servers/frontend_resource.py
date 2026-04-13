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


def _summarize_issue(issue: dict[str, Any]) -> dict[str, str]:
    status_data = issue.get("status") if isinstance(issue, dict) else None
    if isinstance(status_data, dict):
        status = _stringify(status_data.get("name"))
    else:
        status = _stringify(status_data, "Unknown")
    return {
        "key": _stringify(issue.get("key")),
        "summary": _stringify(issue.get("summary"), "No summary"),
        "status": status,
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
                issues_result = jira_fetcher.get_project_issues(key, limit=5)
                issues_data = issues_result.to_simplified_dict()
                issue_items = [
                    item
                    for item in issues_data.get("issues", [])[:5]
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
                    "recentIssues": [_summarize_issue(issue) for issue in issue_items],
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
        try:
            spaces_data = confluence_fetcher.get_spaces(start=0, limit=30)
            raw_spaces = (
                spaces_data.get("results", []) if isinstance(spaces_data, dict) else []
            )

            for space in raw_spaces[:20]:
                if not isinstance(space, dict):
                    continue
                space_key = _stringify(space.get("key"))
                pages: list[dict[str, Any]] = []
                try:
                    tree = confluence_fetcher.get_space_page_tree(
                        space_key=space_key,
                        limit=40,
                    )
                    raw_pages = tree.get("pages", []) if isinstance(tree, dict) else []
                    for page in raw_pages[:40]:
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

        payload["confluence"] = {
            "available": True,
            "account": {
                "name": _stringify(
                    user.get("displayName") or user.get("username")
                ),
                "email": _stringify(user.get("email"), "Unavailable"),
                "site": _stringify(getattr(confluence_fetcher.config, "url", None)),
            }
            if user
            else None,
            "spaces": spaces,
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
  const root = document.createElement('div');
  root.id = 'atlassian-resource-root';
  document.body.appendChild(root);

  const state = {
    selectedProjectKey: payload.preferences?.selectedJiraProject || '',
    showConfluence: payload.preferences?.showConfluence !== false,
    preferencesSupported: payload.preferences?.supported === true,
    isSaving: false,
    statusMessage: '',
  };

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
    body { margin: 0; font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: radial-gradient(circle at top left, var(--accent-soft), transparent 35%), var(--bg); color: var(--text); }
    .wrap { padding: 18px; display: grid; gap: 16px; }
    .hero { display: grid; gap: 6px; padding: 16px; border: 1px solid var(--border); border-radius: 18px; background: linear-gradient(135deg, var(--panel-strong), var(--panel)); }
    .hero h1 { margin: 0; font-size: 18px; }
    .hero p, .muted { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
    .section { display: grid; gap: 10px; padding: 14px; border: 1px solid var(--border); border-radius: 16px; background: var(--panel); }
    .section h2 { margin: 0; font-size: 14px; }
    .account { display: grid; gap: 2px; padding: 10px 12px; border-radius: 12px; background: var(--accent-soft); }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    details { border: 1px solid var(--border); border-radius: 12px; background: rgba(255,255,255,0.02); overflow: hidden; }
    summary { list-style: none; cursor: pointer; padding: 12px 14px; display: flex; justify-content: space-between; gap: 10px; align-items: center; }
    summary::-webkit-details-marker { display: none; }
    .title { font-size: 13px; font-weight: 600; }
    .meta { color: var(--muted); font-size: 11px; }
    .body { padding: 0 14px 14px; display: grid; gap: 10px; }
    .pill { display: inline-flex; padding: 3px 8px; border-radius: 999px; font-size: 11px; background: var(--accent-soft); color: var(--accent); border: 1px solid var(--border); margin-right: 6px; }
    ul { margin: 0; padding-left: 18px; }
    li { margin: 4px 0; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; background: rgba(127,127,127,0.14); padding: 2px 6px; border-radius: 8px; word-break: break-all; }
    button.copy { appearance: none; border: 1px solid var(--border); background: transparent; color: var(--text); padding: 8px 10px; border-radius: 10px; font-size: 11px; cursor: pointer; justify-self: start; }
    button.copy:hover { background: var(--accent-soft); }
    button.action { appearance: none; border: 1px solid var(--border); background: transparent; color: var(--text); padding: 8px 10px; border-radius: 10px; font-size: 11px; cursor: pointer; }
    button.action:hover { background: var(--accent-soft); }
    button.action[disabled] { opacity: 0.6; cursor: wait; }
    .toggle { display: inline-flex; align-items: center; gap: 8px; color: var(--text); font-size: 11px; }
    .toggle input { accent-color: var(--accent); }
    .status { min-height: 16px; color: var(--muted); font-size: 11px; }
    .page { padding-left: calc(var(--depth) * 14px); display: grid; gap: 6px; margin: 8px 0; }
    .page-title { font-size: 12px; font-weight: 600; }
    .footer { color: var(--ok); font-size: 12px; }
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
    return `
    <details>
      <summary>
        <span>
          <span class="title">${escapeHtml(project.key)} · ${escapeHtml(project.name)}</span><br>
          <span class="meta">${escapeHtml(project.type)}${project.archived ? ' · archived' : ''}${isSelected ? ' · selected for chat' : ''}</span>
        </span>
        <span class="meta">Open</span>
      </summary>
      <div class="body">
        <div class="muted">${escapeHtml(project.description)}</div>
        <div><span class="pill">MCP project resource</span> <code>${escapeHtml(project.resourceUri)}</code></div>
        <div class="actions">
          <button class="copy" data-copy="${escapeHtml(project.resourceUri)}">Copy project resource URI</button>
          ${state.preferencesSupported ? `<button class="action" data-select-project="${escapeHtml(project.key)}" ${state.isSaving ? 'disabled' : ''}>${isSelected ? 'Selected for chat' : 'Use for chat context'}</button>` : ''}
        </div>
        <div>
          <div class="title" style="margin-bottom:6px;">Recent issues</div>
          <ul>
            ${project.recentIssues.length ? project.recentIssues.map((issue) => `<li><strong>${escapeHtml(issue.key)}</strong> ${escapeHtml(issue.summary)} <span class="meta">[${escapeHtml(issue.status)}]</span></li>`).join('') : '<li class="muted">No recent issues available</li>'}
          </ul>
        </div>
      </div>
    </details>`;
  };

  const renderSpace = (space) => `
    <details>
      <summary>
        <span>
          <span class="title">${escapeHtml(space.key)} · ${escapeHtml(space.name)}</span><br>
          <span class="meta">${escapeHtml(space.type)} · ${escapeHtml(space.status)} · ${space.pages.length} pages</span>
        </span>
        <span class="meta">Open</span>
      </summary>
      <div class="body">
        <div><span class="pill">MCP space resource</span> <code>${escapeHtml(space.resourceUri)}</code></div>
        <button class="copy" data-copy="${escapeHtml(space.resourceUri)}">Copy space resource URI</button>
        <div>
          <div class="title" style="margin-bottom:6px;">Pages</div>
          ${space.pages.length ? space.pages.map((page) => `
            <div class="page" style="--depth:${page.depth};">
              <div class="page-title">${escapeHtml(page.title)}</div>
              <code>${escapeHtml(page.resourceUri)}</code>
              <button class="copy" data-copy="${escapeHtml(page.resourceUri)}">Copy page context URI</button>
            </div>`).join('') : '<div class="muted">No pages available</div>'}
        </div>
      </div>
    </details>`;

  const render = () => {
    const selectedProject = getSelectedProject();
    const selectedSummary = selectedProject
      ? `${escapeHtml(selectedProject.key)} · ${escapeHtml(selectedProject.name)}`
      : 'No Jira project selected';

    root.innerHTML = `
      <div class="wrap">
        <section class="hero">
          <h1>Atlassian Resource Browser</h1>
          <p>Browse connected Jira projects and Confluence spaces directly from the plugin card.</p>
          <p>Selecting a Jira project here scopes this Atlassian session so chat searches know which project you mean. Confluence visibility is controlled separately for this card.</p>
        </section>
        <section class="section">
          <h2>Chat Context</h2>
          ${state.preferencesSupported ? `
            <div class="account">
              <div><strong>${selectedSummary}</strong></div>
              <div class="muted">${selectedProject ? 'Jira searches in this session will use the selected project as context.' : 'Choose a Jira project below to scope this Atlassian session.'}</div>
            </div>
            <div class="actions">
              ${state.selectedProjectKey ? `<button class="action" data-clear-project="true" ${state.isSaving ? 'disabled' : ''}>Clear project context</button>` : ''}
              <label class="toggle">
                <input type="checkbox" data-toggle-confluence="true" ${state.showConfluence ? 'checked' : ''} ${state.isSaving ? 'disabled' : ''}>
                <span>Show Confluence in this card</span>
              </label>
            </div>
            <div class="status">${escapeHtml(state.statusMessage)}</div>
          ` : `
            <div class="muted">Session-scoped project context is available when this plugin is connected with SSO.</div>
          `}
        </section>
        <section class="section">
          <h2>Jira</h2>
          ${payload.jira.available && payload.jira.account ? `
            <div class="account">
              <div><strong>${escapeHtml(payload.jira.account.name)}</strong></div>
              <div class="muted">${escapeHtml(payload.jira.account.email)}</div>
              <div class="muted">${escapeHtml(payload.jira.account.site)}</div>
            </div>
            ${payload.jira.projects.map(renderProject).join('') || '<div class="muted">No visible projects.</div>'}
          ` : '<div class="muted">Jira is unavailable for this session.</div>'}
        </section>
        ${state.showConfluence ? `
          <section class="section">
            <h2>Confluence</h2>
            ${payload.confluence.available && payload.confluence.account ? `
              <div class="account">
                <div><strong>${escapeHtml(payload.confluence.account.name)}</strong></div>
                <div class="muted">${escapeHtml(payload.confluence.account.email)}</div>
                <div class="muted">${escapeHtml(payload.confluence.account.site)}</div>
              </div>
              ${payload.confluence.spaces.map(renderSpace).join('') || '<div class="muted">No visible spaces.</div>'}
            ` : '<div class="muted">Confluence is unavailable for this session.</div>'}
          </section>
        ` : ''}
        <div class="footer">Copying a URI gives you the canonical MCP resource to use in the resource browser or chat workflow.</div>
      </div>`;

    root.querySelectorAll('[data-copy]').forEach((button) => {
      button.addEventListener('click', async () => {
        const uri = button.getAttribute('data-copy');
        if (!uri) return;
        await copyUri(uri);
        const original = button.textContent;
        button.textContent = 'Copied';
        setTimeout(() => { button.textContent = original; }, 1200);
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
  };

  render();
})();
""".strip()
    return template.replace("__PAYLOAD__", payload_json).replace("__THEME__", theme_json)
