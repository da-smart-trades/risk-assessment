# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Orphan-user redirect middleware.

A user with zero ``TeamMember`` rows can't usefully use the app — every
team-scoped resource (alerts, manual metrics, integrations) is empty
and any team-admin link 404s. This middleware funnels such users to
``/auth/no-team`` so they see a clear next step (contact your admin /
sign out) instead of a broken dashboard.

The check fires only for authenticated requests. Superusers and the
auth surface bypass it (they need to be able to sign in / out and to
operate without a team in the operator panel).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litestar.enums import ScopeType
from litestar.middleware.base import AbstractMiddleware
from litestar.response.redirect import ASGIRedirectResponse

if TYPE_CHECKING:
    from litestar.types import ASGIApp, Receive, Scope, Send

NO_TEAM_PATH = "/auth/no-team/"

_NO_TEAM_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    NO_TEAM_PATH,
    "/auth/",
    "/login",
    "/logout",
    "/static/",
    "/assets/",
    "/favicon",
    "/build/",
    "/schema/",
    "/health",
    "/admin/",  # superusers can still operate
    "/uploads/",
)


class NoTeamMiddleware(AbstractMiddleware):
    """Redirect signed-in users with zero teams to ``/auth/no-team``.

    Trust anchor: we don't reload the User row on every request. The
    login + OIDC handlers set ``session["team_count"]`` on success;
    the team-membership controllers bump it. ``team_count = 0`` (or
    missing) on an authenticated session triggers the redirect.

    Pure auth + admin + static paths bypass (see
    ``_NO_TEAM_ALLOWLIST_PREFIXES``) so the dead-end page itself,
    logout, and superuser admin tools always remain reachable.
    """

    scopes = {ScopeType.HTTP}  # noqa: RUF012

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the middleware with the inner ASGI app."""
        super().__init__(app=app)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch with a possible redirect to ``NO_TEAM_PATH``."""
        if not self._should_redirect(scope):
            await self.app(scope, receive, send)
            return
        redirect = ASGIRedirectResponse(path=NO_TEAM_PATH, status_code=303)
        await redirect(scope, receive, send)

    @staticmethod
    def _should_redirect(scope: Scope) -> bool:
        """True iff the request is a no-team authenticated user.

        Class-level ``scopes = {ScopeType.HTTP}`` already restricts the
        middleware to HTTP scopes.
        """
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in _NO_TEAM_ALLOWLIST_PREFIXES):
            return False
        session = scope.get("session") or {}
        if not isinstance(session, dict):
            return False
        if not session.get("user_id"):
            return False
        if session.get("is_superuser"):
            return False
        team_count = session.get("team_count", None)
        return team_count is not None and team_count <= 0
