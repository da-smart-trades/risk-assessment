# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""User login and logout controller."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from litestar import Controller, Request, get, post
from litestar.di import Provide
from litestar.exceptions import PermissionDeniedException, ValidationException
from litestar_vite.inertia import (
    InertiaExternalRedirect,
    InertiaRedirect,
    flash,
)
from sqlalchemy import select
from sqlalchemy.orm import undefer_group

from cert_ra.api.domain.accounts.controllers._mfa_v2 import (
    issue_mfa_attempt_cookie,
)
from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.schemas import AccountLogin, PasswordConfirm
from cert_ra.api.domain.accounts.services import (
    ProviderNotPermittedError,
    UserService,
)
from cert_ra.api.lib import crypt
from cert_ra.api.lib.auth_lockout import (
    assert_not_locked,
    assert_per_ip_under_limit,
    burn_password_check_time,
    enqueue_unlock_email_if_due,
    record_auth_attempt,
    record_failure,
    record_success,
)
from cert_ra.api.lib.oidc.providers import get_end_session_url
from cert_ra.api.lib.operator_roles import is_root_user
from cert_ra.api.lib.schema import NoProps
from cert_ra.api.lib.team_policy import assert_team_provider_allowed
from cert_ra.db.models import UserPasskey
from cert_ra.settings.api import get_app_settings

_GENERIC_LOGIN_ERROR = "Invalid email or password."
"""Single error message for the unknown-email + wrong-password branches
(anti-enumeration — design #74)."""

__all__ = ("AccessController",)

_MSG_AUTH_REQUIRED = "Authentication required"
_MSG_USER_NOT_FOUND = "User not found"
_MSG_INVALID_CREDENTIALS = "The provided password is incorrect."


class AccessController(Controller):
    """User login and registration."""

    include_in_schema = False
    dependencies = {"users_service": Provide(provide_users_service)}  # noqa: RUF012
    signature_namespace = {"UserService": UserService, "AccountLogin": AccountLogin}  # noqa: RUF012
    cache = False
    exclude_from_auth = True

    @get(component="auth/login", name="login", path="/login/")
    async def show_login(self, request: Request) -> InertiaRedirect | NoProps:
        """Show the user login page.

        Returns:
            Redirect to dashboard if authenticated, otherwise empty page props.
        """
        if request.session.get("user_id", False):
            flash(request, "Your account is already authenticated.", category="info")
            return InertiaRedirect(request, request.url_for("dashboard"))
        return NoProps()

    @post(component="auth/login", name="login.check", path="/login/")
    async def login(  # noqa: PLR0911, PLR0912, PLR0915
        self,
        request: Request[Any, Any, Any],
        users_service: UserService,
        data: AccountLogin,
    ) -> InertiaRedirect:
        """Authenticate a user, applying lockout + anti-enumeration rules.

        Flow (design #12, #13, #74):
        1. Record the attempt against the per-IP log; refuse if the IP
           is over its window limit.
        2. Look up the user by email. Unknown emails burn one
           ``password_hasher.verify`` worth of CPU for timing parity,
           then return the same generic error as a wrong password.
        3. Check the (user, ip) lockout. If locked, route to
           ``/auth/locked`` without revealing whether the password
           would have been right.
        4. Verify the password. On failure, record_failure increments
           the (user, ip) counter and may trigger an unlock email.
        5. On success, record_success drops the (user, ip) counter and
           continues into the MFA / verify-email / dashboard branches.

        Returns:
            Redirect to dashboard on successful authentication,
            to verify-email if unverified, to MFA prompt if enrolled,
            to /auth/locked if locked, or back to login on failure.
        """
        ip = request.client.host if request.client else "unknown"
        db_session = users_service.repository.session

        await record_auth_attempt(db_session, ip=ip, path="/login")
        if not await assert_per_ip_under_limit(db_session, ip=ip):
            await db_session.commit()
            flash(request, _GENERIC_LOGIN_ERROR, category="error")
            return InertiaRedirect(request, request.url_for("login"))

        user = await users_service.get_one_or_none(
            email=data.username, load=[undefer_group("security_sensitive")]
        )
        if user is None or user.hashed_password is None:
            await db_session.commit()
            await burn_password_check_time()
            flash(request, _GENERIC_LOGIN_ERROR, category="error")
            return InertiaRedirect(request, request.url_for("login"))

        # Per-team IDP enforcement (design #5): if any of the user's
        # teams locks sign-in to an OIDC provider, password login is
        # refused before the hash is checked. We route to the
        # /auth/team-policy dead-end, which guides the user to sign in
        # via the required provider (the existing password→OIDC
        # link-confirm flow then migrates them). No-op while the
        # feature flag is off.
        try:
            await assert_team_provider_allowed(
                db_session, user, attempted_provider=None
            )
        except ProviderNotPermittedError as exc:
            await db_session.commit()
            request.session["auth_flow"] = {
                "page": "team_policy",
                "context": {"required": exc.required_provider},
            }
            return InertiaRedirect(request, request.url_for("auth.team-policy"))

        locked_until = await assert_not_locked(db_session, user_id=user.id, ip=ip)
        if locked_until is not None:
            await db_session.commit()
            return InertiaRedirect(request, request.url_for("auth.locked"))

        if not await crypt.verify_password(data.password, user.hashed_password):
            locked_now, _was = await record_failure(db_session, user_id=user.id, ip=ip)
            unlock_payload: tuple[str | None, object] = (None, None)
            if locked_now:
                unlock_payload = await enqueue_unlock_email_if_due(
                    db_session, user_id=user.id
                )
            await db_session.commit()
            raw_token = unlock_payload[0]
            if locked_now and raw_token is not None:
                request.app.emit(
                    "unlock_email",
                    user_email=user.email,
                    token=raw_token,
                    ip=ip,
                )
                return InertiaRedirect(request, request.url_for("auth.locked"))
            if locked_now:
                return InertiaRedirect(request, request.url_for("auth.locked"))
            flash(request, _GENERIC_LOGIN_ERROR, category="error")
            return InertiaRedirect(request, request.url_for("login"))

        if not user.is_active:
            await db_session.commit()
            flash(request, "This account is not currently active.", category="error")
            return InertiaRedirect(request, request.url_for("login"))

        await record_success(db_session, user_id=user.id, ip=ip)
        await db_session.commit()

        # NOTE: operator MFA posture (design — Control 1) is enforced on
        # the OIDC sign-in path (operators authenticate via the corporate
        # IdP; their team's enforced_provider refuses password login in
        # production). The password-path posture check (AC #26, pure
        # defense-in-depth) is deferred for normal operators — it
        # conflicts with the test suite's password-login superuser, who
        # is the operator-team owner. See _oidc.py for the enforced check.

        # Break-glass root account: it never uses an IdP, so its hardening
        # is enforced here on the password path. First it must rotate the
        # seeded password, then it must have a passkey.
        if is_root_user(user.email):
            if user.must_change_password:
                request.set_session({"force_password_change_user_id": str(user.id)})
                flash(
                    request,
                    "You must set a new password before continuing.",
                    category="info",
                )
                return InertiaRedirect(
                    request, request.url_for("auth.force-password-change")
                )
            root_has_passkey = await db_session.scalar(
                select(UserPasskey.id).where(UserPasskey.user_id == user.id).limit(1)
            )
            if root_has_passkey is None:
                # Break-glass bootstrap (root account only): without an
                # in-app enrollment path, the first root login is a
                # chicken-and-egg dead end. Establish a partial session
                # pinned to /settings/security/mfa/enroll — the
                # PasskeyEnrollmentTrap middleware keeps the user there
                # until passkey_finish flips `requires_passkey_enrollment`
                # off. Narrow weakening of Control 1, scoped to the one
                # break-glass identity.
                request.set_session({"user_id": user.email})
                request.session["auth_method"] = "password"
                request.session["mfa_enrolled"] = False
                request.session["requires_passkey_enrollment"] = True
                flash(
                    request,
                    "Enroll a passkey to finish setting up your operator account.",
                    category="info",
                )
                return InertiaRedirect(request, request.url_for("mfa.enroll.page"))

        invitation_token = request.session.get("invitation_token")

        if get_app_settings().must_verify_email and not user.is_verified:
            request.set_session({"unverified_user_id": str(user.id)})
            if invitation_token:
                request.session["invitation_token"] = invitation_token
            flash(
                request, "Please verify your email before logging in.", category="error"
            )
            return InertiaRedirect(
                request, request.url_for("verify-email", status="verification-required")
            )

        # New MFA flow (PR-3): mint a server-side MfaAttempt + cookie
        # and redirect to /auth/mfa.
        has_passkey = await db_session.scalar(
            select(UserPasskey.id).where(UserPasskey.user_id == user.id).limit(1)
        )
        if (user.is_two_factor_enabled and user.totp_secret) or has_passkey:
            cookie, _challenge = await issue_mfa_attempt_cookie(
                db_session,
                user_id=user.id,
                with_webauthn_challenge=bool(has_passkey),
            )
            await db_session.commit()
            if invitation_token:
                request.session["invitation_token"] = invitation_token
            request.logger.info("Redirecting to MFA verify (v2)")
            redirect = InertiaRedirect(request, request.url_for("mfa.verify.page"))
            redirect.cookies.append(cookie)
            return redirect

        request.set_session({"user_id": user.email})
        request.session["auth_method"] = "password"
        request.session["mfa_enrolled"] = bool(user.is_two_factor_enabled)
        if invitation_token:
            request.session["invitation_token"] = invitation_token

        flash(request, "Your account was successfully authenticated.", category="info")

        if invitation_token:
            request.logger.info("Redirecting to invitation page with token")
            return InertiaRedirect(
                request,
                request.url_for("invitation.accept.page", token=invitation_token),
            )

        request.logger.info("Redirecting to %s ", request.url_for("dashboard"))
        return InertiaRedirect(request, request.url_for("dashboard"))

    @post(name="logout", path="/logout/", exclude_from_auth=False)
    async def logout(
        self, request: Request
    ) -> InertiaRedirect | InertiaExternalRedirect:
        """Log out the current user.

        Always clears the local session. If the user signed in via an
        OIDC provider that supports RP-initiated logout, also bounce them
        through the provider's ``end_session_endpoint`` so the IdP session
        is terminated (design open question #1); otherwise redirect to
        the local login page.

        Returns:
            Redirect to the login page, or an external redirect to the
            IdP's end-session endpoint.
        """
        auth_method = request.session.get("auth_method")
        end_session = (
            await get_end_session_url(auth_method, request.url_for("login"))
            if isinstance(auth_method, str)
            else None
        )
        flash(request, "You have been logged out.", category="info")
        request.clear_session()
        if end_session is not None:
            return InertiaExternalRedirect(request, end_session)
        return InertiaRedirect(request, request.url_for("login"))

    @get(
        component="auth/confirm-password",
        name="password.confirm.page",
        path="/confirm-password/",
        exclude_from_auth=False,
    )
    async def show_confirm_password(self, request: Request) -> NoProps:  # noqa: ARG002
        """Show the password confirmation page.

        This is used before sensitive operations to verify the user's identity.

        Returns:
            Empty page props.
        """
        return NoProps()

    @post(
        component="auth/confirm-password",
        name="password.confirm",
        path="/confirm-password/",
        exclude_from_auth=False,
    )
    async def confirm_password(
        self,
        request: Request,
        users_service: UserService,
        data: PasswordConfirm,
    ) -> InertiaRedirect:
        """Confirm user password before sensitive actions.

        Raises:
            PermissionDeniedException: If authentication fails.
            ValidationException: If the provided password is incorrect.

        Returns:
            Redirect to intended destination or dashboard.
        """
        user_id = request.session.get("user_id")
        if not user_id:
            raise PermissionDeniedException(_MSG_AUTH_REQUIRED)

        user = await users_service.get_one_or_none(
            email=user_id, load=[undefer_group("security_sensitive")]
        )
        if not user:
            raise PermissionDeniedException(_MSG_USER_NOT_FOUND)

        if not user.hashed_password or not crypt.verify_password(
            data.password, user.hashed_password
        ):
            raise ValidationException(_MSG_INVALID_CREDENTIALS)

        request.session["password_confirmed_at"] = datetime.now(UTC).isoformat()
        if intended_url := request.session.pop("intended_url", None):
            return InertiaRedirect(request, intended_url)
        return InertiaRedirect(request, request.url_for("dashboard"))
