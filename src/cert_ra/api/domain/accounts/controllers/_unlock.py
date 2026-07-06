# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Unlock-via-email controller.

GET ``/auth/unlock/{token}`` consumes the unlock token via the canonical
helper and clears every ``UserLockout`` row for the user. The page
itself just flashes a confirmation and redirects to ``/login``; the
user signs in again from there.
"""

from __future__ import annotations

from typing import Annotated

from litestar import Controller, Request, get
from litestar.di import Provide
from litestar.params import Parameter
from litestar_vite.inertia import InertiaRedirect, flash

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.lib.auth_lockout import force_unlock_user
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.api.lib.unlock_tokens import (
    UnlockTokenUnusableError,
    assert_unlock_token_usable,
    claim_unlock_token_consumed,
    find_unlock_token_by_token_hash,
)

__all__ = ("UnlockController",)


class UnlockController(Controller):
    """The unlock-via-email recovery flow."""

    include_in_schema = False
    dependencies = {"users_service": Provide(provide_users_service)}  # noqa: RUF012
    signature_namespace = {"UserService": UserService}  # noqa: RUF012
    cache = False
    exclude_from_auth = True

    @get(
        component="auth/locked",
        name="auth.locked",
        path="/auth/locked/",
    )
    async def show_locked(self, request: Request) -> object:  # noqa: ARG002
        """Render the locked-account page.

        The page wording is intentionally generic — it does NOT
        confirm whether the email exists. It tells the visitor we've
        sent an unlock email if they have an account, and to wait or
        contact their team admin.
        """
        from cert_ra.api.lib.schema import NoProps

        return NoProps()

    @get(name="auth.unlock", path="/auth/unlock/{token:str}")
    async def consume_unlock(
        self,
        request: Request,
        users_service: UserService,
        token: Annotated[
            str, Parameter(title="Token", description="The unlock token.")
        ],
    ) -> InertiaRedirect:
        """Atomically consume the unlock token and clear lockouts.

        On success, redirects to ``/login`` with a confirmation flash.
        On any failure (missing / consumed / expired / not-found), we
        render the same redirect with the same generic message — the
        page does NOT reveal whether the token was valid (design
        anti-enumeration rule).
        """
        db_session = users_service.repository.session
        row = await find_unlock_token_by_token_hash(db_session, hmac_sha256(token))
        try:
            assert_unlock_token_usable(row)
        except UnlockTokenUnusableError:
            await db_session.commit()
            flash(
                request,
                "If the link was valid, your account has been unlocked.",
                category="info",
            )
            return InertiaRedirect(request, request.url_for("login"))

        assert row is not None
        claimed = await claim_unlock_token_consumed(db_session, row.id)
        if not claimed:
            await db_session.commit()
            flash(
                request,
                "If the link was valid, your account has been unlocked.",
                category="info",
            )
            return InertiaRedirect(request, request.url_for("login"))

        await force_unlock_user(db_session, user_id=row.user_id)
        await db_session.commit()
        request.app.emit(
            "unlock_completed",
            user_id=row.user_id,
        )
        flash(
            request,
            "Your account has been unlocked. Sign in to continue.",
            category="success",
        )
        return InertiaRedirect(request, request.url_for("login"))


def auth_locked_redirect(request: Request) -> InertiaRedirect:
    """Render the ``/auth/locked`` page.

    A tiny helper so the login controller doesn't have to know the
    URL name. Kept here to colocate with the unlock surface.
    """
    return InertiaRedirect(request, request.url_for("auth.locked"))
