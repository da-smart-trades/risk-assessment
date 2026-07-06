# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helpers for the PendingProviderSwitch state machine.

Server-side state for the enforcement self-migration flow (OIDC sign-in
via wrong provider → authenticate at the required provider → atomic
UserOauthAccount swap).

Single-lookup-path invariant: no other code may issue
``select(PendingProviderSwitch)`` outside this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import select, update

from cert_ra.db.models import PendingProviderSwitch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PendingProviderSwitchUnusableError(Exception):
    """Raised by ``assert_pending_provider_switch_usable``."""

    def __init__(self, reason: str) -> None:
        """Initialize with a structured reason string."""
        super().__init__(reason)
        self.reason = reason


async def find_pending_provider_switch_by_token_hash(
    db: AsyncSession, token_hash: str
) -> PendingProviderSwitch | None:
    """The ONLY function that reads a PendingProviderSwitch row."""
    return await db.scalar(  # type: ignore[no-any-return]
        select(PendingProviderSwitch).where(
            PendingProviderSwitch.token_hash == token_hash
        )
    )


def assert_pending_provider_switch_usable(
    switch: PendingProviderSwitch | None,
) -> None:
    """Validate the row; raise the matching XUnusableError on bad state."""
    if switch is None:
        raise PendingProviderSwitchUnusableError("not_found")
    if switch.consumed_at is not None:
        raise PendingProviderSwitchUnusableError("consumed")
    if switch.expires_at <= datetime.now(UTC):
        raise PendingProviderSwitchUnusableError("expired")


async def claim_pending_provider_switch_consumed(
    db: AsyncSession, switch_id: UUID
) -> bool:
    """Atomic CAS — first POST wins."""
    result = await db.execute(
        update(PendingProviderSwitch)
        .where(
            PendingProviderSwitch.id == switch_id,
            PendingProviderSwitch.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(UTC))
        .returning(PendingProviderSwitch.id)
    )
    return result.scalar_one_or_none() is not None
