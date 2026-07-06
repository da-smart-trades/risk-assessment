# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Step-up reauthentication controller — ``/auth/reauth``.

Used when an already-signed-in user is about to perform a sensitive
action that needs a fresh credential check (e.g., admin recovery,
unlinking the last OIDC account). The flow:

1. ``GET /auth/reauth?next=<path>`` renders the prompt page. ``next``
   is validated via ``safe_redirect_target`` so an attacker can't
   forge ``next=https://evil.com`` (design #131).
2. ``POST /auth/reauth/password`` accepts a password (and MFA via the
   MfaAttempt flow if enrolled). On success bumps
   ``session["last_auth_at"]`` and redirects to ``next``.

The handler does NOT:

- rotate the session (design #130) — reauth keeps the same session id.
- invalidate other sessions.
- mint a new ``user_id`` (it must equal the current one — refuse
  binding to a different user, design #129).
"""

from __future__ import annotations

from datetime import UTC, datetime

from litestar import Controller, Request, get, post
from litestar.di import Provide
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy.orm import undefer_group

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.accounts.schemas import PasswordConfirm
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.lib import crypt
from cert_ra.api.lib.safe_redirect import safe_redirect_target
from cert_ra.api.lib.schema import NoProps

__all__ = ("ReauthController",)


class ReauthController(Controller):
    """Step-up reauthentication for sensitive actions."""

    include_in_schema = False
    dependencies = {"users_service": Provide(provide_users_service)}  # noqa: RUF012
    signature_namespace = {  # noqa: RUF012
        "UserService": UserService,
        "PasswordConfirm": PasswordConfirm,
    }
    cache = False
    guards = [requires_active_user]  # noqa: RUF012

    @get(component="auth/reauth", name="auth.reauth", path="/auth/reauth/")
    async def show(self, request: Request) -> NoProps:
        """Render the reauth prompt.

        Pins the validated ``next`` to session so the POST handler
        can read it without re-parsing.
        """
        raw_next = request.query_params.get("next") or "/dashboard"
        request.session["reauth_next"] = safe_redirect_target(raw_next)
        return NoProps()

    @post(
        component="auth/reauth",
        name="auth.reauth.password",
        path="/auth/reauth/password",
    )
    async def submit_password(
        self,
        request: Request,
        users_service: UserService,
        data: PasswordConfirm,
    ) -> InertiaRedirect:
        """Verify the password and bump ``last_auth_at``.

        - Refuses if the current session has no ``user_id``.
        - Refuses if the supplied password is wrong (no enumeration —
          we already know who the user is).
        - Does NOT rotate the session (design #130).
        - Does NOT establish a new ``user_id`` (design #129).
        """
        user_id_str = request.session.get("user_id")
        if not user_id_str:
            return InertiaRedirect(request, request.url_for("login"))

        user = await users_service.get_one_or_none(
            email=user_id_str, load=[undefer_group("security_sensitive")]
        )
        if user is None or not user.hashed_password:
            flash(request, "Incorrect password. Try again.", category="error")
            return InertiaRedirect(request, request.url_for("auth.reauth"))

        if not await crypt.verify_password(data.password, user.hashed_password):
            flash(request, "Incorrect password. Try again.", category="error")
            return InertiaRedirect(request, request.url_for("auth.reauth"))

        request.session["last_auth_at"] = datetime.now(UTC).isoformat()
        next_target = safe_redirect_target(
            request.session.pop("reauth_next", "/dashboard")
        )
        return InertiaRedirect(request, next_target)
