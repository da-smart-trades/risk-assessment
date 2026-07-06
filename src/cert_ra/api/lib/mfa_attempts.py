# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helpers for the MfaAttempt state machine.

Server-side per-attempt state (user_id, WebAuthn challenge, consume
flag) keyed by a high-entropy cookie token. The cookie carries only
the lookup token; no identity payload.

Single-attempt invariant: each row supports exactly one verify POST.
``claim_mfa_attempt_consumed`` records the outcome atomically.

Single-lookup-path invariant: no other code may issue
``select(MfaAttempt)`` outside this module.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import select, update

from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import MfaAttempt

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

MFA_ATTEMPT_TTL = timedelta(minutes=5)
"""Per design — a single MFA prompt is good for 5 minutes."""


async def mint_mfa_attempt(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    webauthn_challenge: bytes | None,
) -> tuple[str, MfaAttempt]:
    """Insert a fresh ``MfaAttempt`` and return ``(plain_token, row)``.

    Args:
        db: Async session — the caller commits.
        user_id: User the attempt is bound to (password+MFA path).
            ``None`` for passwordless passkey flow — the credential
            identifies the user on verify.
        webauthn_challenge: 32 random bytes pinned to the assertion
            (``clientDataJSON.challenge``). ``None`` for TOTP-only.

    Returns:
        ``(plain_token, row)`` — the plaintext token goes in a cookie
        scoped to ``/auth/mfa``; only the HMAC hash is persisted.
    """
    plain_token = secrets.token_urlsafe(32)
    row = MfaAttempt(
        user_id=user_id,
        token_hash=hmac_sha256(plain_token),
        webauthn_challenge=webauthn_challenge,
        expires_at=datetime.now(UTC) + MFA_ATTEMPT_TTL,
    )
    db.add(row)
    return plain_token, row


class MfaAttemptUnusableError(Exception):
    """Raised by ``assert_mfa_attempt_usable``."""

    def __init__(self, reason: str) -> None:
        """Initialize with a structured reason string."""
        super().__init__(reason)
        self.reason = reason


async def find_mfa_attempt_by_token_hash(
    db: AsyncSession, token_hash: str
) -> MfaAttempt | None:
    """The ONLY function that reads an MfaAttempt row."""
    return await db.scalar(  # type: ignore[no-any-return]
        select(MfaAttempt).where(MfaAttempt.token_hash == token_hash)
    )


def assert_mfa_attempt_usable(attempt: MfaAttempt | None) -> None:
    """Validate the row; raise the matching XUnusableError on bad state."""
    if attempt is None:
        raise MfaAttemptUnusableError("not_found")
    if attempt.consumed_at is not None:
        raise MfaAttemptUnusableError("consumed")
    if attempt.expires_at <= datetime.now(UTC):
        raise MfaAttemptUnusableError("expired")


async def claim_mfa_attempt_consumed(
    db: AsyncSession, attempt_id: UUID, *, outcome: str
) -> bool:
    """Atomic CAS — first POST wins.

    The outcome is recorded in the same UPDATE so we know post-hoc
    whether this attempt succeeded or failed.

    Args:
        db: Async session.
        attempt_id: The MfaAttempt row to consume.
        outcome: ``'success'``, ``'fail'``, or ``'expired'``.

    Returns:
        True if the consume happened atomically; False if a parallel
        POST already consumed the row.
    """
    result = await db.execute(
        update(MfaAttempt)
        .where(
            MfaAttempt.id == attempt_id,
            MfaAttempt.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(UTC), outcome=outcome)
        .returning(MfaAttempt.id)
    )
    return result.scalar_one_or_none() is not None
