# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helpers for the TeamInvitation state machine.

Single-lookup-path invariant: **no other code may issue a
``select(TeamInvitation)`` or ``update(TeamInvitation)`` during the
sign-in / invitation-acceptance flows.** All reads go through
``find_invitation_by_token_hash``; all state transitions go through
the ``claim_*`` helpers. Enforced at CI by ``canonical_helper_check.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import select, update

from cert_ra.db.models import TeamInvitation
from cert_ra.db.models.user import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class InvitationUnusableError(Exception):
    """Raised by ``assert_invitation_usable`` for any disqualifying state.

    The error carries a structured reason for logs; controllers render
    a generic user-facing message that does not distinguish causes
    (anti-enumeration).
    """

    def __init__(self, reason: str) -> None:
        """Initialize with a structured reason string."""
        super().__init__(reason)
        self.reason = reason


async def find_invitation_by_token_hash(
    db: AsyncSession, token_hash: str
) -> TeamInvitation | None:
    """The ONLY function that reads a TeamInvitation by token_hash.

    No other code path may issue ``select(TeamInvitation)`` during the
    invitation flows. Pairs with ``assert_invitation_usable`` below.
    """
    return await db.scalar(  # type: ignore[no-any-return]
        select(TeamInvitation).where(TeamInvitation.token_hash == token_hash)
    )


def assert_invitation_usable(
    invitation: TeamInvitation | None,
    *,
    expected_user_id: UUID | None = None,
) -> None:
    """Single source of truth for invitation-state checks.

    Args:
        invitation: Row returned by ``find_invitation_by_token_hash``,
            or ``None``.
        expected_user_id: For CROSS_TEAM_JOIN, the signed-in user's id —
            ``invitation.user_id`` must match. Pass ``None`` to skip
            this check (FIRST_TIME_ACTIVATION callers don't have a
            signed-in user yet).

    Raises:
        InvitationUnusableError: With a structured reason. Controllers
            render the same generic page regardless of the reason.
    """
    if invitation is None:
        raise InvitationUnusableError("not_found")
    if invitation.revoked_at is not None:
        raise InvitationUnusableError("revoked")
    if invitation.accepted_at is not None:
        raise InvitationUnusableError("already_accepted")
    if invitation.is_accepted:
        # Legacy boolean flag for pre-OIDC-SSO invitations.
        raise InvitationUnusableError("already_accepted")
    now = datetime.now(UTC)
    if invitation.expires_at is not None and invitation.expires_at <= now:
        raise InvitationUnusableError("expired")
    if (
        expected_user_id is not None
        and invitation.user_id is not None
        and invitation.user_id != expected_user_id
    ):
        raise InvitationUnusableError("user_mismatch")


async def claim_user_activation(
    db: AsyncSession,
    user_id: UUID,
    *,
    hashed_password: str | None = None,
) -> bool:
    """Atomically transition User from pre-activation to activated.

    Returns ``True`` if this caller won the activation race, ``False``
    if someone else activated first (the WHERE predicate ``activated_at
    IS NULL`` short-circuits). Callers MUST short-circuit on ``False``
    — no further writes (UserOauthAccount insert, session creation,
    etc.) should follow a lost claim.

    Args:
        db: Async session.
        user_id: The pre-provisioned User to activate.
        hashed_password: When set, written atomically with
            ``activated_at``. Used by the password-set first-time
            activation handler so two parallel POSTs can't both write
            different hashes.

    Returns:
        True on race win, False on race loss.
    """
    now = datetime.now(UTC)
    values: dict[str, object] = {"activated_at": now, "is_verified": True}
    if hashed_password is not None:
        values["hashed_password"] = hashed_password
    result = await db.execute(
        update(User)
        .where(User.id == user_id, User.activated_at.is_(None))
        .values(**values)
        .returning(User.id)
    )
    return result.scalar_one_or_none() is not None


async def claim_invitation_accepted(db: AsyncSession, invitation_id: UUID) -> bool:
    """Atomically transition TeamInvitation to accepted.

    Mirror of ``claim_user_activation`` for the invitation row. The
    WHERE predicate excludes already-accepted, revoked, or
    is_accepted=True rows.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(TeamInvitation)
        .where(
            TeamInvitation.id == invitation_id,
            TeamInvitation.accepted_at.is_(None),
            TeamInvitation.revoked_at.is_(None),
            TeamInvitation.is_accepted.is_(False),
        )
        .values(accepted_at=now, is_accepted=True)
        .returning(TeamInvitation.id)
    )
    return result.scalar_one_or_none() is not None


async def claim_invitation_revoked(db: AsyncSession, invitation_id: UUID) -> bool:
    """Atomic revoke.

    Used by reissue and decline. Returns ``False`` if the invitation
    was already accepted (don't revoke an accepted invitation; the
    acceptance is the terminal state) or already revoked.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(TeamInvitation)
        .where(
            TeamInvitation.id == invitation_id,
            TeamInvitation.accepted_at.is_(None),
            TeamInvitation.revoked_at.is_(None),
            TeamInvitation.is_accepted.is_(False),
        )
        .values(revoked_at=now)
        .returning(TeamInvitation.id)
    )
    return result.scalar_one_or_none() is not None
