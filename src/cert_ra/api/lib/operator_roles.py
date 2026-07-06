# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Operator-team membership + role helpers (PR-8).

The operator team is the first-party platform team (``Team.is_operator``).
PR-8 layers two operator-only controls on top of normal team membership:

- **MFA posture** (Control 1): operators must sign in with a passkey;
  TOTP is refused. Enforced via ``assert_operator_mfa_posture`` in
  ``auth_lockout`` using ``user_is_operator`` from here.
- **Role split** (Control 2): ``operator_support`` (read-only) vs
  ``operator_tenant_admin`` (cross-customer writes). The role lives on
  the operator-team ``TeamMember.role``; write routes check
  ``is_operator_tenant_admin``.

Operator team hardening.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cert_ra.db.models import Team, TeamMember, TeamRoles
from cert_ra.settings.api import get_superuser_settings

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.db.models import User

__all__ = (
    "is_operator_tenant_admin",
    "is_root_user",
    "user_is_operator",
)


def is_root_user(email: str | None) -> bool:
    """True if ``email`` is the break-glass root account.

    The root is the single superuser configured via
    ``CERT_RA_SUPERUSER_EMAIL`` (``SuperuserSettings.email``). It is a
    deliberate break-glass account: it must NEVER be subject to operator
    IDP enforcement and must NEVER be linked to an OIDC provider, so an
    IdP outage can never lock the operator out. It signs in with password
    + passkey only. Comparison is case-insensitive.
    """
    if not email:
        return False
    return email.strip().lower() == get_superuser_settings().email.strip().lower()


async def user_is_operator(db: AsyncSession, user: User) -> bool:
    """True if ``user`` belongs to the operator team."""
    return await _user_is_operator_by_id(db, user.id)


async def is_operator_tenant_admin(db: AsyncSession, user: User) -> bool:
    """True if ``user`` is an ``operator_tenant_admin`` (or operator owner).

    ``operator_tenant_admin`` (and operator-team owners) may perform
    cross-customer write actions. ``operator_support`` and non-operators
    return ``False``.
    """
    role = await db.scalar(
        select(TeamMember.role)
        .join(Team, TeamMember.team_id == Team.id)
        .where(
            TeamMember.user_id == user.id,
            Team.is_operator.is_(True),
            (TeamMember.is_owner.is_(True))
            | (TeamMember.role == TeamRoles.OPERATOR_TENANT_ADMIN),
        )
        .limit(1)
    )
    return role is not None


async def _user_is_operator_by_id(db: AsyncSession, user_id: UUID) -> bool:
    """Membership check keyed by id (the single query path)."""
    found = await db.scalar(
        select(TeamMember.id)
        .join(Team, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == user_id, Team.is_operator.is_(True))
        .limit(1)
    )
    return found is not None
