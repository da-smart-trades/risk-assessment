# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Per-team IDP enforcement check.

A team owner can lock their team to a single OIDC provider
(``Team.enforced_provider``). Once set, members may only authenticate
via that provider — both password login and other-provider OIDC
sign-in are refused and routed into the self-migration flow.

``assert_team_provider_allowed`` is the single choke point. It is
called from:

- the OIDC resolver, after the canonical-identity match
  (``attempted_provider=identity.provider.value``); and
- the password login handler, before the password hash is checked
  (``attempted_provider=None``).

The whole mechanism is dark until ``cert_ra_features_enforced_provider``
is flipped on: while the flag is False this function is a no-op so a
stray ``enforced_provider`` value can never lock anyone out mid-rollout.

Per-team IDP enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from cert_ra.api.domain.accounts.services._oidc_resolver import (
    ProviderNotPermittedError,
)
from cert_ra.api.lib.operator_roles import is_root_user
from cert_ra.db.models import Team, TeamMember, User, UserOauthAccount
from cert_ra.settings.api import get_feature_settings

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = (
    "assert_team_provider_allowed",
    "enforced_provider_for_user",
    "find_conflicting_enforcement_users",
    "find_stuck_members",
)


async def find_conflicting_enforcement_users(
    db: AsyncSession,
) -> list[dict[str, object]]:
    """Users who belong to 2+ teams enforcing *different* providers.

    The single-IDP-per-user invariant means such a user cannot satisfy
    every team at once — whichever provider they migrate to locks them
    out of the other team's sign-in. This is a documented known
    limitation (design open question #3); run it as a periodic check to
    surface at-risk users before they get stuck.

    Returns:
        ``[{userId, providers: [...]}]``, one row per conflicting user,
        empty when there is no conflict.
    """
    rows = await db.execute(
        select(
            TeamMember.user_id,
            func.array_agg(func.distinct(Team.enforced_provider)),
        )
        .join(Team, Team.id == TeamMember.team_id)
        .where(Team.enforced_provider.is_not(None))
        .group_by(TeamMember.user_id)
        .having(func.count(func.distinct(Team.enforced_provider)) > 1)
    )
    return [
        {"userId": str(user_id), "providers": sorted(providers)}
        for user_id, providers in rows
    ]


async def enforced_provider_for_user(db: AsyncSession, user: User) -> str | None:
    """Return the provider a user's team enforces, or ``None``.

    If the user belongs to multiple enforcing teams the first match wins
    (the single-IDP-per-user invariant means a coherent rollout uses one
    provider). No-op (returns ``None``) while the feature flag is off.
    """
    if not get_feature_settings().enforced_provider:
        return None
    return await db.scalar(
        select(Team.enforced_provider)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            TeamMember.user_id == user.id,
            Team.enforced_provider.is_not(None),
        )
        .limit(1)
    )


async def assert_team_provider_allowed(
    db: AsyncSession,
    user: User,
    *,
    attempted_provider: str | None,
) -> None:
    """Raise if any of ``user``'s teams enforces a different provider.

    Args:
        db: Async SQLAlchemy session.
        user: The resolved user attempting to sign in.
        attempted_provider: The provider value used to authenticate
            (``"google"`` | ``"microsoft"`` | ``"github"``), or ``None``
            for password sign-in.

    Raises:
        ProviderNotPermittedError: A team the user belongs to enforces a
            provider other than ``attempted_provider``. Carries the first
            offending team's id and required provider. A password attempt
            (``attempted_provider=None``) trips on any enforcing team.

    The break-glass root account (``CERT_RA_SUPERUSER_EMAIL``) is exempt:
    it must always be able to sign in with a password so an IdP outage
    can never lock the operator out. Operator-team enforcement applies
    independently of the customer-rollout feature flag (the operator team
    forcing its IdP is a security baseline, not a gated rollout);
    customer-team enforcement remains gated by the flag.
    """
    if is_root_user(user.email):
        return

    customer_enforcement_on = get_feature_settings().enforced_provider
    rows = await db.execute(
        select(Team.id, Team.enforced_provider, Team.is_operator)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(
            TeamMember.user_id == user.id,
            Team.enforced_provider.is_not(None),
        )
    )
    for team_id, enforced_provider, is_operator in rows:
        if enforced_provider == attempted_provider:
            continue
        if not (is_operator or customer_enforcement_on):
            continue
        raise ProviderNotPermittedError(
            team_id=team_id,
            required_provider=enforced_provider,
            attempted_provider=attempted_provider,
            target_user_id=user.id,
        )


async def find_stuck_members(
    db: AsyncSession, team_id: UUID
) -> list[dict[str, object]]:
    """Members of ``team_id`` who can't self-migrate to the enforced provider.

    A member is "stuck" iff (design — The "stuck list" admin view):
    (a) the team has ``enforced_provider`` + ``enforced_provider_set_at``
    set, (b) the member has no ``UserOauthAccount`` for the enforced
    provider, and (c) they haven't signed in since the policy changed
    (never activated, or activated before ``enforced_provider_set_at``).

    Returns:
        A list of ``{id, email, activatedAt, hasActiveLockout}`` dicts,
        empty if the team has no enforcement set.
    """
    team = await db.get(Team, team_id)
    if (
        team is None
        or team.enforced_provider is None
        or team.enforced_provider_set_at is None
    ):
        return []

    has_provider = (
        select(UserOauthAccount.id)
        .where(
            UserOauthAccount.user_id == User.id,
            UserOauthAccount.oauth_name == team.enforced_provider,
        )
        .exists()
    )
    rows = await db.execute(
        select(User.id, User.email, User.activated_at, User.has_active_lockout)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(
            TeamMember.team_id == team_id,
            ~has_provider,
            (User.activated_at.is_(None))
            | (User.activated_at < team.enforced_provider_set_at),
        )
        .order_by(User.email)
    )
    return [
        {
            "id": str(user_id),
            "email": email,
            "activatedAt": activated_at.isoformat() if activated_at else None,
            "hasActiveLockout": bool(has_lockout),
        }
        for user_id, email, activated_at, has_lockout in rows
    ]
