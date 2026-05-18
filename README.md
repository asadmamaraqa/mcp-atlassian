# MCP Atlassian

This is a Model Context Protocol (MCP) server for Atlassian products (Confluence and Jira), supporting both Cloud and Server/Data Center deployments.

## Quick Start

### 1. Get Your API Token
Go to https://id.atlassian.com/manage-profile/security/api-tokens and create a token.
(For Server/Data Center, use a Personal Access Token instead)

### 2. Configure Your IDE
Add to your Claude Desktop or Cursor MCP configuration:

```json
{
  "mcpServers": {
    "mcp-atlassian": {
      "command": "uvx",
      "args": ["mcp-atlassian"],
      "env": {
        "JIRA_URL": "https://your-company.atlassian.net",
        "JIRA_USERNAME": "your.email@company.com",
        "JIRA_API_TOKEN": "your_api_token",
        "CONFLUENCE_URL": "https://your-company.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "your.email@company.com",
        "CONFLUENCE_API_TOKEN": "your_api_token"
      }
    }
  }
}
```

### 3. Start Using
Ask your AI assistant to:
- "Find issues assigned to me in PROJ project"
- "Search Confluence for onboarding docs"
- "Create a bug ticket for the login issue"
- "Update the status of PROJ-123 to Done"

## Key Tools (72 tools total)

**Jira:**
- `jira_search` - Search with JQL
- `jira_get_issue` - Get issue details
- `jira_create_issue` - Create issues
- `jira_update_issue` - Update issues
- `jira_transition_issue` - Change status

**Confluence:**
- `confluence_search` - Search with CQL
- `confluence_get_page` - Get page content
- `confluence_create_page` - Create pages
- `confluence_update_page` - Update pages
- `confluence_add_comment` - Add comments

## Compatibility

- **Confluence Cloud**: Fully supported
- **Confluence Server/Data Center**: Supported (v6.0+)
- **Jira Cloud**: Fully supported
- **Jira Server/Data Center**: Supported (v8.14+)

Full documentation is available at [mcp-atlassian.soomiles.com](https://mcp-atlassian.soomiles.com).

test