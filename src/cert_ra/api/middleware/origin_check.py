# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Origin / Referer allowlist for state-changing requests.

Defends against cross-site state-changing requests where the browser
sent a valid session cookie but the request was launched from
``evil.com``. Litestar's CSRF middleware covers the
double-submit-token side; this middleware closes the secondary
header-based gap (design #88-#90).

The header check fires only on state-changing methods (POST / PUT /
PATCH / DELETE). Reads (GET / HEAD / OPTIONS) bypass the check.

The allowlist comes from ``AppSettings.csrf_allowed_origins``. An
empty allowlist is treated as a misconfiguration → all state-changing
requests are refused (fail-closed).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litestar.enums import ScopeType
from litestar.middleware.base import AbstractMiddleware
from litestar.response.base import ASGIResponse

if TYPE_CHECKING:
    from litestar.types import ASGIApp, Receive, Scope, Send

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
"""Methods that require an Origin / Referer match. GET / HEAD / OPTIONS
are not subject to the check — they're idempotent reads."""


class OriginCheckMiddleware(AbstractMiddleware):
    """Validate the ``Origin`` (or ``Referer``) header on writes.

    For each state-changing request:

    1. If ``Origin`` is present, it MUST be a byte-for-byte match for
       one of the allowlist entries.
    2. If ``Origin`` is missing, fall back to ``Referer`` and check
       that its scheme + host + port match an allowlist entry's
       prefix.
    3. ``Origin: null`` is rejected outright (design #90 — this is
       how some sandboxed contexts signal a hostile load).
    """

    scopes = {ScopeType.HTTP}  # noqa: RUF012

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the middleware with the inner ASGI app."""
        super().__init__(app=app)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch with a possible 403 short-circuit."""
        if not self._should_check(scope):
            await self.app(scope, receive, send)
            return
        if self._allowlist_allows(scope):
            await self.app(scope, receive, send)
            return
        await ASGIResponse(
            body=b'{"detail":"Origin not allowed"}',
            status_code=403,
            media_type="application/json",
        )(scope, receive, send)

    @staticmethod
    def _should_check(scope: Scope) -> bool:
        """Only HTTP state-changing methods trigger the check.

        Class-level ``scopes = {ScopeType.HTTP}`` already restricts
        the middleware to HTTP; we only need the method check here.
        """
        return scope.get("method", "GET") in _STATE_CHANGING_METHODS

    @staticmethod
    def _allowlist_allows(scope: Scope) -> bool:
        """Return True if the request's Origin/Referer is on the allowlist.

        Browsers always send ``Origin`` on cross-origin state-changing
        requests, so the check is essentially "if Origin is present,
        it MUST match." Origin-absent requests (server-to-server,
        tests, curl, native HTTP clients) bypass this header check —
        Litestar's CSRF token middleware is the line of defense for
        them.

        ``settings.csrf_allowed_origins`` empty means fail-closed.
        """
        from cert_ra.settings.api import get_app_settings

        allowed = list(get_app_settings().csrf_allowed_origins or [])
        if not allowed:
            return False

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        origin = headers.get("origin")

        if origin is not None:
            if origin == "null":
                return False
            return origin in allowed

        referer = headers.get("referer")
        if referer is None:
            # No Origin and no Referer: not a browser cross-origin
            # request. CSRF token defense covers this.
            return True
        return any(
            referer.startswith(prefix + "/") or referer == prefix for prefix in allowed
        )
