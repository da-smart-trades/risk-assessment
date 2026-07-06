# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Forced password-change interstitial (break-glass root, first login).

When the break-glass root account (``CERT_RA_SUPERUSER_EMAIL``) signs in
with ``must_change_password`` set (true at bootstrap), the login handler
stashes ``force_password_change_user_id`` in the session and redirects
here instead of establishing a full session. The user sets a new
password; ``must_change_password`` is cleared and they re-authenticate.

After re-login the root's passkey requirement (enforced in the login
handler) routes them to operator setup if they still lack a passkey.

Operator team hardening — break-glass root account.
"""

from __future__ import annotations

from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.di import Provide
from litestar_vite.inertia import InertiaRedirect, flash
from msgspec import Struct

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.lib.schema import NoProps

__all__ = ("ForcePasswordChangeController",)

_MIN_PASSWORD_LENGTH = 12
_SESSION_KEY = "force_password_change_user_id"


class ForcePasswordChangeForm(Struct):
    """POST payload for the forced password change."""

    password: str
    confirm_password: str


class ForcePasswordChangeController(Controller):
    """Forced password rotation before the root's session is established."""

    include_in_schema = False
    exclude_from_auth = True
    cache = False
    dependencies = {  # noqa: RUF012
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {  # noqa: RUF012
        "UserService": UserService,
        "ForcePasswordChangeForm": ForcePasswordChangeForm,
    }

    @get(
        component="auth/force-password-change",
        name="auth.force-password-change",
        path="/auth/force-password-change/",
    )
    async def show(self, request: Request) -> NoProps | InertiaRedirect:
        """Render the forced-change form (only with a valid session marker)."""
        if not request.session.get(_SESSION_KEY):
            return InertiaRedirect(request, request.url_for("login"))
        return NoProps()

    @post(
        component="auth/force-password-change",
        name="auth.force-password-change.submit",
        path="/auth/force-password-change/",
    )
    async def submit(
        self,
        request: Request,
        users_service: UserService,
        data: ForcePasswordChangeForm,
    ) -> InertiaRedirect:
        """Set the new password, clear the flag, and require re-login."""
        uid = request.session.get(_SESSION_KEY)
        if not uid:
            return InertiaRedirect(request, request.url_for("login"))
        user = await users_service.get_one_or_none(id=UUID(uid))
        if user is None:
            request.clear_session()
            return InertiaRedirect(request, request.url_for("login"))

        if (
            len(data.password) < _MIN_PASSWORD_LENGTH
            or data.password != data.confirm_password
        ):
            flash(
                request,
                f"Password must be at least {_MIN_PASSWORD_LENGTH} characters "
                "and match the confirmation.",
                category="error",
            )
            return InertiaRedirect(
                request, request.url_for("auth.force-password-change")
            )

        user.must_change_password = False
        await users_service.reset_password(data.password, db_obj=user)
        await users_service.repository.session.commit()

        request.clear_session()
        flash(
            request,
            "Password updated. Sign in with your new password.",
            category="success",
        )
        return InertiaRedirect(request, request.url_for("login"))
