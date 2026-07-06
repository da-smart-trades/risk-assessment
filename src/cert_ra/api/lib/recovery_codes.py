# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helper for the UserRecoveryCode state machine.

Recovery codes are user-scoped (not token-keyed) and use argon2id
hashing because the input is low-entropy (~10 chars). The helper
combines argon2 verify against each unused candidate with an atomic
CAS update on the matched row.

Single-lookup-path invariant: no other code may issue
``select(UserRecoveryCode)`` or ``update(UserRecoveryCode)`` during
the sign-in flow.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from pwdlib.exceptions import PwdlibError
from sqlalchemy import select, update

from cert_ra.api.lib.crypt import backup_code_hasher
from cert_ra.db.models import UserRecoveryCode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _verify_recovery_code_sync(
    submitted_code: str, candidates: list[UserRecoveryCode]
) -> UUID | None:
    """CPU-bound argon2 verify loop. Returns the matched row id or None.

    ``pwdlib.verify`` returns a bool (no mismatch exception); we only
    catch ``PwdlibError`` to cover malformed or unparseable stored
    hashes (treat as no-match rather than crash the sign-in path).
    """
    for candidate in candidates:
        try:
            if backup_code_hasher.verify(submitted_code, candidate.code_hash):
                return candidate.id
        except PwdlibError:
            continue
    return None


async def claim_recovery_code_used(
    db: AsyncSession, user_id: UUID, submitted_code: str
) -> bool:
    """Hash-verify the submitted code and, on a match, claim it.

    Walks the user's unused recovery codes, runs argon2 verify against
    each, and atomically transitions the matched row to used.

    Returns ``True`` iff a usable code was matched AND claimed. Returns
    ``False`` if:

      - no stored hash matches the submitted code, OR
      - the matching row was concurrently consumed by another POST
        between the verify and the CAS update.

    The caller MUST NOT separately update ``used_at``, MUST NOT select
    rows by hash without going through this function, and MUST
    short-circuit on False (no session is established for a False
    return).

    Args:
        db: Async session.
        user_id: The user whose recovery codes are being checked.
            Lookup is scoped to this user — codes belonging to other
            users are never in the candidate set.
        submitted_code: Plain-text recovery code from the MFA prompt.

    Returns:
        True on successful atomic claim; False on no-match or race-loss.
    """
    candidates = list(
        (
            await db.scalars(
                select(UserRecoveryCode).where(
                    UserRecoveryCode.user_id == user_id,
                    UserRecoveryCode.used_at.is_(None),
                )
            )
        ).all()
    )
    # argon2 verify is CPU-bound; offload to the executor like crypt.py does.
    matched_id = await asyncio.get_running_loop().run_in_executor(
        None, _verify_recovery_code_sync, submitted_code, candidates
    )
    if matched_id is None:
        return False
    # Atomic claim: only the request whose UPDATE matches the
    # `used_at IS NULL` predicate wins.
    result = await db.execute(
        update(UserRecoveryCode)
        .where(
            UserRecoveryCode.id == matched_id,
            UserRecoveryCode.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
        .returning(UserRecoveryCode.id)
    )
    return result.scalar_one_or_none() is not None
