# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helpers for the PendingOidcLink state machine.

Server-side state for the password → OIDC discovery-mode link-confirm
flow. The cookie carries only a high-entropy token; identity payload
lives in the row.

Single-lookup-path invariant: no other code may issue
``select(PendingOidcLink)`` outside this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import case, select, update

from cert_ra.db.models import PendingOidcLink

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


FAILED_ATTEMPT_THRESHOLD = 3


class PendingLinkUnusableError(Exception):
    """Raised by ``assert_pending_link_usable`` for any disqualifying state."""

    def __init__(self, reason: str) -> None:
        """Initialize with a structured reason string."""
        super().__init__(reason)
        self.reason = reason


async def find_pending_link_by_token_hash(
    db: AsyncSession, token_hash: str
) -> PendingOidcLink | None:
    """The ONLY function that reads a PendingOidcLink row."""
    return await db.scalar(  # type: ignore[no-any-return]
        select(PendingOidcLink).where(PendingOidcLink.token_hash == token_hash)
    )


def assert_pending_link_usable(link: PendingOidcLink | None) -> None:
    """Single source of truth for pending-link state checks.

    Raises:
        PendingLinkUnusableError: With a structured reason; controllers
            render a generic page regardless.
    """
    if link is None:
        raise PendingLinkUnusableError("not_found")
    if link.consumed_at is not None:
        raise PendingLinkUnusableError("consumed")
    if link.expires_at <= datetime.now(UTC):
        raise PendingLinkUnusableError("expired")
    if link.failed_attempts >= FAILED_ATTEMPT_THRESHOLD:
        raise PendingLinkUnusableError("locked")


async def claim_pending_link_consumed(db: AsyncSession, link_id: UUID) -> bool:
    """Atomic CAS — returns True iff this caller is the consumer.

    The WHERE predicate ``consumed_at IS NULL`` short-circuits parallel
    POSTs.
    """
    result = await db.execute(
        update(PendingOidcLink)
        .where(
            PendingOidcLink.id == link_id,
            PendingOidcLink.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(UTC))
        .returning(PendingOidcLink.id)
    )
    return result.scalar_one_or_none() is not None


async def increment_failed_attempts(db: AsyncSession, link_id: UUID) -> int:
    """Atomic increment of ``failed_attempts`` with auto-consume on threshold.

    Used by the link-confirm wrong-password path. Single SQL statement
    (not read-modify-write) so two parallel wrong-password POSTs cannot
    both read ``failed_attempts=N`` and both write ``N+1``.

    Returns:
        The new ``failed_attempts`` value after the increment.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(PendingOidcLink)
        .where(
            PendingOidcLink.id == link_id,
            PendingOidcLink.consumed_at.is_(None),
        )
        .values(
            failed_attempts=PendingOidcLink.failed_attempts + 1,
            consumed_at=case(
                (
                    PendingOidcLink.failed_attempts + 1 >= FAILED_ATTEMPT_THRESHOLD,
                    now,
                ),
                else_=PendingOidcLink.consumed_at,
            ),
        )
        .returning(PendingOidcLink.failed_attempts)
    )
    row = result.scalar_one_or_none()
    return int(row) if row is not None else 0
