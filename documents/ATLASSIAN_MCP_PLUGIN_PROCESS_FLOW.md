# Atlassian MCP Plugin Process Flow In Function AI

This document shows the high-level process flow for Atlassian MCP usage in Function AI.

## Process Flow Diagram

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant FE as Function AI Frontend
    participant BE as Function AI Backend
    participant AMS as atlassian-mcp-server
    participant ATL as Atlassian API

    Note over User,FE: 1. Plugin setup and connection
    User->>FE: Enable Atlassian plugin and click Connect
    FE->>BE: Send plugin connection request
    BE->>AMS: Request auth URL through MCP auth initiate path
    AMS-->>BE: Return Atlassian authorization URL
    BE-->>FE: Return trusted auth URL
    FE->>ATL: Redirect user to Atlassian login and consent
    ATL-->>BE: Return callback with auth code and state
    BE->>AMS: Proxy callback to complete MCP authentication
    AMS->>ATL: Exchange code for Atlassian access
    ATL-->>AMS: Authenticated Atlassian session context
    AMS-->>BE: Set MCP session cookie
    BE-->>FE: Set server-scoped session cookie and confirm plugin connection

    Note over User,BE: 2. Chat request with Atlassian plugin enabled
    User->>FE: Send Jira or Confluence related prompt
    FE->>BE: Send chat request with plugin enabled and session cookie
    BE->>BE: Resolve session, prepare model context, and decide Atlassian tool use

    Note over BE,AMS: 3. Tool execution through MCP
    BE->>AMS: Run Atlassian tool with resolved user session
    AMS->>AMS: Filter available Jira or Confluence tools for this authenticated context
    AMS->>ATL: Execute Atlassian operation
    ATL-->>AMS: Jira or Confluence result
    AMS-->>BE: MCP tool result

    Note over BE,User: 4. Response assembly
    BE->>BE: Combine tool result with model response generation
    BE-->>FE: Stream final answer
    FE-->>User: Render Atlassian-backed response
```

## Summary

- The frontend handles plugin enablement, OAuth initiation, chat input, and the MCP session cookie used for later Atlassian requests.
- The backend manages the MCP OAuth proxy flow, validates the returned auth URL, forwards the callback, renames the MCP cookie with the server ID, and coordinates Atlassian MCP execution.
- The Atlassian MCP server handles Atlassian authentication, exposes Jira and Confluence tools based on the authenticated context, executes Atlassian operations, and returns results to Function AI.
- Atlassian APIs provide the underlying Jira and Confluence data used during tool execution.

## Security Boundary

- This Function AI integration follows the generic OAuth-based MCP flow for non-PAT servers. Unlike the GitHub plugin flow, it does not depend on Azure Key Vault or backend PAT storage in the normal request path.
- After connection, the frontend and backend rely on a server-scoped MCP session cookie rather than re-sending raw Atlassian credentials on each request.
- The backend validates the returned authorization URL before redirecting the user and isolates MCP cookies by server name.
- Jira and Confluence data returned by tools can flow into backend response generation and the final user response.
*** End Patch