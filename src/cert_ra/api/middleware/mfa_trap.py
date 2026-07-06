# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""MFA enrollment-trap middleware.

A password user who is signed in but has not yet enrolled MFA is
trapped at ``/settings/security/mfa/enroll``. Every other request
redirects there. OIDC-only users (no ``hashed_password``) and users
who have already enrolled (``is_two_factor_enabled=True``) pass
through unimpeded.

A second, narrower trigger fires for the break-glass root account
when it lands in a fresh session with no enrolled passkey. The login
handler sets ``requires_passkey_enrollment=True`` to pin the root
session to the enrollment page until ``passkey_finish`` clears the
flag.

MFA enrollment trap middleware; operator team hardening — Control 1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litestar.enums import ScopeType
from litestar.middleware.base import AbstractMiddleware
from litestar.response.redirect import ASGIRedirectResponse

if TYPE_CHECKING:
    from litestar.types import ASGIApp, Receive, Scope, Send

ENROLL_PATH = "/settings/security/mfa/enroll"
"""The single page the trap allows during enrollment."""

# Paths a trapped user can still hit. Auth / sign-out / static / API
# routes the React shell needs to render the enrollment page.
_TRAP_ALLOWLIST_PREFIXES: tuple[str, ...] = (
    ENROLL_PATH,
    "/settings/security/mfa/",  # POST enroll/verify endpoints
    "/logout",
    "/auth/",  # /auth/mfa, /auth/<provider>/callback, etc.
    "/login",
    "/static/",
    "/assets/",
    "/favicon",
    "/build/",
    "/schema/",  # OpenAPI / Swagger
    "/health",
)


class MfaEnrollmentTrapMiddleware(AbstractMiddleware):
    """Force password users to enroll MFA before any other request.

    The middleware inspects the session for three keys set by login:
    ``user_id`` (email), ``auth_method`` ("password" | "google" | ...),
    and ``mfa_enrolled`` (bool). The bool is the trust-anchor — we
    don't reload the User row on every request. The login handler
    sets ``mfa_enrolled = user.is_two_factor_enabled`` at session
    establish time, and the enrollment endpoint flips it to True on
    success.

    Trap conditions (all must hold):
      - Session has a ``user_id``.
      - ``auth_method == "password"``.
      - ``mfa_enrolled`` is falsy.
      - The request path is not in ``_TRAP_ALLOWLIST_PREFIXES``.

    On trap, returns a 303 redirect to ``ENROLL_PATH``. Inertia treats
    303 as a hard redirect, so it survives client-side navigation.
    """

    scopes = {ScopeType.HTTP}  # noqa: RUF012

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the middleware with the inner ASGI app."""
        super().__init__(app=app)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch with a possible redirect to enrollment."""
        if not self._should_trap(scope):
            await self.app(scope, receive, send)
            return
        redirect = ASGIRedirectResponse(path=ENROLL_PATH, status_code=303)
        await redirect(scope, receive, send)

    @staticmethod
    def _should_trap(scope: Scope) -> bool:
        """True iff this request is a trapped password user off the allowlist.

        Two independent triggers:

        - Generic MFA trap: a signed-in password user without enrolled
          MFA. Always on — every password user must enroll MFA before
          they can use the rest of the app.
        - Root passkey bootstrap: the session carries
          ``requires_passkey_enrollment=True``, set by the login
          handler when the break-glass root account signs in with no
          enrolled passkey.

        The path allowlist applies to both.
        """
        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in _TRAP_ALLOWLIST_PREFIXES):
            return False
        session = scope.get("session") or {}
        if not isinstance(session, dict) or not session.get("user_id"):
            return False
        if session.get("requires_passkey_enrollment"):
            return True
        if session.get("auth_method") != "password":
            return False
        return not session.get("mfa_enrolled", False)
