# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helpers for the UserPasswordResetToken state machine.

Single-use password-reset links. The reset handler must NOT establish
a session, MUST NOT touch MFA factors or OAuth links, and MUST
invalidate all of the user's existing sessions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import select, update

from cert_ra.db.models import UserPasswordResetToken
from cert_ra.db.models.user import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PasswordResetTokenUnusableError(Exception):
    """Raised by ``assert_password_reset_token_usable``."""

    def __init__(self, reason: str) -> None:
        """Initialize with a structured reason string."""
        super().__init__(reason)
        self.reason = reason


async def find_password_reset_token_by_token_hash(
    db: AsyncSession, token_hash: str
) -> UserPasswordResetToken | None:
    """The ONLY function that reads a UserPasswordResetToken row."""
    return await db.scalar(  # type: ignore[no-any-return]
        select(UserPasswordResetToken).where(
            UserPasswordResetToken.token_hash == token_hash
        )
    )


def assert_password_reset_token_usable(
    token: UserPasswordResetToken | None,
) -> None:
    """Validate the row; raise the matching XUnusableError on bad state."""
    if token is None:
        raise PasswordResetTokenUnusableError("not_found")
    if token.consumed_at is not None:
        raise PasswordResetTokenUnusableError("consumed")
    if token.expires_at <= datetime.now(UTC):
        raise PasswordResetTokenUnusableError("expired")


async def claim_password_reset(
    db: AsyncSession,
    token_id: UUID,
    *,
    new_hashed_password: str,
) -> UUID | None:
    """Atomic compound: consume the token AND update the user's password.

    The caller MUST NOT update ``hashed_password`` independently — this
    helper is the only legitimate path. The CAS on
    ``consumed_at IS NULL`` short-circuits a parallel re-POST.

    Args:
        db: Async session.
        token_id: The reset-token row to consume.
        new_hashed_password: Already-hashed (argon2id) new password,
            ready to write into ``user_account.hashed_password``.

    Returns:
        The ``user_id`` of the user whose password was updated, or
        ``None`` if the token was already consumed (race lost).
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(UserPasswordResetToken)
        .where(
            UserPasswordResetToken.id == token_id,
            UserPasswordResetToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
        .returning(UserPasswordResetToken.user_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    user_id: UUID = row
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(hashed_password=new_hashed_password)
    )
    return user_id
