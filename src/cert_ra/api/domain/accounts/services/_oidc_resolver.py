# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""OIDC identity resolver — admin-pre-provisioned user lookup.

The resolver turns a validated ``ExtractedIdentity`` (from
``cert_ra.api.lib.oidc.identity``) into either a signed-in ``User``
or a structured exception that the controller routes to a landing
page.

The lookup is deliberately simple — admin-driven provisioning means
no auto-create paths:

1. Find existing identity by (provider, subject). Canonical key.
2. Otherwise look up the pre-provisioned ``User`` by verified email.
3. If found and ALREADY has an OAuth row → ``WrongProviderError`` (the
   single-IDP-per-user invariant from PR-1's
   ``uq_oauth_user_singleton``).
4. If found and ALREADY has a password but no OAuth →
   ``PendingLinkRequired`` (the password→OIDC link-confirm flow lands
   in PR-2b).
5. If found and pre-activation → activate atomically via the
   PR-1 ``claim_user_activation`` helper, insert the
   ``UserOauthAccount``, return the user.
6. No matching User → ``UnknownUserError``. Controller routes to
   ``/auth/invitation-required`` (lands in PR-6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import undefer

from cert_ra.api.lib.invitations import claim_user_activation
from cert_ra.db.models.oauth_account import UserOauthAccount
from cert_ra.db.models.user import User

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.api.lib.oidc.identity import ExtractedIdentity


class UnknownUserError(Exception):
    """The OIDC sign-in did not match any admin-provisioned User.

    Membership is admin-driven. We do not create User rows from token
    claims. The controller routes to ``/auth/invitation-required`` —
    the page wording does NOT reveal whether the email exists
    elsewhere, does NOT name a team, does NOT name an admin.
    """


class RootCannotUseIdpError(Exception):
    """The OIDC sign-in matched the break-glass root account.

    The root (``CERT_RA_SUPERUSER_EMAIL``) must NEVER be linked to or
    sign in via an IdP — an IdP outage must not be able to lock the
    operator out. The controller routes this back to the password login
    page with a "this account uses password sign-in" message.
    """


class WrongProviderError(Exception):
    """The OIDC sign-in matched a user already linked to a different provider.

    Each user has at most one linked OIDC provider (PR-1's
    ``uq_oauth_user_singleton``). Switching providers must originate
    from a session authenticated with the current provider (PR-7's
    enforcement self-migration or settings-initiated switch), not via
    opportunistic sign-in with the new one.
    """

    def __init__(
        self,
        target_user_id: UUID,
        existing_provider: str,
        attempted_provider: str,
    ) -> None:
        super().__init__(
            f"Account is linked to {existing_provider!r}, not {attempted_provider!r}"
        )
        self.target_user_id = target_user_id
        self.existing_provider = existing_provider
        self.attempted_provider = attempted_provider


class ProviderNotPermittedError(Exception):
    """A team the resolved user belongs to enforces a different provider.

    Raised by ``assert_team_provider_allowed`` (``cert_ra.api.lib
    .team_policy``) from both the OIDC resolver and the password login
    handler. Carries enough context for the controller to either route
    the user into the enforcement self-migration flow (when the required
    provider is available) or to the ``/auth/team-policy`` dead-end.

    ``attempted_provider`` is ``None`` for password sign-in (no OIDC
    provider was used).
    """

    def __init__(
        self,
        *,
        team_id: UUID,
        required_provider: str,
        attempted_provider: str | None,
        target_user_id: UUID,
    ) -> None:
        super().__init__(
            f"Team {team_id} requires sign-in via {required_provider!r}, "
            f"not {attempted_provider!r}"
        )
        self.team_id = team_id
        self.required_provider = required_provider
        self.attempted_provider = attempted_provider
        self.target_user_id = target_user_id


class PendingLinkRequired(Exception):  # noqa: N818 — flow-control signal, not an error
    """The OIDC sign-in matched a password-protected user.

    Defer linking until the user proves they also know the password.
    The controller stashes the validated identity in a
    ``PendingOidcLink`` row and redirects to ``/auth/link-confirm``.
    The link-confirm controller lands in PR-2b.
    """

    def __init__(
        self,
        identity: ExtractedIdentity,
        target_user_id: UUID,
    ) -> None:
        super().__init__("Pending link confirmation")
        self.identity = identity
        self.target_user_id = target_user_id


class OidcIdentityResolver:
    """Resolve an ``ExtractedIdentity`` to a User or raise.

    Stateless apart from the supplied DB session. No background work;
    every state transition is atomic.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def resolve(self, identity: ExtractedIdentity) -> User:
        """Run the 5-step lookup.

        Returns:
            The User the controller should sign in.

        Raises:
            UnknownUserError: No admin-provisioned User for this email.
            WrongProviderError: User exists but is linked to a
                different provider.
            PendingLinkRequired: User exists with a password but no
                OAuth — defer to link-confirm flow.
            ProviderNotPermittedError: User resolved, but a team they
                belong to enforces a different provider — defer to the
                enforcement self-migration flow.
        """
        # Step 1: canonical identity lookup.
        existing_oauth = await self.db.scalar(
            select(UserOauthAccount).where(
                UserOauthAccount.oauth_name == identity.provider.value,
                UserOauthAccount.account_id == identity.subject,
            )
        )
        if existing_oauth is not None:
            user = await self.db.get(User, existing_oauth.user_id)
            if user is None:
                # Orphan oauth row (user deleted, oauth row leaked).
                raise UnknownUserError
            _refuse_root(user)
            await self._assert_provider_allowed(user, identity)
            return user

        # Step 2: lookup by verified email.
        # Undefer hashed_password so the Step 4 check below doesn't trigger
        # an implicit per-attribute load — under AsyncSession that would
        # raise MissingGreenlet (deferred-column loads happen via sync
        # attribute access that can't reach the async greenlet).
        user = await self.db.scalar(
            select(User)
            .where(User.email == identity.email)
            .options(undefer(User.hashed_password))
        )
        if user is None:
            raise UnknownUserError
        # The break-glass root must never be linked to / signed in via an IdP.
        _refuse_root(user)

        # Step 3: already linked to a different provider.
        existing_link = await self.db.scalar(
            select(UserOauthAccount).where(UserOauthAccount.user_id == user.id).limit(1)
        )
        if existing_link is not None:
            raise WrongProviderError(
                target_user_id=user.id,
                existing_provider=existing_link.oauth_name,
                attempted_provider=identity.provider.value,
            )

        # Step 4: existing password-only user.
        if user.hashed_password is not None:
            raise PendingLinkRequired(identity=identity, target_user_id=user.id)

        # Enforcement: refuse first-time activation via a provider the
        # user's team forbids, before any OAuth row is written. A
        # conforming invitation carries ``force_provider`` so this only
        # trips on a hand-crafted sign-in attempt.
        await self._assert_provider_allowed(user, identity)

        # Step 5: first-time activation. The CAS short-circuits if
        # someone else just activated this user in a parallel POST.
        claimed = await claim_user_activation(self.db, user.id)
        if not claimed:
            # Race lost — re-load and treat as already-activated.
            await self.db.refresh(user)
            if user.activated_at is None:
                # Defensive: race claimant somehow didn't persist
                # activated_at. Refuse rather than risk dual writes.
                raise UnknownUserError
            return user  # type: ignore[no-any-return]

        self.db.add(
            UserOauthAccount(
                user_id=user.id,
                oauth_name=identity.provider.value,
                account_id=identity.subject,
                account_email=identity.email,
                # Per the design, we do NOT retain access_token for
                # SSO-only flows — we don't call provider APIs on
                # the user's behalf. Stored as empty string for the
                # NOT NULL constraint.
                access_token="",
                scopes=None,
            )
        )
        if not user.is_verified:
            user.is_verified = True
            user.verified_at = datetime.now(UTC).date()
        if user.name is None and identity.name is not None:
            user.name = identity.name
        await self.db.flush()
        return user  # type: ignore[no-any-return]

    async def _assert_provider_allowed(
        self, user: User, identity: ExtractedIdentity
    ) -> None:
        """Raise ``ProviderNotPermittedError`` if a team forbids this provider.

        Lazy import breaks the resolver ↔ team_policy cycle: team_policy
        imports ``ProviderNotPermittedError`` from this module.
        """
        from cert_ra.api.lib.team_policy import assert_team_provider_allowed

        await assert_team_provider_allowed(
            self.db, user, attempted_provider=identity.provider.value
        )


def _refuse_root(user: User) -> None:
    """Raise ``RootCannotUseIdpError`` if ``user`` is the break-glass root."""
    from cert_ra.api.lib.operator_roles import is_root_user

    if is_root_user(user.email):
        raise RootCannotUseIdpError
