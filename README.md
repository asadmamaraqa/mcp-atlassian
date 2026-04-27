# Atlassian MCP Server for Function AI

Model Context Protocol (MCP) server for Atlassian products (Confluence and Jira), deployed as a plugin for **[Function AI](https://github.com/solita-internal/function-ai)**.

Based on [sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian) with custom extensions for Function AI integration: OAuth bridge for per-user Atlassian authentication, frontend resource endpoints for plugin cards, session-backed project/space preferences, and Azure deployment infrastructure.

## Architecture

This server runs as a Docker container alongside the Function AI backend and exposes MCP tools over **streamable-http** at `/mcp`. Function AI proxies user requests to this server, forwarding per-user OAuth tokens so each user interacts with Jira/Confluence under their own identity.

### Key additions over upstream

| Component | Path | Purpose |
|-----------|------|---------|
| FunctionAI OAuth bridge | `src/mcp_atlassian/servers/functionai_oauth.py` | Per-user OAuth 2.0 session management, cookie-based session tracking |
| Frontend resources | `src/mcp_atlassian/servers/frontend_resource.py` | `GET /api/resources/frontend` endpoint for plugin card UI |
| MCP resources | `src/mcp_atlassian/servers/resources.py` | Browseable Atlassian context via MCP resource URIs |
| User preferences | via `functionai_oauth.py` | Session-scoped Jira project / Confluence space selection |
| Dependencies | `src/mcp_atlassian/servers/dependencies.py` | Per-request fetcher creation with user token injection |
| Azure infra | `infra/bicep/` | Bicep modules for Azure Container Apps deployment |
| CI/CD | `.github/workflows/` | Deploy pipelines for dev/test/prod (infra, code, connectivity) |

## Running Locally

### Prerequisites

- Docker
- An `.env` file with Atlassian credentials (copy from `.env.example`)

### Docker

```bash
# Build
docker build -t atlassian-mcp-local .

# Run
docker run -d \
  --name atlassian-mcp-local \
  -p 8001:8000 \
  --env-file .env \
  atlassian-mcp-local \
  --transport streamable-http \
  --host 0.0.0.0 \
  --port 8000 \
  --path /mcp
```

The MCP endpoint will be available at `http://localhost:8001/mcp`.

> **Note:** Function AI backend expects the server on port 8001 locally since the backend itself uses port 8000.

### Authentication

The server supports multiple auth methods (in order of precedence):

1. **OAuth 2.0** — per-user tokens forwarded by Function AI as `Authorization: Bearer <signed_session_cookie>`
2. **API Token** (Cloud) — `JIRA_USERNAME` + `JIRA_API_TOKEN`
3. **Personal Access Token** (Server/Data Center) — `JIRA_PERSONAL_TOKEN`

For local development without OAuth, set API token credentials in `.env`. See `.env.example` for all options.

## Deployment

Infrastructure is managed with Azure Bicep and deployed via GitHub Actions workflows:

| Workflow | Purpose |
|----------|---------|
| `deploy-infra-{env}.yml` | Provision Azure Container Apps, networking, DNS |
| `deploy-code-{env}.yml` | Build and push Docker image, deploy to Container Apps |
| `deploy-connectivity-{env}.yml` | Configure private endpoints and DNS zone links |

Environments: `dev`, `test`, `prod`.

## Tools

| Jira | Confluence |
|------|------------|
| `jira_search` — Search with JQL | `confluence_search` — Search with CQL |
| `jira_get_issue` — Get issue details | `confluence_get_page` — Get page content |
| `jira_create_issue` — Create issues | `confluence_create_page` — Create pages |
| `jira_update_issue` — Update issues | `confluence_update_page` — Update pages |
| `jira_transition_issue` — Change status | `confluence_add_comment` — Add comments |

See upstream [Tools Reference](https://mcp-atlassian.soomiles.com/docs/tools-reference) for the full list.

## Development

```bash
uv sync --frozen --all-extras --dev   # install dependencies
pre-commit install                     # setup hooks
pre-commit run --all-files            # lint (Ruff + mypy)
uv run pytest -xvs                    # run tests
```

## Security

Never share API tokens. Keep `.env` files secure. See [SECURITY.md](SECURITY.md).

## License

MIT — See [LICENSE](LICENSE). Based on [sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian). Not an official Atlassian product.
