# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Enforcement self-migration flow (OIDC, wrong provider).

When a user whose team now enforces provider ``Y`` signs in via their
currently-linked provider ``X``, the OIDC resolver raises
``ProviderNotPermittedError``. Rather than dead-ending the user, the
OIDC controller routes them here:

1. ``start_provider_switch`` stashes the validated ``X`` identity in a
   server-side ``PendingProviderSwitch`` row, sets a short-TTL cookie
   carrying only the row's token, and redirects the user to
   ``/auth/<Y>/login`` to authenticate at the required provider.
2. The ``Y`` callback runs the normal OIDC handshake (so we get a
   validated ``Y`` identity), then — seeing the switch cookie —
   ``complete_provider_switch`` verifies the two identities belong to
   the same person (case-insensitive email match) and atomically swaps
   the user's ``UserOauthAccount`` from ``X`` to ``Y``.

The swap is a sensitive credential change: it rotates the session and
invalidates the user's other sessions via ``reauthenticate_session``
(design #9). Email mismatch consumes the row without swapping (the row
can't be replayed) and refuses.

Per-team IDP enforcement — Enforcement migration flow.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from litestar import Controller, Request, post
from litestar.datastructures import Cookie
from litestar.di import Provide
from litestar.exceptions import NotFoundException
from litestar.response import Redirect
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import delete, select

from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.lib.pending_provider_switches import (
    PendingProviderSwitchUnusableError,
    assert_pending_provider_switch_usable,
    claim_pending_provider_switch_consumed,
    find_pending_provider_switch_by_token_hash,
)
from cert_ra.api.lib.session_rotation import reauthenticate_session
from cert_ra.api.lib.team_policy import enforced_provider_for_user
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import PendingProviderSwitch, UserOauthAccount
from cert_ra.db.models.user import User
from cert_ra.settings.api import get_feature_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.api.lib.oidc.identity import ExtractedIdentity

__all__ = (
    "PENDING_SWITCH_COOKIE_NAME",
    "PENDING_SWITCH_TTL",
    "ProviderSwitchController",
    "complete_provider_switch",
    "has_pending_switch_cookie",
    "issue_pending_switch_cookie",
    "start_provider_switch",
)

# The cookie must ride along the second handshake's callback
# (``/auth/<Y>/callback``), so it is scoped to the whole ``/auth``
# subtree rather than a single endpoint.
PENDING_SWITCH_COOKIE_NAME = "pending_provider_switch_token"
PENDING_SWITCH_TTL = timedelta(minutes=10)
_PENDING_SWITCH_COOKIE_PATH = "/auth"


def issue_pending_switch_cookie(raw_token: str) -> Cookie:
    """Build the cookie that carries the pending-switch token."""
    return Cookie(
        key=PENDING_SWITCH_COOKIE_NAME,
        value=raw_token,
        max_age=int(PENDING_SWITCH_TTL.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path=_PENDING_SWITCH_COOKIE_PATH,
    )


def _clear_pending_switch_cookie() -> Cookie:
    """Build a deletion (Max-Age=0) for the pending-switch cookie."""
    return Cookie(
        key=PENDING_SWITCH_COOKIE_NAME,
        value="",
        max_age=0,
        httponly=True,
        secure=True,
        samesite="lax",
        path=_PENDING_SWITCH_COOKIE_PATH,
    )


async def start_provider_switch(
    request: Request,
    db: AsyncSession,
    *,
    target_user_id: UUID,
    source_identity: ExtractedIdentity,
    required_provider: str,
) -> Redirect:
    """Stash the source identity and redirect to the required provider.

    Mints a ``PendingProviderSwitch`` row from the validated wrong-
    provider (source) identity, attaches the token cookie, and sends the
    user to ``/auth/<required>/login``. The caller is responsible for
    nothing else — the row is committed here.

    Args:
        request: The current request (for ``url_for`` + commit session).
        db: Async SQLAlchemy session.
        target_user_id: The resolved user whose link will be swapped.
        source_identity: Validated identity from the wrong-provider
            handshake.
        required_provider: The provider the team enforces.

    Returns:
        A redirect to the required provider's login, carrying the
        pending-switch cookie.
    """
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        PendingProviderSwitch(
            target_user_id=target_user_id,
            source_provider=source_identity.provider.value,
            source_subject=source_identity.subject,
            source_email=source_identity.email,
            target_provider=required_provider,
            token_hash=hmac_sha256(raw_token),
            expires_at=now + PENDING_SWITCH_TTL,
        )
    )
    await db.commit()
    flash(
        request,
        f"Your team now requires {_provider_label(required_provider)} "
        "sign-in. Sign in there to finish switching.",
        category="info",
    )
    response = Redirect(
        request.url_for("oidc.login", provider=required_provider),
    )
    response.cookies.append(issue_pending_switch_cookie(raw_token))
    return response


async def start_settings_provider_switch(
    request: Request,
    db: AsyncSession,
    *,
    user: User,
    required_provider: str,
) -> Redirect:
    """Begin a settings-initiated switch toward ``required_provider``.

    Unlike ``start_provider_switch`` (driven by a fresh wrong-provider
    handshake), the source identity here comes from the signed-in user's
    existing ``UserOauthAccount``. If the user has no linked provider
    (password account), there is nothing to stash — we send them straight
    to the required provider, where the existing password→OIDC
    link-confirm flow takes over.

    Returns:
        A redirect to ``/auth/<required>/login`` (with the pending-switch
        cookie when an existing OAuth link is being swapped).
    """
    account = await db.scalar(
        select(UserOauthAccount).where(UserOauthAccount.user_id == user.id).limit(1)
    )
    if account is None:
        # Password user — the link-confirm migration handles this.
        return Redirect(
            request.url_for("oidc.login", provider=required_provider),
        )
    raw_token = secrets.token_urlsafe(32)
    db.add(
        PendingProviderSwitch(
            target_user_id=user.id,
            source_provider=account.oauth_name,
            source_subject=account.account_id,
            source_email=account.account_email,
            target_provider=required_provider,
            token_hash=hmac_sha256(raw_token),
            expires_at=datetime.now(UTC) + PENDING_SWITCH_TTL,
        )
    )
    await db.commit()
    response = Redirect(request.url_for("oidc.login", provider=required_provider))
    response.cookies.append(issue_pending_switch_cookie(raw_token))
    return response


async def complete_provider_switch(
    request: Request,
    db: AsyncSession,
    identity: ExtractedIdentity,
    *,
    raw_token: str,
    redirect_to: str,
) -> Redirect | InertiaRedirect:
    """Finish a switch: verify, atomically swap the link, sign in.

    Called from the OIDC callback when the pending-switch cookie is
    present. ``identity`` is the validated identity from the required
    (target) provider's handshake.

    Returns:
        A redirect to ``redirect_to`` on success, or to ``/login`` on
        any refusal (anti-enumeration: identical shape for every
        failure cause). The pending-switch cookie is cleared either way.
    """
    token_hash = hmac_sha256(raw_token)
    switch = await find_pending_provider_switch_by_token_hash(db, token_hash)
    try:
        assert_pending_provider_switch_usable(switch)
    except PendingProviderSwitchUnusableError:
        return _reject(request)
    assert switch is not None  # narrowed by assert_..._usable

    # The callback provider must be the one this switch targets, and the
    # two identities must be the same person (case-insensitive email).
    target_user = await db.get(User, switch.target_user_id)
    mismatch = (
        switch.target_provider != identity.provider.value
        or switch.source_email.lower() != identity.email.lower()
        or target_user is None
    )
    if mismatch:
        # Consume the row so it can't be replayed, but do NOT swap.
        await claim_pending_provider_switch_consumed(db, switch.id)
        await db.commit()
        return _reject(request)

    # Atomic CAS — the first callback to claim the row wins; a parallel
    # replay loses and is bounced.
    claimed = await claim_pending_provider_switch_consumed(db, switch.id)
    if not claimed:
        await db.commit()
        return _reject(request)

    assert target_user is not None  # narrowed above
    await db.execute(
        delete(UserOauthAccount).where(
            UserOauthAccount.user_id == target_user.id,
            UserOauthAccount.oauth_name == switch.source_provider,
        )
    )
    db.add(
        UserOauthAccount(
            user_id=target_user.id,
            oauth_name=switch.target_provider,
            account_id=identity.subject,
            account_email=identity.email,
            # SSO-only: we never call provider APIs on the user's behalf.
            access_token="",
            scopes=None,
        )
    )
    await db.commit()

    # Sensitive credential change — rotate + invalidate other sessions.
    await reauthenticate_session(request, db, user_email=target_user.email)
    request.session["user_id"] = target_user.email
    request.session["auth_method"] = switch.target_provider
    request.session["last_auth_at"] = datetime.now(UTC).isoformat()

    request.app.emit(
        "oidc_provider_switched",
        user_email=target_user.email,
        from_provider=switch.source_provider,
        to_provider=switch.target_provider,
    )
    flash(
        request,
        f"Your sign-in method was changed to "
        f"{_provider_label(switch.target_provider)}.",
        category="info",
    )
    response = InertiaRedirect(request, redirect_to or request.url_for("dashboard"))
    response.cookies.append(_clear_pending_switch_cookie())
    return response


def has_pending_switch_cookie(request: Request) -> str | None:
    """Return the raw pending-switch token from the request, if present."""
    return request.cookies.get(PENDING_SWITCH_COOKIE_NAME)


def _reject(request: Request) -> InertiaRedirect:
    """Generic refusal — flash + redirect to login + clear cookie."""
    flash(
        request,
        "This sign-in link has expired. Please start again.",
        category="error",
    )
    response = InertiaRedirect(request, request.url_for("login"))
    response.cookies.append(_clear_pending_switch_cookie())
    return response


def _provider_label(provider: str) -> str:
    """Human-readable label for a provider value."""
    return {
        "google": "Google",
        "microsoft": "Microsoft",
        "github": "GitHub",
    }.get(provider, provider.capitalize())


class ProviderSwitchController(Controller):
    """Settings-initiated 'switch sign-in provider' toward enforcement.

    Authenticated counterpart to the OIDC self-migration: lets a member
    proactively migrate to the provider their team enforces instead of
    waiting for the automatic switch on their next sign-in. The target
    must equal the user's enforced provider (design — Interaction with
    other flows). Dark while the feature flag is off (404).
    """

    include_in_schema = False
    cache = False
    dependencies = {  # noqa: RUF012
        "users_service": Provide(provide_users_service),
    }
    signature_namespace = {"UserService": UserService}  # noqa: RUF012

    @post(
        name="auth.switch-provider",
        path="/profile/switch-provider/{provider:str}",
        status_code=303,
    )
    async def switch(
        self,
        request: Request,
        users_service: UserService,
        current_user: User,
        provider: str,
    ) -> Redirect | InertiaRedirect:
        """Begin switching the signed-in user to ``provider``."""
        if not get_feature_settings().enforced_provider:
            raise NotFoundException("Not found")
        db = users_service.repository.session
        target = provider.strip().lower()
        required = await enforced_provider_for_user(db, current_user)
        if required is None or target != required:
            flash(
                request,
                "No sign-in provider switch is required.",
                category="info",
            )
            return InertiaRedirect(request, request.url_for("profile.show"))
        existing = await db.scalar(
            select(UserOauthAccount).where(
                UserOauthAccount.user_id == current_user.id,
                UserOauthAccount.oauth_name == target,
            )
        )
        if existing is not None:
            flash(
                request,
                f"You already sign in with {_provider_label(target)}.",
                category="info",
            )
            return InertiaRedirect(request, request.url_for("profile.show"))
        return await start_settings_provider_switch(
            request, db, user=current_user, required_provider=target
        )
