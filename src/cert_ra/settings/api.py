# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import binascii
import os
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Core application settings."""

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_app_", case_sensitive=False, extra="ignore"
    )

    url: str = "http://localhost:8000"
    """Frontend base URL."""
    debug: bool = False
    """Run in debug mode."""
    secret_key: str = Field(
        default_factory=lambda: binascii.hexlify(os.urandom(32)).decode(
            encoding="utf-8"
        )
    )
    """Application secret key. Should be set explicitly in production."""
    session_cookie_name: str = "session"
    """Session cookie name."""
    session_cookie_secure: bool = False
    """Require HTTPS for session cookies."""
    session_cookie_samesite: str = "lax"
    """SameSite policy for session cookies."""
    session_max_age: int = 3600
    """Session max age in seconds."""
    session_renew_on_access: bool = True
    """Renew session expiry on access."""
    name: str = "certora-risk-assessment"
    """Application name."""
    commit_sha: str = ""
    """Git commit the released build was built from. Set at build/deploy time
    (``CERT_RA_APP_COMMIT_SHA``) to the image's ``sha-<git_sha>``. Empty in a
    local checkout, where the About page falls back to a live ``git`` lookup."""
    allowed_cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    """Allowed CORS origins."""
    csrf_cookie_name: str = "XSRF-TOKEN"
    """CSRF cookie name."""
    csrf_header_name: str = "X-XSRF-TOKEN"
    """CSRF header name."""
    csrf_cookie_secure: bool = False
    """Require HTTPS for CSRF cookie."""
    csrf_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    )
    """Origins allowed on state-changing requests. The Origin / Referer
    header MUST exactly match one of these values, otherwise the
    OriginCheckMiddleware returns 403 (design #88-#90)."""
    github_oauth2_client_id: str = ""
    """GitHub OAuth2 client ID."""
    github_oauth2_client_secret: str = ""
    """GitHub OAuth2 client secret."""
    google_oauth2_client_id: str = ""
    """Google OAuth2 client ID."""
    google_oauth2_client_secret: str = ""
    """Google OAuth2 client secret."""
    microsoft_oauth2_client_id: str = ""
    """Microsoft Entra OAuth2 / OIDC client ID (multi-tenant app)."""
    microsoft_oauth2_client_secret: str = ""
    """Microsoft Entra OAuth2 / OIDC client secret."""
    must_verify_email: bool = False
    """Require email verification before login."""

    @property
    def slug(self) -> str:
        """Application name as a URL-safe slug."""
        return self.name.lower().replace(" ", "-")

    @property
    def github_oauth_enabled(self) -> bool:
        """True if GitHub OAuth credentials are configured."""
        return bool(self.github_oauth2_client_id and self.github_oauth2_client_secret)

    @property
    def google_oauth_enabled(self) -> bool:
        """True if Google OAuth credentials are configured."""
        return bool(self.google_oauth2_client_id and self.google_oauth2_client_secret)

    @property
    def microsoft_oauth_enabled(self) -> bool:
        """True if Microsoft Entra OAuth/OIDC credentials are configured."""
        return bool(
            self.microsoft_oauth2_client_id and self.microsoft_oauth2_client_secret
        )


class FeatureSettings(BaseSettings):
    """Feature flags gating not-yet-fully-rolled-out behaviour.

    Each flag ships its schema + admin code dark and is flipped per
    deployment once the flow is verified.
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_features_", case_sensitive=False, extra="ignore"
    )

    enforced_provider: bool = False
    """Gate per-team ``enforced_provider``. While False the enforcement
    self-migration flow is dormant: sign-in is never refused on policy
    grounds, the team enforcement UI is hidden, and the set/unset API
    returns 404. The column and admin-view code still ship."""


class ServerSettings(BaseSettings):
    """HTTP server settings."""

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_server_", case_sensitive=False, extra="ignore"
    )

    app_loc: str = "cert_ra.api.asgi:create_app"
    """Path to ASGI app factory."""
    host: str = "0.0.0.0"  # noqa: S104
    """Server network host."""
    port: int = 8000
    """Server port."""
    keepalive: int = 65
    """Seconds to hold connections open (65 is > AWS lb idle timeout)."""
    reload: bool = False
    """Enable hot reloading."""
    reload_dirs: list[str] = []
    """Directories to watch for hot reloading."""
    http_workers: int | None = None
    """Number of HTTP worker processes."""


class LogSettings(BaseSettings):
    """Logging configuration settings."""

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_log_", case_sensitive=False, extra="ignore"
    )

    exclude_paths: str = r"^/static/"
    """Regex to exclude paths from access logging."""
    http_event: str = "HTTP"
    """Log event name for HTTP handler logs."""
    include_compressed_body: bool = False
    """Include body of compressed responses in log output."""
    level: int = 10
    """Root log level (stdlib integer values)."""
    obfuscate_cookies: set[str] = {"session", "XSRF-TOKEN"}
    """Request cookie keys to obfuscate in logs."""
    obfuscate_headers: set[str] = {"Authorization", "X-API-KEY", "X-XSRF-TOKEN"}
    """Request header keys to obfuscate in logs."""
    request_fields: list[
        Literal[
            "path",
            "method",
            "content_type",
            "headers",
            "cookies",
            "query",
            "path_params",
            "body",
            "scheme",
            "client",
        ]
    ] = ["path", "method", "query", "path_params"]
    """Request attributes to include in access logs."""
    response_fields: list[Literal["status_code", "headers", "body", "cookies"]] = [
        "status_code"
    ]
    """Response attributes to include in access logs."""
    sqlalchemy_level: int = 20
    """Log level for SQLAlchemy loggers."""
    uvicorn_access_level: int = 20
    """Log level for uvicorn access logs."""
    uvicorn_error_level: int = 20
    """Log level for uvicorn error logs."""
    granian_access_level: int = 30
    """Log level for granian access logs."""
    granian_error_level: int = 20
    """Log level for granian error logs."""


class EmailSettings(BaseSettings):
    """Email service settings."""

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_email_", case_sensitive=False, extra="ignore"
    )

    enabled: bool = False
    """Enable email sending. If False, emails are logged but not sent."""
    backend: str = "console"
    """Email backend: 'console', 'memory', or 'resend'."""
    from_email: str = "noreply@example.com"
    """Default sender email address."""
    from_name: str = ""
    """Default sender display name."""
    resend_api_key: str = ""
    """Resend API key (required when backend='resend')."""
    verification_token_expires_hours: int = 24
    """Hours until email verification token expires."""
    password_reset_token_expires_minutes: int = 60
    """Minutes until password reset token expires."""
    invitation_token_expires_days: int = 7
    """Days until team invitation token expires."""


class ViteSettings(BaseSettings):
    """Vite development server settings."""

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_vite_", case_sensitive=False, extra="ignore"
    )

    dev_mode: bool = False
    """Start Vite development server."""
    template_dir: Path = Path("resources")
    """Template directory for Vite/Inertia resources."""


class OperatorTeamSettings(BaseSettings):
    """Operator team settings.

    The operator team is the first-party team that runs the platform: it
    curates metrics, publishes security reports, and onboards other
    organizations. Exactly one operator team exists per deployment and is
    created on first startup.
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_operator_team_", case_sensitive=False, extra="ignore"
    )

    name: str = "Certora"
    """Display name of the operator team."""
    domain: str = "certora.com"
    """Email domain that restricts operator team membership."""
    enforced_provider: str | None = None
    """OIDC provider the operator team is pinned to at creation
    ('google' | 'microsoft' | 'github'). When set, operators may only
    sign in via this provider (PR-8, Control 1). Leave unset to skip
    pinning (the default, so it stays inert in tests)."""
    slack_webhook_url: str = ""
    """Slack incoming-webhook URL for the operator-action alert fan-out
    (PR-8, Control 3). When set, every audited operator action posts a
    message. Empty (default) disables Slack delivery."""


class SuperuserSettings(BaseSettings):
    """Bootstrap superuser settings.

    When ``password`` is set the application ensures a superuser exists on
    startup. If a superuser is already present the settings are ignored so
    the hook is safe to run on every restart.

    The superuser email domain should match ``OperatorTeamSettings.domain``
    so the account can be assigned as owner of the operator team.
    """

    model_config = SettingsConfigDict(
        env_prefix="cert_ra_superuser_", case_sensitive=False, extra="ignore"
    )

    email: str = "user@certora.com"
    """Email address for the bootstrap superuser."""
    password: str | None = None
    """Plain-text password for the bootstrap superuser. Leave unset to disable."""


@cache
def get_app_settings() -> AppSettings:
    """Get cached AppSettings instance."""
    return AppSettings()


@cache
def get_feature_settings() -> FeatureSettings:
    """Get cached FeatureSettings instance."""
    return FeatureSettings()


@cache
def get_server_settings() -> ServerSettings:
    """Get cached ServerSettings instance."""
    return ServerSettings()


@cache
def get_log_settings() -> LogSettings:
    """Get cached LogSettings instance."""
    return LogSettings()


@cache
def get_email_settings() -> EmailSettings:
    """Get cached EmailSettings instance."""
    return EmailSettings()


@cache
def get_vite_settings() -> ViteSettings:
    """Get cached ViteSettings instance."""
    return ViteSettings()


@cache
def get_operator_team_settings() -> OperatorTeamSettings:
    """Get cached OperatorTeamSettings instance."""
    return OperatorTeamSettings()


@cache
def get_superuser_settings() -> SuperuserSettings:
    """Get cached SuperuserSettings instance."""
    return SuperuserSettings()
