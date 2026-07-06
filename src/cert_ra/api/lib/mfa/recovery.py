# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Recovery code generation + insertion helpers.

Consumption goes through the canonical helper in
``cert_ra.api.lib.recovery_codes`` (``claim_recovery_code_used``).
This module only handles the write-side of enrollment / regenerate:
generate ``RECOVERY_CODE_COUNT`` plaintext codes, argon2id-hash each,
insert one row per code keyed to the user.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import delete

from cert_ra.api.lib.crypt import backup_code_hasher
from cert_ra.db.models import UserRecoveryCode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

RECOVERY_CODE_COUNT = 10
"""Per design — ten codes. Aligns with the iterate-to-verify bound in
``claim_recovery_code_used``."""

_CODE_GROUPS = 2
_GROUP_BYTES = 4


def _generate_plain_code() -> str:
    """Generate a single recovery code, formatted as ``XXXX-XXXX``.

    Two groups of 4 uppercase hex characters (16 bits each — 32 bits
    total). Argon2id verification covers the brute-force gap.
    """
    parts = [secrets.token_hex(_GROUP_BYTES).upper() for _ in range(_CODE_GROUPS)]
    return "-".join(parts)


async def _hash_code(code: str) -> str:
    """Argon2id-hash ``code`` on the executor (CPU-bound)."""
    return await asyncio.get_running_loop().run_in_executor(
        None, backup_code_hasher.hash, code
    )


async def issue_recovery_codes(db: AsyncSession, user_id: UUID) -> list[str]:
    """Issue a fresh set of recovery codes for ``user_id``.

    Deletes any existing ``UserRecoveryCode`` rows for the user (used
    or unused) and inserts a fresh batch. The caller is responsible for
    committing — typically inside the same transaction that flips
    ``is_two_factor_enabled`` or completes an MFA regenerate request.

    Returns:
        The plaintext codes — shown to the user exactly once. After
        commit, only the argon2 hashes remain in the DB.
    """
    await db.execute(
        delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id)
    )
    plain = [_generate_plain_code() for _ in range(RECOVERY_CODE_COUNT)]
    hashes = await asyncio.gather(*(_hash_code(c) for c in plain))
    for code_hash in hashes:
        db.add(UserRecoveryCode(user_id=user_id, code_hash=code_hash))
    return plain
