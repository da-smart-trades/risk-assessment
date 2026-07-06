# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helpers for the UserUnlockToken state machine.

Single-use unlock links issued via email when a user's first IP lockout
triggers within the throttle window. Clicking clears every UserLockout
row for the user atomically.

Single-lookup-path invariant: no other code may issue
``select(UserUnlockToken)`` outside this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import select, update

from cert_ra.db.models import UserUnlockToken

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class UnlockTokenUnusableError(Exception):
    """Raised by ``assert_unlock_token_usable``."""

    def __init__(self, reason: str) -> None:
        """Initialize with a structured reason string."""
        super().__init__(reason)
        self.reason = reason


async def find_unlock_token_by_token_hash(
    db: AsyncSession, token_hash: str
) -> UserUnlockToken | None:
    """The ONLY function that reads an UserUnlockToken row."""
    return await db.scalar(  # type: ignore[no-any-return]
        select(UserUnlockToken).where(UserUnlockToken.token_hash == token_hash)
    )


def assert_unlock_token_usable(token: UserUnlockToken | None) -> None:
    """Validate the row; raise the matching XUnusableError on bad state."""
    if token is None:
        raise UnlockTokenUnusableError("not_found")
    if token.consumed_at is not None:
        raise UnlockTokenUnusableError("consumed")
    if token.expires_at <= datetime.now(UTC):
        raise UnlockTokenUnusableError("expired")


async def claim_unlock_token_consumed(db: AsyncSession, token_id: UUID) -> bool:
    """Atomic CAS — first click wins."""
    result = await db.execute(
        update(UserUnlockToken)
        .where(
            UserUnlockToken.id == token_id,
            UserUnlockToken.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(UTC))
        .returning(UserUnlockToken.id)
    )
    return result.scalar_one_or_none() is not None
