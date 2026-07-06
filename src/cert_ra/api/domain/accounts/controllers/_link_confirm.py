# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Password → OIDC link-confirm controller.

When an OIDC sign-in matches a password-protected user (no prior OAuth
link), the OIDC callback stashes the validated identity in a
``PendingOidcLink`` row and redirects here. The user proves password
ownership; on success we atomically link the OIDC identity and
**clear** ``hashed_password`` (the design's one-way SSO migration —
once linked, a user can't revert to password sign-in via this flow).

Session is established only on a successful link. A failed password
verify (up to 3 attempts) increments ``PendingOidcLink.failed_attempts``
atomically; on the third strike the row is consumed and the cookie is
useless. The user can restart the OIDC sign-in to mint a fresh row.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from litestar import Controller, Request, get, post
from litestar.datastructures import Cookie
from litestar.di import Provide
from litestar.response import Response
from litestar_vite.inertia import InertiaRedirect, flash
from msgspec import Struct
from sqlalchemy import select
from sqlalchemy.orm import undefer

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.accounts.services._oidc_resolver import (
    PendingLinkRequired,
)
from cert_ra.api.lib import crypt
from cert_ra.api.lib.pending_links import (
    PendingLinkUnusableError,
    assert_pending_link_usable,
    claim_pending_link_consumed,
    find_pending_link_by_token_hash,
    increment_failed_attempts,
)
from cert_ra.api.lib.session_rotation import reauthenticate_session
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import PendingOidcLink, UserOauthAccount
from cert_ra.db.models.user import User

__all__ = (
    "PENDING_LINK_COOKIE_NAME",
    "PENDING_LINK_TTL",
    "LinkConfirmController",
    "issue_pending_link_cookie",
)


# Cookie scoping notes:
#   - HttpOnly + Secure + SameSite=Lax: standard for auth cookies
#   - path="/auth/link-confirm": the cookie is sent only on the
#     link-confirm endpoints; never bleeds into other traffic
#   - max_age tracks the row's expires_at (10 minutes)
PENDING_LINK_COOKIE_NAME = "pending_link_token"
PENDING_LINK_TTL = timedelta(minutes=10)
PENDING_LINK_COOKIE_PATH = "/auth/link-confirm"


def issue_pending_link_cookie(raw_token: str) -> Cookie:
    """Build the cookie that carries the link-confirm token.

    Args:
        raw_token: The plaintext, high-entropy token whose HMAC hash
            is stored on the matching ``PendingOidcLink`` row.

    Returns:
        A Litestar ``Cookie`` instance the caller attaches to the
        outbound response (typically via
        ``Response(cookies=[issue_pending_link_cookie(token)])``).
    """
    return Cookie(
        key=PENDING_LINK_COOKIE_NAME,
        value=raw_token,
        max_age=int(PENDING_LINK_TTL.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path=PENDING_LINK_COOKIE_PATH,
    )


def _clear_pending_link_cookie() -> Cookie:
    """Build a cookie deletion (Max-Age=0) for ``PENDING_LINK_COOKIE_NAME``."""
    return Cookie(
        key=PENDING_LINK_COOKIE_NAME,
        value="",
        max_age=0,
        httponly=True,
        secure=True,
        samesite="lax",
        path=PENDING_LINK_COOKIE_PATH,
    )


class LinkConfirmFormData(Struct):
    """POST payload for ``POST /auth/link-confirm``."""

    password: str


class LinkConfirmController(Controller):
    """Password → OIDC link-confirm flow."""

    include_in_schema = False
    dependencies = {  # noqa: RUF012
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {"UserService": UserService}  # noqa: RUF012
    cache = False
    exclude_from_auth = True

    @get(
        component="auth/link-confirm",
        name="link-confirm.show",
        path="/auth/link-confirm/",
    )
    async def show(
        self,
        request: Request,
        users_service: UserService,
    ) -> dict[str, Any] | Response:
        """Render the link-confirm form.

        Reads the ``pending_link_token`` cookie, validates the
        underlying ``PendingOidcLink`` row, and ships the email +
        provider to the Inertia page so the user knows what they're
        about to confirm.
        """
        link = await self._load_link(request, users_service)
        if link is None:
            return self._reject(request, "expired")
        return {
            "email": link.email,
            "provider": link.provider,
            "providerLabel": _provider_label(link.provider),
        }

    @post(
        component="auth/link-confirm",
        name="link-confirm.submit",
        path="/auth/link-confirm/",
    )
    async def submit(
        self,
        request: Request,
        users_service: UserService,
        data: LinkConfirmFormData,
    ) -> Response | InertiaRedirect:
        """Verify password, atomically link OIDC identity, sign in."""
        link = await self._load_link(request, users_service)
        if link is None:
            return self._reject(request, "expired")

        db_session = users_service.repository.session
        # Undefer hashed_password — it's a deferred column on User, and
        # the check + crypt.verify_password call below would otherwise
        # trigger an implicit per-attribute load that raises
        # MissingGreenlet under AsyncSession.
        user = await db_session.scalar(
            select(User)
            .where(User.id == link.target_user_id)
            .options(undefer(User.hashed_password))
        )
        if user is None or user.hashed_password is None:
            # Edge case: user was deleted, or their password was cleared
            # between the OIDC handshake and this POST. Refuse cleanly.
            return self._reject(request, "no_password_user")

        password_ok = await crypt.verify_password(data.password, user.hashed_password)
        if not password_ok:
            failed = await increment_failed_attempts(db_session, link.id)
            await db_session.commit()
            if failed >= 3:  # noqa: PLR2004 — threshold from design (#8)
                response = self._reject(request, "too_many_attempts")
                return response
            flash(
                request,
                "Incorrect password. Try again.",
                category="error",
            )
            return InertiaRedirect(request, request.url_for("link-confirm.show"))

        # CAS-claim the row. A parallel POST that already consumed it
        # loses here and is bounced to the generic "expired" page.
        claimed = await claim_pending_link_consumed(db_session, link.id)
        if not claimed:
            await db_session.commit()
            return self._reject(request, "expired")

        # Insert the OAuth row + clear the password in the same
        # transaction. The uq_oauth_user_singleton unique constraint
        # from PR-1 protects against double-link races.
        db_session.add(
            UserOauthAccount(
                user_id=user.id,
                oauth_name=link.provider,
                account_id=link.subject,
                account_email=link.email,
                # See _oidc_resolver.py for the access_token="" note.
                access_token="",
                scopes=None,
            )
        )
        user.hashed_password = None
        if not user.is_verified:
            user.is_verified = True
            user.verified_at = datetime.now(UTC).date()
        if user.name is None and link.name is not None:
            user.name = link.name
        await db_session.commit()

        # Session rotation: link-confirm is a credential change. The
        # design (#9) says this transition invalidates other sessions
        # too — handled inside reauthenticate_session.
        await reauthenticate_session(request, db_session, user_email=user.email)
        request.session["user_id"] = user.email
        request.session["auth_method"] = link.provider
        request.session["last_auth_at"] = datetime.now(UTC).isoformat()

        request.app.emit(
            "oidc_account_linked",
            user_email=user.email,
            provider=link.provider,
            account_email=link.email,
        )

        flash(
            request,
            f"You linked {_provider_label(link.provider)} to your "
            "Certora account. Welcome back.",
            category="info",
        )
        response = InertiaRedirect(request, request.url_for("dashboard"))
        response.cookies.append(_clear_pending_link_cookie())
        return response

    async def _load_link(
        self,
        request: Request,
        users_service: UserService,
    ) -> PendingOidcLink | None:
        """Resolve the cookie to a usable ``PendingOidcLink`` or ``None``.

        Returns ``None`` for any unusable state (missing cookie,
        no matching row, consumed, expired, locked) — controllers
        render the same generic page regardless of the cause
        (anti-enumeration of pending-link state).
        """
        raw_token = request.cookies.get(PENDING_LINK_COOKIE_NAME)
        if not raw_token:
            return None
        token_hash = hmac_sha256(raw_token)
        db_session = users_service.repository.session
        link = await find_pending_link_by_token_hash(db_session, token_hash)
        try:
            assert_pending_link_usable(link)
        except PendingLinkUnusableError:
            return None
        return link

    def _reject(self, request: Request, _reason: str) -> Response:
        """Generic refusal — flash + redirect to login + clear cookie.

        ``_reason`` is for logs only; the response shape is identical
        across all rejection paths (anti-enumeration).
        """
        flash(
            request,
            "This sign-in link has expired. Please start again.",
            category="error",
        )
        response = InertiaRedirect(request, request.url_for("login"))
        response.cookies.append(_clear_pending_link_cookie())
        return response


def _provider_label(provider: str) -> str:
    """Human-readable label for a provider value."""
    return {
        "google": "Google",
        "microsoft": "Microsoft",
        "github": "GitHub",
    }.get(provider, provider.capitalize())


# Helpers re-exported for the OidcController's PendingLinkRequired branch.


async def mint_pending_oidc_link(
    db_session: Any,
    *,
    exc: PendingLinkRequired,
) -> tuple[str, PendingOidcLink]:
    """Generate a random token, hash it, and insert the row.

    Caller is responsible for committing the session. Returns the
    plaintext token (to put in the cookie) and the inserted row (for
    diagnostics / response routing).
    """
    raw_token = secrets.token_urlsafe(32)
    token_hash = hmac_sha256(raw_token)
    now = datetime.now(UTC)
    row = PendingOidcLink(
        target_user_id=exc.target_user_id,
        provider=exc.identity.provider.value,
        subject=exc.identity.subject,
        email=exc.identity.email,
        name=exc.identity.name,
        token_hash=token_hash,
        expires_at=now + PENDING_LINK_TTL,
    )
    db_session.add(row)
    return raw_token, row


def __getattr__(name: str) -> Any:  # pragma: no cover
    """Help Litestar resolve forward refs during route introspection."""
    if name == "_provider_label":
        return _provider_label
    raise AttributeError(name)
