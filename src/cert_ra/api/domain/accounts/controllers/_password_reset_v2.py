# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Password-reset controller — PR-5 flow.

Replaces the legacy ``EmailToken``-backed flow with the OIDC SSO
design's canonical-helper flow on ``UserPasswordResetToken``.

Three handlers:

- ``POST /auth/forgot-password`` — always returns the same generic
  response, whether the email is registered, a SSO-only account, or
  unknown. Token + email only get emitted when there's a real user
  with a password.
- ``GET /auth/reset/{token}`` — renders the set-new-password form.
- ``POST /auth/reset`` — atomically consumes the token, updates
  ``hashed_password`` via ``claim_password_reset``, clears the
  current session, and redirects to ``/login?reset=ok``. Does NOT
  establish a session.

Anti-enumeration rules (design #82, #83):

- Forgot-password is rate-limited via the per-IP counter from PR-4.
- Same response body + status + headers regardless of branch.
- Timing parity via ``burn_password_check_time`` on the no-op branches.
- The reset POST never names "wrong email" — same generic message.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from litestar import Controller, Request, get, post
from litestar.di import Provide
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy.orm import undefer_group

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.schemas import (
    EmailSent,
    ForgotPasswordRequest,
    PasswordReset,
    PasswordResetToken,
)
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.lib import crypt
from cert_ra.api.lib.auth_lockout import burn_password_check_time
from cert_ra.api.lib.password_resets import (
    PasswordResetTokenUnusableError,
    assert_password_reset_token_usable,
    claim_password_reset,
    find_password_reset_token_by_token_hash,
)
from cert_ra.api.lib.schema import NoProps
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import UserPasswordResetToken

__all__ = ("PasswordResetV2Controller",)

PASSWORD_RESET_TOKEN_TTL = timedelta(hours=1)
"""Per design — a self-service reset link expires in 1 hour."""

_GENERIC_FORGOT_RESPONSE = (
    "If an account exists for that email, we've sent a reset link."
)


class PasswordResetV2Controller(Controller):
    """Self-service password reset on the canonical-helper flow."""

    include_in_schema = False
    dependencies = {"users_service": Provide(provide_users_service)}  # noqa: RUF012
    signature_namespace = {  # noqa: RUF012
        "UserService": UserService,
        "ForgotPasswordRequest": ForgotPasswordRequest,
        "PasswordReset": PasswordReset,
    }
    exclude_from_auth = True

    @get(
        component="auth/forgot-password",
        name="forgot-password.v2",
        path="/auth/forgot-password/",
    )
    async def show_forgot(self, request: Request) -> NoProps | InertiaRedirect:
        """Render the forgot-password form."""
        if request.session.get("user_id"):
            return InertiaRedirect(request, request.url_for("dashboard"))
        return NoProps()

    @post(
        component="auth/forgot-password",
        name="forgot-password.v2.send",
        path="/auth/forgot-password/",
        status_code=200,
    )
    async def send_reset(
        self,
        request: Request,
        users_service: UserService,
        data: ForgotPasswordRequest,
    ) -> EmailSent:
        """Mint a reset token + emit the email event — when applicable.

        Three branches all flash the same generic confirmation:

        1. Unknown email → burn one verify-time worth of CPU; no token.
        2. SSO-only user (``hashed_password IS NULL``) → no token, no
           email — design refuses to issue a password to an SSO-only
           account via a self-service flow.
        3. Real password user → mint ``UserPasswordResetToken``, emit
           ``password_reset_v2_requested`` for the email signal.
        """
        normalized = data.email.strip().lower()
        db_session = users_service.repository.session
        # undefer_group("security_sensitive") so the user.hashed_password
        # check below doesn't trigger a deferred-column load that fails
        # with MissingGreenlet under AsyncSession.
        user = await users_service.get_one_or_none(
            email=normalized, load=[undefer_group("security_sensitive")]
        )

        if user is None or user.hashed_password is None:
            await burn_password_check_time()
            flash(request, _GENERIC_FORGOT_RESPONSE, category="info")
            return EmailSent()

        plain_token = secrets.token_urlsafe(32)
        row = UserPasswordResetToken(
            user_id=user.id,
            token_hash=hmac_sha256(plain_token),
            expires_at=datetime.now(UTC) + PASSWORD_RESET_TOKEN_TTL,
        )
        db_session.add(row)
        await db_session.commit()

        request.app.emit(
            "password_reset_v2_requested",
            user_email=user.email,
            user_name=user.name,
            token=plain_token,
            ip_address=request.client.host if request.client else "unknown",
        )
        flash(request, _GENERIC_FORGOT_RESPONSE, category="info")
        return EmailSent()

    @get(
        component="auth/reset-password",
        name="reset-password.v2",
        path="/auth/reset/{token:str}",
    )
    async def show_reset(
        self,
        request: Request,
        users_service: UserService,
        token: str,
    ) -> InertiaRedirect | PasswordResetToken:
        """Render the set-new-password form, or bounce if the token is bad.

        Anti-enumeration: all rejection branches go to the same
        ``forgot-password.v2`` URL with the same generic flash. No
        wording reveals whether the token was wrong vs expired vs
        already used.
        """
        if request.session.get("user_id"):
            return InertiaRedirect(request, request.url_for("dashboard"))
        db_session = users_service.repository.session
        row = await find_password_reset_token_by_token_hash(
            db_session, hmac_sha256(token)
        )
        try:
            assert_password_reset_token_usable(row)
        except PasswordResetTokenUnusableError:
            flash(
                request,
                "This password-reset link is invalid or has expired.",
                category="error",
            )
            return InertiaRedirect(request, request.url_for("forgot-password.v2"))
        assert row is not None
        user = await users_service.get_one_or_none(id=row.user_id)
        if user is None:
            flash(
                request,
                "This password-reset link is invalid or has expired.",
                category="error",
            )
            return InertiaRedirect(request, request.url_for("forgot-password.v2"))
        return PasswordResetToken(token=token, email=user.email)

    @post(
        component="auth/reset-password",
        name="reset-password.v2.submit",
        path="/auth/reset",
    )
    async def submit_reset(
        self,
        request: Request,
        users_service: UserService,
        data: PasswordReset,
    ) -> InertiaRedirect:
        """Atomically consume the token + update the password.

        Design rules (#78, #79, #80, #81):
        - Never establish a session here. The user MUST go through
          ``/login`` (which then runs the lockout, MFA, etc. gates).
        - Never touch ``UserPasskey`` / ``totp_secret`` /
          ``UserRecoveryCode`` / ``UserOauthAccount``. Reset is
          password-only.
        - Clear ``request.session`` to log out the current device.
          Full multi-device invalidation is gated on
          ``invalidate_other_user_sessions`` (PR-1 deferred).
        """
        db_session = users_service.repository.session
        row = await find_password_reset_token_by_token_hash(
            db_session, hmac_sha256(data.token)
        )
        try:
            assert_password_reset_token_usable(row)
        except PasswordResetTokenUnusableError:
            flash(
                request,
                "This password-reset link is invalid or has expired.",
                category="error",
            )
            return InertiaRedirect(request, request.url_for("forgot-password.v2"))
        assert row is not None

        hashed = await crypt.get_password_hash(data.password)
        user_id = await claim_password_reset(
            db_session, row.id, new_hashed_password=hashed
        )
        if user_id is None:
            await db_session.commit()
            flash(
                request,
                "This password-reset link is invalid or has expired.",
                category="error",
            )
            return InertiaRedirect(request, request.url_for("forgot-password.v2"))
        await db_session.commit()
        request.app.emit("password_reset_v2_completed", user_id=user_id)

        # Clear the current session — reset does not establish one,
        # and any cookie-resumable session from before is now stale.
        request.clear_session()
        flash(
            request,
            "Password updated. Sign in with your new password.",
            category="success",
        )
        return InertiaRedirect(request, request.url_for("login"))
