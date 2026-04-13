"""Main FastMCP server setup for Atlassian integration."""

import base64
import json
import logging
import os
from http.cookies import CookieError, SimpleCookie
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional
from urllib.parse import urlparse

import anyio
import requests
from cachetools import TTLCache
from fastmcp import FastMCP
from fastmcp import settings as fastmcp_settings
from fastmcp.server.event_store import EventStore
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools import Tool as FastMCPTool
from mcp.types import Tool as MCPTool
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mcp_atlassian.confluence import ConfluenceFetcher
from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.config import JiraConfig
from mcp_atlassian.utils.env import is_env_truthy
from mcp_atlassian.utils.environment import get_available_services
from mcp_atlassian.utils.io import is_read_only_mode
from mcp_atlassian.utils.logging import mask_sensitive
from mcp_atlassian.utils.oauth import (
    CLOUD_AUTHORIZE_URL,
    CLOUD_TOKEN_URL,
    DC_AUTHORIZE_PATH,
    DC_TOKEN_PATH,
)
from mcp_atlassian.utils.token_verifier import AtlassianOpaqueTokenVerifier
from mcp_atlassian.utils.tools import get_enabled_tools, should_include_tool
from mcp_atlassian.utils.toolsets import (
    get_enabled_toolsets,
    should_include_tool_by_toolset,
)
from mcp_atlassian.utils.urls import is_atlassian_cloud_url, validate_url_for_ssrf

from .client_storage import build_oauth_client_storage_from_env
from .confluence import confluence_mcp
from .context import MainAppContext
from .frontend_resource import (
    build_confluence_fetcher_for_request,
    build_frontend_payload,
    build_frontend_resource_script,
    build_jira_fetcher_for_request,
)
from .functionai_oauth import SESSION_COOKIE_NAME, get_functionai_oauth_bridge
from .jira import jira_mcp
from .oauth_proxy import HardenedOAuthProxy, parse_env_list
from .resources import register_atlassian_resources

logger = logging.getLogger("mcp-atlassian.server.main")

DEFAULT_HOST = "0.0.0.0"  # noqa: S104
DEFAULT_ALLOWED_REDIRECT_URIS = [
    "http://localhost:*",
    "http://127.0.0.1:*",
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://chat.openai.com/connector_platform_oauth_redirect",
]
DEFAULT_ALLOWED_GRANT_TYPES = ["authorization_code", "refresh_token"]
OAUTH_PROXY_ENABLE_ENV = "ATLASSIAN_OAUTH_PROXY_ENABLE"


def _sanitize_schema_for_compatibility(tool: MCPTool) -> MCPTool:
    """Sanitize tool inputSchema for AI platform compatibility.

    Collapses simple nullable ``anyOf`` unions that Pydantic v2 generates
    for ``T | None`` into a plain ``{"type": T}`` property.  This fixes
    Vertex AI / Google ADK rejecting ``anyOf`` alongside ``default`` or
    ``description`` fields (issues #640, #733).

    The transform is intentionally conservative — it only flattens unions
    of exactly ``[{"type": <primitive>}, {"type": "null"}]`` so that
    complex / nested schemas are left untouched.

    Note: Only top-level ``properties`` are processed.  Nested schemas
    (e.g. ``items`` of arrays or ``properties`` of sub-objects) are not
    walked.  This is sufficient for current tool definitions; extend if
    nested ``anyOf`` patterns appear in the future.

    Args:
        tool: The MCP tool whose inputSchema will be sanitized in-place.

    Returns:
        The same MCPTool instance (mutated) for chaining convenience.
    """
    schema = tool.inputSchema
    if not schema or not isinstance(schema, dict):
        return tool

    properties = schema.get("properties")
    if not properties or not isinstance(properties, dict):
        return tool

    for _prop_name, prop_def in properties.items():
        if not isinstance(prop_def, dict):
            continue

        any_of = prop_def.get("anyOf")
        if not any_of or not isinstance(any_of, list):
            continue

        # Only flatten simple nullable unions: [{"type": T}, {"type": "null"}]
        non_null = [v for v in any_of if v != {"type": "null"}]
        null_present = any(v == {"type": "null"} for v in any_of)

        if null_present and len(non_null) == 1 and "type" in non_null[0]:
            # Collapse: pull the real type up, drop anyOf
            resolved_type = non_null[0]["type"]
            prop_def.pop("anyOf")
            prop_def["type"] = resolved_type

    return tool


async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@asynccontextmanager
async def main_lifespan(app: FastMCP[MainAppContext]) -> AsyncIterator[dict[str, Any]]:
    logger.info("Main Atlassian MCP server lifespan starting...")
    services = get_available_services()
    read_only = is_read_only_mode()
    enabled_tools = get_enabled_tools()
    enabled_toolsets = get_enabled_toolsets()

    loaded_jira_config: JiraConfig | None = None
    loaded_confluence_config: ConfluenceConfig | None = None

    if services.get("jira"):
        try:
            jira_config = JiraConfig.from_env()
            if jira_config.is_auth_configured():
                loaded_jira_config = jira_config
                logger.info(
                    "Jira configuration loaded and authentication is configured."
                )
            else:
                logger.warning(
                    "Jira URL found, but authentication is not fully configured. Jira tools will be unavailable."
                )
        except Exception as e:
            logger.error(f"Failed to load Jira configuration: {e}", exc_info=True)

    if services.get("confluence"):
        try:
            confluence_config = ConfluenceConfig.from_env()
            if confluence_config.is_auth_configured():
                loaded_confluence_config = confluence_config
                logger.info(
                    "Confluence configuration loaded and authentication is configured."
                )
            else:
                logger.warning(
                    "Confluence URL found, but authentication is not fully configured. Confluence tools will be unavailable."
                )
        except Exception as e:
            logger.error(f"Failed to load Confluence configuration: {e}", exc_info=True)

    app_context = MainAppContext(
        full_jira_config=loaded_jira_config,
        full_confluence_config=loaded_confluence_config,
        read_only=read_only,
        enabled_tools=enabled_tools,
        enabled_toolsets=enabled_toolsets,
    )
    logger.info(f"Read-only mode: {'ENABLED' if read_only else 'DISABLED'}")
    logger.info(f"Enabled tools filter: {enabled_tools or 'All tools enabled'}")
    logger.info(f"Enabled toolsets filter: {sorted(enabled_toolsets)}")

    try:
        yield {"app_lifespan_context": app_context}
    except Exception as e:
        logger.error(f"Error during lifespan: {e}", exc_info=True)
        raise
    finally:
        logger.info("Main Atlassian MCP server lifespan shutting down...")
        # Perform any necessary cleanup here
        try:
            # Close any open connections if needed
            if loaded_jira_config:
                logger.debug("Cleaning up Jira resources...")
            if loaded_confluence_config:
                logger.debug("Cleaning up Confluence resources...")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)
        logger.info("Main Atlassian MCP server lifespan shutdown complete.")


class AtlassianMCP(FastMCP[MainAppContext]):
    """Custom FastMCP server class for Atlassian integration with tool filtering."""

    _active_streamable_http_path: str | None = None

    @staticmethod
    def _normalize_http_path(path: str) -> str:
        normalized_path = path.strip()
        if not normalized_path:
            return "/"
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
        normalized_path = normalized_path.rstrip("/")
        return normalized_path or "/"

    def get_streamable_http_path(self) -> str:
        if self._active_streamable_http_path:
            return self._active_streamable_http_path
        return self._normalize_http_path(fastmcp_settings.streamable_http_path)

    async def _list_tools_mcp(self) -> list[MCPTool]:
        # Filter tools based on enabled_tools, read_only mode, and service configuration from the lifespan context.
        req_context = self._mcp_server.request_context
        if req_context is None or req_context.lifespan_context is None:
            logger.warning(
                "Lifespan context not available during _list_tools_mcp call."
            )
            return []

        lifespan_ctx_dict = req_context.lifespan_context
        app_lifespan_state: MainAppContext | None = (
            lifespan_ctx_dict.get("app_lifespan_context")
            if isinstance(lifespan_ctx_dict, dict)
            else None
        )
        read_only = (
            getattr(app_lifespan_state, "read_only", False)
            if app_lifespan_state
            else False
        )
        enabled_tools_filter = (
            getattr(app_lifespan_state, "enabled_tools", None)
            if app_lifespan_state
            else None
        )
        enabled_toolsets_filter: set[str] | None = (
            getattr(app_lifespan_state, "enabled_toolsets", None)
            if app_lifespan_state
            else None
        )

        header_based_services = {"jira": False, "confluence": False}
        request = getattr(req_context, "request", None)
        if request is not None:
            service_headers = getattr(request.state, "atlassian_service_headers", {})
            if service_headers:
                header_based_services = get_available_services(service_headers)
                logger.debug(
                    f"Header-based service availability: {header_based_services}"
                )

        logger.debug(
            f"_list_tools_mcp: read_only={read_only}, enabled_tools_filter={enabled_tools_filter}, header_services={header_based_services}"
        )

        all_tools: dict[str, FastMCPTool] = await self.get_tools()
        logger.debug(
            f"Aggregated {len(all_tools)} tools before filtering: {list(all_tools.keys())}"
        )

        filtered_tools: list[MCPTool] = []
        for registered_name, tool_obj in all_tools.items():
            tool_tags = tool_obj.tags

            if not should_include_tool_by_toolset(tool_tags, enabled_toolsets_filter):
                logger.debug(
                    f"Excluding tool '{registered_name}' (toolset not enabled)"
                )
                continue

            if not should_include_tool(registered_name, enabled_tools_filter):
                logger.debug(f"Excluding tool '{registered_name}' (not enabled)")
                continue

            if tool_obj and read_only and "write" in tool_tags:
                logger.debug(
                    f"Excluding tool '{registered_name}' due to read-only mode and 'write' tag"
                )
                continue

            # Exclude Jira/Confluence tools if config is not fully authenticated
            is_jira_tool = "jira" in tool_tags
            is_confluence_tool = "confluence" in tool_tags
            service_configured_and_available = True
            if app_lifespan_state:
                jira_available = (
                    app_lifespan_state.full_jira_config is not None
                ) or header_based_services.get("jira", False)
                confluence_available = (
                    app_lifespan_state.full_confluence_config is not None
                ) or header_based_services.get("confluence", False)

                if is_jira_tool and not jira_available:
                    logger.debug(
                        f"Excluding Jira tool '{registered_name}' as Jira configuration/authentication is incomplete and no header-based auth available."
                    )
                    service_configured_and_available = False
                if is_confluence_tool and not confluence_available:
                    logger.debug(
                        f"Excluding Confluence tool '{registered_name}' as Confluence configuration/authentication is incomplete and no header-based auth available."
                    )
                    service_configured_and_available = False
            elif is_jira_tool or is_confluence_tool:
                jira_available = header_based_services.get("jira", False)
                confluence_available = header_based_services.get("confluence", False)

                if is_jira_tool and not jira_available:
                    logger.debug(
                        f"Excluding Jira tool '{registered_name}' as no Jira authentication available."
                    )
                    service_configured_and_available = False
                if is_confluence_tool and not confluence_available:
                    logger.debug(
                        f"Excluding Confluence tool '{registered_name}' as no Confluence authentication available."
                    )
                    service_configured_and_available = False

            if not service_configured_and_available:
                continue

            mcp_tool = tool_obj.to_mcp_tool(name=registered_name)
            _sanitize_schema_for_compatibility(mcp_tool)
            filtered_tools.append(mcp_tool)

        logger.debug(
            f"_list_tools_mcp: Total tools after filtering: {len(filtered_tools)}"
        )
        return filtered_tools

    def http_app(
        self,
        path: str | None = None,
        middleware: list[Middleware] | None = None,
        json_response: bool | None = None,
        stateless_http: bool | None = None,
        transport: Literal["http", "streamable-http", "sse"] = "streamable-http",
        event_store: EventStore | None = None,
        retry_interval: int | None = None,
    ) -> StarletteWithLifespan:
        final_path = path
        if transport == "streamable-http":
            configured_path = path or fastmcp_settings.streamable_http_path
            final_path = self._normalize_http_path(configured_path)
            self._active_streamable_http_path = final_path

        user_token_mw = Middleware(UserTokenMiddleware, mcp_server_ref=self)
        final_middleware_list = [user_token_mw]
        if middleware:
            final_middleware_list.extend(middleware)
        app = super().http_app(
            path=final_path,
            middleware=final_middleware_list,
            json_response=json_response,
            stateless_http=stateless_http,
            transport=transport,
            event_store=event_store,
            retry_interval=retry_interval,
        )
        return app


token_validation_cache: TTLCache[
    int, tuple[bool, str | None, JiraFetcher | None, ConfluenceFetcher | None]
] = TTLCache(maxsize=100, ttl=300)


def _parse_filter_values(raw_value: Any) -> set[str]:
    if raw_value is None:
        return set()

    if isinstance(raw_value, str):
        values = raw_value.split(",")
    elif isinstance(raw_value, (list, tuple, set)):
        values = raw_value
    else:
        values = [raw_value]

    normalized: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text:
            normalized.add(text.upper())
    return normalized


def _apply_scope_state_from_session(state: dict[str, Any], session: Any) -> None:
    state["user_atlassian_token"] = session.access_token
    state["user_atlassian_auth_type"] = "oauth"
    state["user_atlassian_cloud_id"] = session.cloud_id
    state["user_selected_jira_project"] = getattr(
        session, "selected_jira_project", None
    )
    state["user_show_confluence"] = getattr(session, "show_confluence", True)


def _apply_request_state_from_session(request: Request, session: Any) -> None:
    request.state.user_atlassian_token = session.access_token
    request.state.user_atlassian_auth_type = "oauth"
    request.state.user_atlassian_cloud_id = session.cloud_id
    request.state.user_selected_jira_project = getattr(
        session, "selected_jira_project", None
    )
    request.state.user_show_confluence = getattr(session, "show_confluence", True)


def _resolve_bridge_session_from_request(
    request: Request,
    bridge: Any,
) -> tuple[str | None, Any | None]:
    session_id = bridge.get_session_id_from_request(request)
    auth_header = (request.headers.get("authorization") or "").strip()
    if session_id is None and auth_header.startswith("Bearer "):
        bearer_value = auth_header[7:].strip()
        session_id = bearer_value or None

    if not session_id:
        return None, None

    return session_id, bridge.resolve_session(session_id)


def _resolve_frontend_app_context(request: Request) -> MainAppContext:
    lifespan_state = getattr(request.app.state, "lifespan_context", {})
    app_context = (
        lifespan_state.get("app_lifespan_context")
        if isinstance(lifespan_state, dict)
        else None
    )

    if app_context is not None:
        return app_context

    loaded_jira_config = None
    loaded_confluence_config = None

    if get_available_services().get("jira"):
        try:
            candidate = JiraConfig.from_env()
            if candidate.is_auth_configured():
                loaded_jira_config = candidate
        except Exception:
            loaded_jira_config = None

    if get_available_services().get("confluence"):
        try:
            candidate = ConfluenceConfig.from_env()
            if candidate.is_auth_configured():
                loaded_confluence_config = candidate
        except Exception:
            loaded_confluence_config = None

    return MainAppContext(
        full_jira_config=loaded_jira_config,
        full_confluence_config=loaded_confluence_config,
        read_only=is_read_only_mode(),
        enabled_tools=get_enabled_tools(),
        enabled_toolsets=get_enabled_toolsets(),
    )


class UserTokenMiddleware:
    """ASGI-compliant middleware to extract Atlassian user tokens/credentials.

    Based on PR #700 by @isaacpalomero - fixes ASGI protocol violations that caused
    server crashes when MCP clients disconnect during HTTP requests.
    """

    def __init__(
        self, app: ASGIApp, mcp_server_ref: Optional["AtlassianMCP"] = None
    ) -> None:
        self.app = app
        self.mcp_server_ref = mcp_server_ref
        if not self.mcp_server_ref:
            logger.warning(
                "UserTokenMiddleware initialized without mcp_server_ref. "
                "Path matching for MCP endpoint might fail if settings are needed."
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Pass through non-HTTP requests directly per ASGI spec
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # According to ASGI spec, middleware should copy scope when modifying it
        scope_copy: Scope = dict(scope)

        # Ensure state exists in scope - this is where Starlette stores request state
        if "state" not in scope_copy:
            scope_copy["state"] = {}

        # Initialize default authentication state (only initialize fields that should always exist)
        # Note: user_atlassian_token and user_atlassian_auth_type are NOT initialized
        # They are only set when present, so hasattr() checks work correctly
        scope_copy["state"]["user_atlassian_email"] = None
        scope_copy["state"]["user_atlassian_cloud_id"] = None
        scope_copy["state"]["auth_validation_error"] = None

        logger.debug(
            f"UserTokenMiddleware: Processing {scope_copy.get('method', 'UNKNOWN')} "
            f"{scope_copy.get('path', 'UNKNOWN')}"
        )

        # Skip auth header processing if IGNORE_HEADER_AUTH is set
        # (useful for GCP Cloud Run / AWS ALB that inject Authorization headers)
        ignore_header_auth = is_env_truthy("IGNORE_HEADER_AUTH")

        # Only process authentication for our MCP endpoint
        if (
            not ignore_header_auth
            and self.mcp_server_ref
            and self._should_process_auth(scope_copy)
        ):
            self._process_authentication_headers(scope_copy)

        # Create wrapped send function to handle client disconnections gracefully
        async def safe_send(message: Message) -> None:
            try:
                await send(message)
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                # Client disconnected - log but don't propagate to avoid ASGI violations
                logger.debug(
                    f"Client disconnected during response: {type(e).__name__}: {e}"
                )
                # Don't re-raise - this prevents the ASGI protocol violation
                return
            except Exception:
                # Re-raise unexpected errors
                raise

        # Check for auth errors and return 401 before calling app
        auth_error = scope_copy["state"].get("auth_validation_error")
        if auth_error:
            logger.warning(f"Authentication failed: {auth_error}")
            await self._send_json_error_response(safe_send, 401, auth_error)
            return  # Don't call self.app - request is rejected

        # Call the next application with modified scope and safe send wrapper
        await self.app(scope_copy, receive, safe_send)

    async def _send_json_error_response(
        self, send: Send, status_code: int, error_message: str
    ) -> None:
        """Send a JSON error response via ASGI protocol.

        Args:
            send: ASGI send callable (should be safe_send wrapper).
            status_code: HTTP status code (e.g., 401).
            error_message: Error message to include in JSON body.
        """
        body = json.dumps({"error": error_message}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _should_process_auth(self, scope: Scope) -> bool:
        """Check if this request should be processed for authentication."""
        if not self.mcp_server_ref or scope.get("method") != "POST":
            return False

        try:
            mcp_path = self.mcp_server_ref.get_streamable_http_path()
            request_path = AtlassianMCP._normalize_http_path(scope.get("path", ""))
            return request_path == mcp_path
        except (AttributeError, ValueError) as e:
            logger.warning(f"Error checking auth path: {e}")
            return False

    def _process_authentication_headers(self, scope: Scope) -> None:
        """Process authentication headers and store in scope state."""
        try:
            # Parse headers from scope (headers are byte tuples per ASGI spec)
            headers = dict(scope.get("headers", []))
            auth_header = headers.get(b"authorization")
            cloud_id_header = headers.get(b"x-atlassian-cloud-id")
            cookie_header = headers.get(b"cookie")

            # Convert bytes to strings (ASGI headers are always bytes)
            auth_header_str = auth_header.decode("latin-1") if auth_header else None
            cloud_id_str = (
                cloud_id_header.decode("latin-1") if cloud_id_header else None
            )
            cookie_header_str = cookie_header.decode("latin-1") if cookie_header else None

            # Extract additional Atlassian service headers for service availability detection
            jira_token_header = headers.get(b"x-atlassian-jira-personal-token")
            jira_url_header = headers.get(b"x-atlassian-jira-url")
            confluence_token_header = headers.get(
                b"x-atlassian-confluence-personal-token"
            )
            confluence_url_header = headers.get(b"x-atlassian-confluence-url")

            # Convert service header bytes to strings
            jira_token_str = (
                jira_token_header.decode("latin-1") if jira_token_header else None
            )
            jira_url_str = (
                jira_url_header.decode("latin-1") if jira_url_header else None
            )
            confluence_token_str = (
                confluence_token_header.decode("latin-1")
                if confluence_token_header
                else None
            )
            confluence_url_str = (
                confluence_url_header.decode("latin-1")
                if confluence_url_header
                else None
            )

            # Validate URLs to prevent SSRF
            if jira_url_str:
                ssrf_error = validate_url_for_ssrf(jira_url_str)
                if ssrf_error:
                    scope["state"]["auth_validation_error"] = (
                        f"Forbidden: Invalid Jira URL - {ssrf_error}"
                    )
                    return

            if confluence_url_str:
                ssrf_error = validate_url_for_ssrf(confluence_url_str)
                if ssrf_error:
                    scope["state"]["auth_validation_error"] = (
                        f"Forbidden: Invalid Confluence URL - {ssrf_error}"
                    )
                    return

            # Build service headers dict
            service_headers = {}
            if jira_token_str:
                service_headers["X-Atlassian-Jira-Personal-Token"] = jira_token_str
            if jira_url_str:
                service_headers["X-Atlassian-Jira-Url"] = jira_url_str
            if confluence_token_str:
                service_headers["X-Atlassian-Confluence-Personal-Token"] = (
                    confluence_token_str
                )
            if confluence_url_str:
                service_headers["X-Atlassian-Confluence-Url"] = confluence_url_str

            scope["state"]["atlassian_service_headers"] = service_headers
            if service_headers:
                logger.debug(
                    f"UserTokenMiddleware: Extracted service headers: {list(service_headers.keys())}"
                )

            session_id_str = None
            if cookie_header_str:
                session_id_str = self._get_mcp_session_id_from_cookie(cookie_header_str)
                if session_id_str:
                    logger.debug(
                        "UserTokenMiddleware: MCP session cookie found"
                    )

            logger.debug(
                f"UserTokenMiddleware: Processing auth for {scope.get('path')}, "
                f"AuthHeader present: {bool(auth_header_str)}, "
                f"CloudId present: {bool(cloud_id_str)}"
            )

            # Process Cloud ID
            if cloud_id_str and cloud_id_str.strip():
                scope["state"]["user_atlassian_cloud_id"] = cloud_id_str.strip()
                logger.debug(
                    f"UserTokenMiddleware: Extracted cloudId: {cloud_id_str.strip()}"
                )

            # Process Authorization header
            if auth_header_str:
                self._parse_auth_header(auth_header_str, scope)
            elif session_id_str:
                bridge = get_functionai_oauth_bridge()
                if bridge is None:
                    logger.debug(
                        "UserTokenMiddleware: Ignoring MCP session header because FunctionAI OAuth bridge is disabled"
                    )
                else:
                    session = bridge.resolve_session(session_id_str)
                    if session is None:
                        scope["state"]["auth_validation_error"] = (
                            "Unauthorized: Invalid or expired MCP session"
                        )
                        return
                    _apply_scope_state_from_session(scope["state"], session)
                    logger.debug(
                        "UserTokenMiddleware: Resolved OAuth token from MCP session cookie"
                    )
            else:
                logger.debug("UserTokenMiddleware: No Authorization header provided")
                # If service headers are present without Authorization header, set PAT auth type
                if service_headers and (
                    (jira_token_str and jira_url_str)
                    or (confluence_token_str and confluence_url_str)
                ):
                    scope["state"]["user_atlassian_auth_type"] = "pat"
                    scope["state"]["user_atlassian_email"] = None
                    logger.debug(
                        "UserTokenMiddleware: Header-based authentication detected. Setting PAT auth type."
                    )

        except Exception as e:
            logger.error(f"Error processing authentication headers: {e}", exc_info=True)
            scope["state"]["auth_validation_error"] = "Authentication processing error"

    @staticmethod
    def _get_mcp_session_id_from_cookie(cookie_header: str | None) -> str | None:
        if not cookie_header:
            return None

        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except CookieError:
            return None

        morsel = cookie.get(SESSION_COOKIE_NAME)
        if morsel is None:
            return None

        value = morsel.value.strip()
        return value or None

    def _parse_auth_header(self, auth_header: str, scope: Scope) -> None:
        """Parse the Authorization header and store credentials in scope state."""
        # Check prefix BEFORE stripping to preserve "Bearer " / "Token " matching
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()  # Remove "Bearer " prefix and strip token
            if not token:
                scope["state"]["auth_validation_error"] = (
                    "Unauthorized: Empty Bearer token"
                )
            else:
                bridge = get_functionai_oauth_bridge()
                session = bridge.resolve_session(token) if bridge is not None else None
                if session is not None:
                    _apply_scope_state_from_session(scope["state"], session)
                    logger.debug(
                        "UserTokenMiddleware: Resolved OAuth token from Bearer session ID"
                    )
                else:
                    scope["state"]["user_atlassian_token"] = token
                    scope["state"]["user_atlassian_auth_type"] = "oauth"
                    logger.debug(
                        "UserTokenMiddleware: Bearer token extracted (masked): "
                        f"...{mask_sensitive(token, 8)}"
                    )

        elif auth_header.startswith("Token "):
            token = auth_header[6:].strip()  # Remove "Token " prefix and strip token
            if not token:
                scope["state"]["auth_validation_error"] = (
                    "Unauthorized: Empty Token (PAT)"
                )
            else:
                scope["state"]["user_atlassian_token"] = token
                scope["state"]["user_atlassian_auth_type"] = "pat"
                logger.debug(
                    "UserTokenMiddleware: PAT token extracted (masked): "
                    f"...{mask_sensitive(token, 8)}"
                )

        elif auth_header.startswith("Basic "):
            encoded = auth_header[6:].strip()
            if not encoded:
                scope["state"]["auth_validation_error"] = (
                    "Unauthorized: Empty Basic auth credentials"
                )
                return
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
            except (ValueError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to decode Basic auth: {e}")
                scope["state"]["auth_validation_error"] = (
                    "Unauthorized: Invalid Basic auth encoding"
                )
                return
            if ":" not in decoded:
                scope["state"]["auth_validation_error"] = (
                    "Unauthorized: Invalid Basic auth format. "
                    "Expected 'email:api_token'"
                )
                return
            email, api_token = decoded.split(":", 1)
            if not email or not api_token:
                scope["state"]["auth_validation_error"] = (
                    "Unauthorized: Email or API token is empty"
                )
                return
            scope["state"]["user_atlassian_email"] = email
            scope["state"]["user_atlassian_api_token"] = api_token
            scope["state"]["user_atlassian_auth_type"] = "basic"
            scope["state"]["user_atlassian_token"] = None
            logger.debug(
                f"UserTokenMiddleware: Basic auth extracted for email: {email}"
            )

        elif auth_header.strip():
            # Non-empty but unsupported auth type
            auth_value = auth_header.strip()
            auth_type = auth_value.split(" ", 1)[0] if " " in auth_value else auth_value
            logger.warning(f"Unsupported Authorization type: {auth_type}")
            scope["state"]["auth_validation_error"] = (
                "Unauthorized: Only 'Bearer <OAuthToken>', "
                "'Token <PAT>', or 'Basic <base64(email:api_token)>' "
                "types are supported."
            )
        else:
            # Empty or whitespace-only
            scope["state"]["auth_validation_error"] = (
                "Unauthorized: Empty Authorization header"
            )


def _get_allowed_redirect_uris() -> list[str] | None:
    raw = os.getenv("ATLASSIAN_OAUTH_ALLOWED_CLIENT_REDIRECT_URIS")
    parsed = parse_env_list(raw)
    if parsed is None:
        return DEFAULT_ALLOWED_REDIRECT_URIS
    return parsed


def _get_allowed_grant_types() -> list[str]:
    raw = os.getenv("ATLASSIAN_OAUTH_ALLOWED_GRANT_TYPES")
    parsed = parse_env_list(raw)
    if parsed is None:
        return DEFAULT_ALLOWED_GRANT_TYPES
    if not parsed:
        logger.warning(
            "ATLASSIAN_OAUTH_ALLOWED_GRANT_TYPES is empty; defaulting to %s",
            DEFAULT_ALLOWED_GRANT_TYPES,
        )
        return DEFAULT_ALLOWED_GRANT_TYPES
    return parsed


def _resolve_upstream_oauth_endpoints(instance_url: str) -> tuple[str, str]:
    parsed_host = (urlparse(instance_url).hostname or "").lower()
    is_cloud = (
        is_atlassian_cloud_url(instance_url) or parsed_host == "auth.atlassian.com"
    )

    if is_cloud:
        return CLOUD_AUTHORIZE_URL, CLOUD_TOKEN_URL

    base_url = instance_url.rstrip("/")
    return f"{base_url}{DC_AUTHORIZE_PATH}", f"{base_url}{DC_TOKEN_PATH}"


def _is_cloud_instance(instance_url: str) -> bool:
    parsed_host = (urlparse(instance_url).hostname or "").lower()
    return is_atlassian_cloud_url(instance_url) or parsed_host == "auth.atlassian.com"


def _is_functionai_redirect_uri(redirect_uri: str | None) -> bool:
    if not redirect_uri:
        return False

    callback_path = (urlparse(redirect_uri).path or "").rstrip("/")
    return callback_path in {"/mcp/auth/callback", "/api/mcp/auth/callback"}


def _build_auth_provider() -> HardenedOAuthProxy | None:
    """Create an opt-in OAuth proxy auth provider with DCR + discovery support."""
    if not is_env_truthy(OAUTH_PROXY_ENABLE_ENV, "false"):
        logger.info(
            "OAuth proxy auth provider disabled; set %s=true to enable DCR/proxy routes.",
            OAUTH_PROXY_ENABLE_ENV,
        )
        return None

    instance_url = (
        os.getenv("ATLASSIAN_OAUTH_INSTANCE_URL")
        or os.getenv("JIRA_URL")
        or os.getenv("CONFLUENCE_URL")
    )
    client_id = (
        os.getenv("ATLASSIAN_OAUTH_CLIENT_ID")
        or os.getenv("JIRA_OAUTH_CLIENT_ID")
        or os.getenv("CONFLUENCE_OAUTH_CLIENT_ID")
    )
    client_secret = (
        os.getenv("ATLASSIAN_OAUTH_CLIENT_SECRET")
        or os.getenv("JIRA_OAUTH_CLIENT_SECRET")
        or os.getenv("CONFLUENCE_OAUTH_CLIENT_SECRET")
    )
    redirect_uri = os.getenv("ATLASSIAN_OAUTH_REDIRECT_URI")
    scope_env = os.getenv("ATLASSIAN_OAUTH_SCOPE", "")

    if _is_functionai_redirect_uri(redirect_uri):
        logger.info(
            "Detected FunctionAI OAuth callback; disabling MCP protocol auth provider and relying on FunctionAI session cookies for MCP requests."
        )
        return None

    if not all([instance_url, client_id, client_secret, redirect_uri]):
        logger.warning(
            "OAuth proxy requested but required vars are missing. "
            "Need instance URL + client credentials + redirect URI."
        )
        return None

    scopes = [s for part in scope_env.replace(",", " ").split() if (s := part)]
    is_cloud = _is_cloud_instance(instance_url)
    upstream_authorize, upstream_token = _resolve_upstream_oauth_endpoints(instance_url)

    parsed_redirect = urlparse(redirect_uri)
    raw_redirect_path = parsed_redirect.path or "/callback"

    base_url = os.getenv("PUBLIC_BASE_URL")
    if not base_url and parsed_redirect.scheme and parsed_redirect.netloc:
        redirect_dir = raw_redirect_path.rsplit("/", 1)[0]
        base_url = f"{parsed_redirect.scheme}://{parsed_redirect.netloc}{redirect_dir}"

    if not base_url:
        host = os.getenv("HOST", DEFAULT_HOST)
        port = os.getenv("PORT", "3000")
        host_for_url = "localhost" if host in (DEFAULT_HOST, "127.0.0.1") else host
        base_url = f"http://{host_for_url}:{port}"

    base_path = urlparse(base_url).path.rstrip("/")
    redirect_path = raw_redirect_path
    if base_path:
        if redirect_path == base_path:
            redirect_path = "/"
        elif redirect_path.startswith(base_path + "/"):
            redirect_path = redirect_path[len(base_path) :]

    if not redirect_path.startswith("/"):
        redirect_path = f"/{redirect_path}"

    allowed_client_redirect_uris = _get_allowed_redirect_uris()
    allowed_grant_types = _get_allowed_grant_types()
    require_consent = is_env_truthy("ATLASSIAN_OAUTH_REQUIRE_CONSENT", "true")
    verifier = AtlassianOpaqueTokenVerifier(required_scopes=scopes)

    return HardenedOAuthProxy(
        upstream_authorization_endpoint=upstream_authorize,
        upstream_token_endpoint=upstream_token,
        upstream_client_id=client_id,
        upstream_client_secret=client_secret,
        token_verifier=verifier,
        base_url=base_url,
        redirect_path=redirect_path,
        allowed_client_redirect_uris=allowed_client_redirect_uris,
        valid_scopes=scopes or None,
        allowed_grant_types=allowed_grant_types,
        forced_scopes=scopes or None,
        token_endpoint_auth_method="client_secret_post",  # noqa: S106
        extra_authorize_params=(
            {"audience": "api.atlassian.com", "prompt": "consent"} if is_cloud else None
        ),
        client_storage=build_oauth_client_storage_from_env(),
        require_authorization_consent=require_consent,
    )


main_mcp = AtlassianMCP(
    name="Atlassian MCP",
    lifespan=main_lifespan,
    auth=_build_auth_provider(),
)
main_mcp.mount(jira_mcp, "jira")
main_mcp.mount(confluence_mcp, "confluence")
register_atlassian_resources(main_mcp)


@main_mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
async def _health_check_route(request: Request) -> JSONResponse:
    return await health_check(request)


@main_mcp.custom_route("/api/auth/initiate", methods=["POST"], include_in_schema=False)
async def _functionai_auth_initiate(request: Request) -> JSONResponse:
    bridge = get_functionai_oauth_bridge()
    if bridge is None:
        return JSONResponse(
            {"error": "Atlassian OAuth compatibility bridge is not configured"},
            status_code=503,
        )

    user_id: str | None = None
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            raw_user_id = payload.get("user_id")
            if raw_user_id is not None:
                user_id = str(raw_user_id).strip() or None
    except json.JSONDecodeError:
        user_id = None

    auth_url = bridge.build_initiate_url(request, user_id)
    return JSONResponse({"auth_url": auth_url})


@main_mcp.custom_route("/api/auth/authorize", methods=["GET"], include_in_schema=False)
async def _functionai_auth_authorize(request: Request) -> JSONResponse:
    bridge = get_functionai_oauth_bridge()
    if bridge is None:
        return JSONResponse(
            {"error": "Atlassian OAuth compatibility bridge is not configured"},
            status_code=503,
        )

    state = (request.query_params.get("state") or "").strip()
    if not state:
        return JSONResponse({"error": "Missing OAuth state"}, status_code=400)

    try:
        authorization_url = bridge.build_authorization_url(request, state)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return RedirectResponse(url=authorization_url, status_code=307)


@main_mcp.custom_route(
    "/api/auth/atlassian-callback", methods=["GET"], include_in_schema=False
)
async def _functionai_auth_atlassian_callback(request: Request) -> JSONResponse:
    bridge = get_functionai_oauth_bridge()
    if bridge is None:
        return JSONResponse(
            {"error": "Atlassian OAuth compatibility bridge is not configured"},
            status_code=503,
        )

    code = (request.query_params.get("code") or "").strip()
    state = (request.query_params.get("state") or "").strip()
    if not code or not state:
        return JSONResponse(
            {"error": "Missing OAuth callback parameters"},
            status_code=400,
        )

    return RedirectResponse(
        url=bridge.build_functionai_callback_url(code, state),
        status_code=307,
    )


@main_mcp.custom_route("/api/auth/callback", methods=["GET"], include_in_schema=False)
async def _functionai_auth_callback(request: Request) -> JSONResponse:
    bridge = get_functionai_oauth_bridge()
    if bridge is None:
        return JSONResponse(
            {"error": "Atlassian OAuth compatibility bridge is not configured"},
            status_code=503,
        )

    code = (request.query_params.get("code") or "").strip()
    state = (request.query_params.get("state") or "").strip()
    user_id = (request.query_params.get("user_id") or "").strip() or None

    if not code or not state:
        return JSONResponse(
            {"error": "Missing OAuth callback parameters"},
            status_code=400,
        )

    try:
        session = await anyio.to_thread.run_sync(
            lambda: bridge.complete_authorization(
                code=code,
                state=state,
                user_id=user_id,
                request=request,
            )
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except requests.HTTPError as exc:
        logger.error("FunctionAI OAuth callback failed: %s", exc, exc_info=True)
        return JSONResponse({"error": "OAuth token exchange failed"}, status_code=502)

    response = JSONResponse({"success": True})
    bridge.attach_session_cookie(response, request, session)
    return response


@main_mcp.custom_route("/api/auth/refresh", methods=["POST"], include_in_schema=False)
async def _functionai_auth_refresh(request: Request) -> JSONResponse:
    bridge = get_functionai_oauth_bridge()
    if bridge is None:
        return JSONResponse(
            {"error": "Atlassian OAuth compatibility bridge is not configured"},
            status_code=503,
        )

    session_id = bridge.get_session_id_from_request(request)
    if not session_id:
        return JSONResponse({"error": "Missing MCP session cookie"}, status_code=401)

    try:
        session = await anyio.to_thread.run_sync(bridge.refresh_session, session_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except requests.HTTPError as exc:
        logger.error("FunctionAI OAuth refresh failed: %s", exc, exc_info=True)
        return JSONResponse({"error": "OAuth token refresh failed"}, status_code=502)

    response = JSONResponse({"success": True})
    bridge.attach_session_cookie(response, request, session)
    return response


@main_mcp.custom_route(
    "/api/frontend/preferences", methods=["POST"], include_in_schema=False
)
async def _frontend_preferences(request: Request) -> JSONResponse:
    bridge = get_functionai_oauth_bridge()
    if bridge is None:
        return JSONResponse(
            {"error": "Atlassian OAuth compatibility bridge is not configured"},
            status_code=503,
        )

    session_id, session = _resolve_bridge_session_from_request(request, bridge)
    if session_id is None or session is None:
        return JSONResponse(
            {"error": "Invalid or expired MCP session"},
            status_code=401,
        )

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        payload = {}

    if not isinstance(payload, dict):
        return JSONResponse(
            {"error": "Preferences payload must be a JSON object"},
            status_code=400,
        )

    selected_project_supplied = "selected_jira_project" in payload
    show_confluence_supplied = "show_confluence" in payload
    selected_project = payload.get("selected_jira_project")

    if (
        not selected_project_supplied
        and not show_confluence_supplied
    ):
        return JSONResponse(
            {"error": "No frontend preferences were provided"},
            status_code=400,
        )

    app_context = _resolve_frontend_app_context(request)
    jira_config = getattr(app_context, "full_jira_config", None)
    allowed_projects = _parse_filter_values(
        getattr(jira_config, "projects_filter", None) if jira_config else None
    )
    if selected_project_supplied and selected_project not in (None, ""):
        normalized_project = str(selected_project).strip().upper()
        if allowed_projects and normalized_project not in allowed_projects:
            return JSONResponse(
                {
                    "error": (
                        f"Project '{normalized_project}' is outside the configured Jira project filter"
                    )
                },
                status_code=400,
            )

    try:
        preference_updates: dict[str, Any] = {}
        if selected_project_supplied:
            preference_updates["selected_jira_project"] = selected_project
        if show_confluence_supplied:
            preference_updates["show_confluence"] = payload.get("show_confluence")

        updated_session = await anyio.to_thread.run_sync(
            lambda: bridge.update_session_preferences(session_id, **preference_updates)
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    return JSONResponse(
        {
            "selected_jira_project": updated_session.selected_jira_project,
            "show_confluence": updated_session.show_confluence,
        }
    )


@main_mcp.custom_route(
    "/api/resources/frontend", methods=["GET"], include_in_schema=False
)
async def _frontend_resource(request: Request) -> PlainTextResponse:
    bridge = get_functionai_oauth_bridge()
    session = None
    if bridge is not None and not getattr(request.state, "user_atlassian_token", None):
        session_id, session = _resolve_bridge_session_from_request(request, bridge)
        auth_header = (request.headers.get("authorization") or "").strip()
        logger.info(
            "Frontend resource requested (has_cookie=%s, has_auth_header=%s)",
            bool(session_id),
            auth_header.startswith("Bearer "),
        )
        if session_id:
            if session is None:
                logger.warning(
                    "Frontend resource denied: MCP session could not be resolved"
                )
                return PlainTextResponse(
                    "Invalid or expired MCP session",
                    status_code=401,
                )
            _apply_request_state_from_session(request, session)
            logger.info(
                "Frontend resource resolved Atlassian MCP session for cloud_id=%s",
                session.cloud_id or "missing",
            )
        else:
            logger.info(
                "Frontend resource falling back to unauthenticated payload"
            )

    theme = (request.query_params.get("theme") or "light").strip().lower()
    if theme not in {"light", "dark"}:
        theme = "light"

    app_context = _resolve_frontend_app_context(request)

    jira_fetcher = None
    confluence_fetcher = None

    jira_config = getattr(app_context, "full_jira_config", None) if app_context else None
    if jira_config is not None:
        try:
            jira_fetcher = build_jira_fetcher_for_request(request, jira_config)
        except ValueError:
            if jira_config.auth_type != "oauth":
                jira_fetcher = JiraFetcher(config=jira_config)

    confluence_config = (
        getattr(app_context, "full_confluence_config", None) if app_context else None
    )
    if confluence_config is not None:
        try:
            confluence_fetcher = build_confluence_fetcher_for_request(
                request,
                confluence_config,
            )
        except ValueError:
            if confluence_config.auth_type != "oauth":
                confluence_fetcher = ConfluenceFetcher(config=confluence_config)

    selected_project = getattr(
        request.state,
        "user_selected_jira_project",
        getattr(session, "selected_jira_project", None) if session else None,
    )
    show_confluence = getattr(
        request.state,
        "user_show_confluence",
        getattr(session, "show_confluence", True) if session else True,
    )
    payload = build_frontend_payload(
        jira_fetcher,
        confluence_fetcher,
        selected_jira_project=selected_project,
        show_confluence=show_confluence,
        preferences_supported=session is not None,
    )
    script = build_frontend_resource_script(payload, theme)
    return PlainTextResponse(script, media_type="application/javascript")


logger.info("Added /healthz endpoint for Kubernetes probes")
