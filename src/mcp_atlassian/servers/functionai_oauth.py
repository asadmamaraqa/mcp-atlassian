"""FunctionAI-compatible OAuth compatibility layer for HTTP MCP deployments."""

from __future__ import annotations

import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from cachetools import TTLCache
from starlette.requests import Request
from starlette.responses import Response

from mcp_atlassian.utils.oauth import CLOUD_ID_URL, HTTP_TIMEOUT, OAuthConfig
from mcp_atlassian.utils.urls import is_atlassian_cloud_url

logger = logging.getLogger("mcp-atlassian.server.functionai_oauth")

SESSION_COOKIE_NAME = "mcp-session-id"
STATE_TTL_SECONDS = 600
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_PROJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_PREFERENCE_UNSET = object()


def _mask_session_id(session_id: str | None) -> str:
    if not session_id:
        return "missing"
    if len(session_id) <= 8:
        return session_id
    return f"{session_id[:4]}...{session_id[-4:]}"


@dataclass(frozen=True)
class FunctionAIOAuthConfig:
    """Configuration needed for the FunctionAI OAuth compatibility layer."""

    instance_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scope: str
    cloud_id: str | None = None
    base_url: str | None = None
    public_base_url: str | None = None

    @property
    def is_data_center(self) -> bool:
        if self.base_url:
            return True
        return not is_atlassian_cloud_url(self.instance_url)

    def build_oauth_config(
        self,
        *,
        redirect_uri: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: float | None = None,
        cloud_id: str | None = None,
    ) -> OAuthConfig:
        return OAuthConfig(
            client_id=self.client_id,
            client_secret=self.client_secret,
            redirect_uri=redirect_uri or self.redirect_uri,
            scope=self.scope,
            cloud_id=cloud_id if not self.is_data_center else None,
            base_url=self.base_url,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )


@dataclass(frozen=True)
class PendingAuthorization:
    """Tracks an in-flight OAuth authorization request."""

    user_id: str | None
    created_at: float


@dataclass
class OAuthSession:
    """Stores per-user Atlassian OAuth session state for MCP requests."""

    session_id: str
    access_token: str
    refresh_token: str | None
    expires_at: float | None
    cloud_id: str | None
    user_id: str | None
    created_at: float
    selected_jira_project: str | None = None
    show_confluence: bool = True

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and time.time() >= self.expires_at)

    @property
    def should_refresh(self) -> bool:
        return bool(self.expires_at and (self.expires_at - time.time()) <= 300)


def _resolve_instance_url() -> str | None:
    url = (
        os.getenv("ATLASSIAN_OAUTH_INSTANCE_URL")
        or os.getenv("JIRA_URL")
        or os.getenv("CONFLUENCE_URL")
    )
    if url:
        return url

    # When no instance URL is set but OAuth credentials are present, assume Cloud
    # mode. The user picks their Atlassian site during the OAuth consent screen and
    # the cloud_id is resolved dynamically from accessible-resources after login.
    if os.getenv("ATLASSIAN_OAUTH_CLIENT_ID") and os.getenv("ATLASSIAN_OAUTH_CLIENT_SECRET"):
        return "https://cloud.atlassian.net"

    return None


def _load_config_from_env() -> FunctionAIOAuthConfig | None:
    instance_url = _resolve_instance_url()
    if not instance_url:
        return None

    oauth_config = OAuthConfig.from_env(service_url=instance_url)
    if not oauth_config:
        return None

    if not oauth_config.client_id or not oauth_config.client_secret:
        return None

    if not oauth_config.redirect_uri:
        return None

    if not oauth_config.is_data_center and not oauth_config.scope:
        return None

    return FunctionAIOAuthConfig(
        instance_url=instance_url,
        client_id=oauth_config.client_id,
        client_secret=oauth_config.client_secret,
        redirect_uri=oauth_config.redirect_uri,
        scope=oauth_config.scope,
        cloud_id=oauth_config.cloud_id,
        base_url=oauth_config.base_url,
        public_base_url=os.getenv("PUBLIC_BASE_URL"),
    )


def _env_signature() -> tuple[str | None, ...]:
    return (
        os.getenv("ATLASSIAN_OAUTH_INSTANCE_URL"),
        os.getenv("JIRA_URL"),
        os.getenv("CONFLUENCE_URL"),
        os.getenv("ATLASSIAN_OAUTH_CLIENT_ID"),
        os.getenv("ATLASSIAN_OAUTH_CLIENT_SECRET"),
        os.getenv("ATLASSIAN_OAUTH_REDIRECT_URI"),
        os.getenv("ATLASSIAN_OAUTH_SCOPE"),
        os.getenv("ATLASSIAN_OAUTH_CLOUD_ID"),
        os.getenv("PUBLIC_BASE_URL"),
        os.getenv("JIRA_OAUTH_CLIENT_ID"),
        os.getenv("JIRA_OAUTH_CLIENT_SECRET"),
        os.getenv("JIRA_OAUTH_REDIRECT_URI"),
        os.getenv("JIRA_OAUTH_SCOPE"),
        os.getenv("CONFLUENCE_OAUTH_CLIENT_ID"),
        os.getenv("CONFLUENCE_OAUTH_CLIENT_SECRET"),
        os.getenv("CONFLUENCE_OAUTH_REDIRECT_URI"),
        os.getenv("CONFLUENCE_OAUTH_SCOPE"),
    )


class FunctionAIOAuthBridge:
    """Server-side compatibility shim for FunctionAI's MCP auth contract."""

    def __init__(self, config: FunctionAIOAuthConfig) -> None:
        self.config = config
        self.pending_authorizations: TTLCache[str, PendingAuthorization] = TTLCache(
            maxsize=1024,
            ttl=STATE_TTL_SECONDS,
        )
        self.sessions: TTLCache[str, OAuthSession] = TTLCache(
            maxsize=1024,
            ttl=SESSION_TTL_SECONDS,
        )

    def build_initiate_url(self, request: Request, user_id: str | None) -> str:
        state = secrets.token_urlsafe(32)
        self.pending_authorizations[state] = PendingAuthorization(
            user_id=user_id,
            created_at=time.time(),
        )
        logger.info(
            "Created pending Atlassian OAuth authorization for user '%s'",
            user_id or "anonymous",
        )
        # Always derive from request so the auth URL host matches whatever host
        # the caller (e.g., FunctionAI backend) used to reach this server.
        # This ensures the is_same_host trust check passes without requiring
        # the caller to allowlist external OAuth domains.
        return _build_external_route_url(request, "/api/auth/authorize", state=state)

    def build_authorization_url(self, request: Request, state: str) -> str:
        pending = self.pending_authorizations.get(state)
        if pending is None:
            raise ValueError("Unknown or expired OAuth state")

        oauth_config = self.config.build_oauth_config(
            redirect_uri=self.build_browser_callback_url(request),
            cloud_id=self.config.cloud_id,
        )
        return oauth_config.get_authorization_url(state)

    def build_browser_callback_url(self, request: Request) -> str:
        if self.config.public_base_url:
            return _build_route_url_from_base(
                self.config.public_base_url,
                "/api/auth/atlassian-callback",
            )
        return _build_external_route_url(request, "/api/auth/atlassian-callback")

    def build_functionai_callback_url(self, code: str, state: str) -> str:
        parsed = urlparse(self.config.redirect_uri)
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        query_items.extend([("code", code), ("state", state)])
        return urlunparse(parsed._replace(query=urlencode(query_items, doseq=True)))

    def complete_authorization(
        self,
        *,
        code: str,
        state: str,
        user_id: str | None,
        request: Request,
    ) -> OAuthSession:
        pending = self.pending_authorizations.pop(state, None)
        if pending is None:
            raise ValueError("Unknown or expired OAuth state")

        if pending.user_id and user_id and pending.user_id != user_id:
            raise ValueError("OAuth state does not belong to the current user")

        token_data = self._exchange_code_for_tokens(
            code,
            redirect_uri=self.build_browser_callback_url(request),
        )
        access_token = str(token_data["access_token"])
        refresh_token = _coerce_optional_str(token_data.get("refresh_token"))
        expires_at = _extract_expiry(token_data)
        cloud_id = self._resolve_cloud_id(access_token)

        session_id = secrets.token_urlsafe(32)
        session = OAuthSession(
            session_id=session_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            cloud_id=cloud_id,
            user_id=user_id or pending.user_id,
            created_at=time.time(),
        )
        self.sessions[session_id] = session
        logger.info(
            "Created Atlassian MCP session %s for user '%s' (cloud_id=%s, refresh_token=%s, expires_at=%s)",
            _mask_session_id(session_id),
            session.user_id or "anonymous",
            session.cloud_id or "missing",
            bool(session.refresh_token),
            session.expires_at,
        )
        return session

    def resolve_session(self, session_id: str) -> OAuthSession | None:
        session = self.sessions.get(session_id)
        if session is None:
            logger.warning(
                "Failed to resolve Atlassian MCP session %s: not found in in-memory cache",
                _mask_session_id(session_id),
            )
            return None

        if session.should_refresh and session.refresh_token:
            logger.info(
                "Refreshing Atlassian MCP session %s during resolve",
                _mask_session_id(session_id),
            )
            try:
                session = self.refresh_session(session_id)
            except ValueError:
                logger.warning(
                    "Failed to refresh Atlassian MCP session %s during resolve",
                    _mask_session_id(session_id),
                )
                self.sessions.pop(session_id, None)
                return None

        if session.is_expired and not session.refresh_token:
            logger.warning(
                "Atlassian MCP session %s expired without refresh token; dropping from cache",
                _mask_session_id(session_id),
            )
            self.sessions.pop(session_id, None)
            return None

        logger.info(
            "Resolved Atlassian MCP session %s for user '%s'",
            _mask_session_id(session_id),
            session.user_id or "anonymous",
        )
        return session

    def refresh_session(self, session_id: str) -> OAuthSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError("Unknown MCP session")

        if not session.refresh_token:
            if session.is_expired:
                self.sessions.pop(session_id, None)
                raise ValueError("MCP session has expired")
            return session

        token_data = self._refresh_access_token(session.refresh_token)
        session.access_token = str(token_data["access_token"])
        session.refresh_token = _coerce_optional_str(
            token_data.get("refresh_token")
        ) or session.refresh_token
        session.expires_at = _extract_expiry(token_data)
        session.cloud_id = session.cloud_id or self._resolve_cloud_id(
            session.access_token
        )
        self.sessions[session_id] = session
        logger.info(
            "Refreshed Atlassian MCP session %s for user '%s' (expires_at=%s)",
            _mask_session_id(session_id),
            session.user_id or "anonymous",
            session.expires_at,
        )
        return session

    def get_session_preferences(self, session_id: str) -> dict[str, Any]:
        session = self.resolve_session(session_id)
        if session is None:
            raise ValueError("Unknown MCP session")

        return {
            "selected_jira_project": session.selected_jira_project,
            "show_confluence": session.show_confluence,
        }

    def update_session_preferences(
        self,
        session_id: str,
        *,
        selected_jira_project: Any = _PREFERENCE_UNSET,
        show_confluence: Any = _PREFERENCE_UNSET,
    ) -> OAuthSession:
        session = self.resolve_session(session_id)
        if session is None:
            raise ValueError("Unknown MCP session")

        if selected_jira_project is not _PREFERENCE_UNSET:
            session.selected_jira_project = _normalize_selected_jira_project(
                selected_jira_project
            )

        if show_confluence is not _PREFERENCE_UNSET:
            session.show_confluence = _coerce_bool(show_confluence)

        self.sessions[session_id] = session
        logger.info(
            "Updated Atlassian MCP session %s preferences for user '%s' (selected_jira_project=%s, show_confluence=%s)",
            _mask_session_id(session_id),
            session.user_id or "anonymous",
            session.selected_jira_project or "none",
            session.show_confluence,
        )
        return session

    def get_session_id_from_request(self, request: Request) -> str | None:
        cookie_header = request.headers.get("cookie")
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

    def attach_session_cookie(
        self,
        response: Response,
        request: Request,
        session: OAuthSession,
    ) -> None:
        secure = _should_use_secure_cookie(request, self.config.redirect_uri)
        same_site = "none" if secure else "lax"
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session.session_id,
            httponly=True,
            secure=secure,
            samesite=same_site,
            path="/",
            max_age=SESSION_TTL_SECONDS,
        )
        logger.info(
            "Attached Atlassian MCP session cookie for session %s (secure=%s, samesite=%s, redirect_uri=%s)",
            _mask_session_id(session.session_id),
            secure,
            same_site,
            self.config.redirect_uri,
        )

    def _exchange_code_for_tokens(
        self,
        code: str,
        *,
        redirect_uri: str,
    ) -> dict[str, Any]:
        oauth_config = self.config.build_oauth_config(
            redirect_uri=redirect_uri,
            cloud_id=self.config.cloud_id,
        )
        payload = {
            "grant_type": "authorization_code",
            "client_id": oauth_config.client_id,
            "client_secret": oauth_config.client_secret,
            "code": code,
            "redirect_uri": oauth_config.redirect_uri,
        }
        response = requests.post(
            oauth_config.token_url,
            data=payload,
            timeout=HTTP_TIMEOUT,
        )
        _raise_for_oauth_failure(response, "authorization code exchange")
        return response.json()

    def _refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        oauth_config = self.config.build_oauth_config(
            refresh_token=refresh_token,
            cloud_id=self.config.cloud_id,
        )
        payload = {
            "grant_type": "refresh_token",
            "client_id": oauth_config.client_id,
            "client_secret": oauth_config.client_secret,
            "refresh_token": refresh_token,
        }
        response = requests.post(
            oauth_config.token_url,
            data=payload,
            timeout=HTTP_TIMEOUT,
        )
        _raise_for_oauth_failure(response, "refresh token exchange")
        return response.json()

    def _resolve_cloud_id(self, access_token: str) -> str | None:
        if self.config.is_data_center:
            return None
        if self.config.cloud_id:
            return self.config.cloud_id

        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(CLOUD_ID_URL, headers=headers, timeout=HTTP_TIMEOUT)
        _raise_for_oauth_failure(response, "accessible resources lookup")
        resources = response.json()
        if isinstance(resources, list) and resources:
            resource = resources[0]
            if isinstance(resource, dict):
                return _coerce_optional_str(resource.get("id"))
        return None


_bridge_cache: tuple[tuple[str | None, ...], FunctionAIOAuthBridge | None] | None = None


def get_functionai_oauth_bridge() -> FunctionAIOAuthBridge | None:
    """Return a cached FunctionAI OAuth bridge for the current environment."""

    global _bridge_cache

    signature = _env_signature()
    if _bridge_cache is not None and _bridge_cache[0] == signature:
        return _bridge_cache[1]

    config = _load_config_from_env()
    bridge = FunctionAIOAuthBridge(config) if config else None
    _bridge_cache = (signature, bridge)
    return bridge


def reset_functionai_oauth_bridge_cache() -> None:
    """Reset the cached FunctionAI bridge instance for tests and env reloads."""

    global _bridge_cache
    _bridge_cache = None


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    value_str = str(value).strip()
    return value_str or None


def _normalize_selected_jira_project(value: Any) -> str | None:
    if value is None:
        return None

    value_str = str(value).strip()
    if not value_str:
        return None
    if "," in value_str:
        raise ValueError("Selected Jira project must be a single project key")

    normalized_value = value_str.upper()
    if not _PROJECT_KEY_PATTERN.fullmatch(normalized_value):
        raise ValueError("Selected Jira project contains unsupported characters")

    return normalized_value


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False

    raise ValueError("show_confluence must be a boolean")


def _extract_expiry(token_data: dict[str, Any]) -> float | None:
    expires_in = token_data.get("expires_in")
    if expires_in is None:
        return None
    try:
        return time.time() + int(expires_in)
    except (TypeError, ValueError):
        return None


def _raise_for_oauth_failure(response: requests.Response, action: str) -> None:
    if response.ok:
        return
    logger.error(
        "Atlassian OAuth %s failed with status %s: %s",
        action,
        response.status_code,
        response.text,
    )
    response.raise_for_status()


def _should_use_secure_cookie(request: Request, redirect_uri: str) -> bool:
    forwarded_proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    )
    if forwarded_proto:
        return forwarded_proto == "https"
    if request.url.scheme.lower() == "https":
        return True
    return redirect_uri.lower().startswith("https://")


def _build_external_route_url(
    request: Request,
    route_path: str,
    **query_params: str,
) -> str:
    route_path = route_path if route_path.startswith("/") else f"/{route_path}"
    forwarded_proto = (
        request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    )
    scheme = forwarded_proto or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    forwarded_prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")

    base_path = forwarded_prefix
    if not base_path:
        request_path = request.url.path
        if request_path.endswith(route_path):
            base_path = request_path[: -len(route_path)].rstrip("/")

    query = ""
    if query_params:
        query = f"?{urlencode(query_params)}"
    return f"{scheme}://{host}{base_path}{route_path}{query}"


def _build_route_url_from_base(
    base_url: str,
    route_path: str,
    **query_params: str,
) -> str:
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    route = route_path if route_path.startswith("/") else f"/{route_path}"
    query = ""
    if query_params:
        query = f"?{urlencode(query_params)}"
    return urlunparse(
        parsed._replace(
            path=f"{base_path}{route}",
            params="",
            query=query[1:] if query else "",
            fragment="",
        )
    )


__all__ = [
    "FunctionAIOAuthBridge",
    "OAuthSession",
    "SESSION_COOKIE_NAME",
    "get_functionai_oauth_bridge",
    "reset_functionai_oauth_bridge_cache",
]
