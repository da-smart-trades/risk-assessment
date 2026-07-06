# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Canonical helpers for the per-(user, ip) lockout state machine.

Three tables come together here:

- ``UserLockout``: one row per (user_id, ip) pair, counts failures
  and pins ``locked_until`` when the counter crosses the threshold.
- ``AuthAttemptLog``: one row per attempt against any ``/auth/*``
  endpoint, keyed by IP, used for the per-IP secondary limit.
- ``UserUnlockToken``: the email-recovery link; the unlock-email
  throttle is gated by ``User.last_unlock_email_at`` (atomic CAS).

Single-lookup-path invariant: no other code may issue
``select(UserLockout)`` or ``update(UserLockout)``. Every state
transition routes through this module.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cert_ra.api.lib.crypt import hasher as password_hasher
from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import (
    AuthAttemptLog,
    User,
    UserLockout,
    UserPasskey,
    UserUnlockToken,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class OperatorPostureError(Exception):
    """An operator signed in without satisfying hardware-passkey MFA.

    Operators (members of ``Team.is_operator``) must have at least one
    enrolled passkey; TOTP is not accepted as their second factor
    (PR-8, Operator team hardening — Control 1). The controller routes
    this to ``/auth/operator-setup-required``.
    """

    DEFAULT_REASON = "Operators must enroll a passkey before signing in."

    def __init__(self, reason: str | None = None) -> None:
        """Capture the structured reason (defaults to the passkey message)."""
        resolved = reason or self.DEFAULT_REASON
        super().__init__(resolved)
        self.reason = resolved


async def assert_operator_mfa_posture(db: AsyncSession, user: User) -> None:
    """Require an enrolled passkey for operators; no-op for everyone else.

    Fires after credential verification (password or OIDC) and before
    the session is created. Raises ``OperatorPostureError`` when an
    operator has no passkey.
    """
    from cert_ra.api.lib.operator_roles import user_is_operator

    if not await user_is_operator(db, user):
        return
    has_passkey = await db.scalar(
        select(UserPasskey.id).where(UserPasskey.user_id == user.id).limit(1)
    )
    if has_passkey is None:
        raise OperatorPostureError


# --- thresholds --------------------------------------------------------

FAILURE_THRESHOLD = 3
"""Failures within ``LOCKOUT_WINDOW`` that trigger a lockout."""

LOCKOUT_WINDOW = timedelta(minutes=10)
"""How far back ``failed_count`` counts. Failures older than this reset
the counter on the next failure."""

LOCKOUT_DURATION = timedelta(minutes=10)
"""How long ``locked_until`` extends past the triggering failure."""

PER_IP_WINDOW = timedelta(minutes=5)
"""Sliding window for the per-IP secondary limit."""

PER_IP_THRESHOLD = 30
"""Per-IP attempts allowed in ``PER_IP_WINDOW`` before refusal."""

UNLOCK_TOKEN_TTL = timedelta(hours=24)
"""How long an emailed unlock link stays valid."""

UNLOCK_EMAIL_THROTTLE = timedelta(minutes=30)
"""Minimum gap between unlock emails for the same user (design Fix 2)."""

PASSWORD_CHECK_CANARY_HASH = (
    # Argon2id hash of a fixed throwaway string. The original is
    # never needed — we verify a constant decoy against this hash to
    # spend equivalent CPU on the unknown-email branch.
    "$argon2id$v=19$m=65536,t=3,p=4$"  # noqa: S105
    "WCTeeo9s9/SH7/3DsUPhBw$"
    "STmd+zG6pYq6zPND7IIr5iIQvbqnhHOG/yVANlyPXX8"
)
"""Argon2id hash to verify on the unknown-email branch. The CPU time
to verify matches a real password check, defeating timing oracles on
``/auth/login`` (design #74)."""


# --- per-IP rate limit -------------------------------------------------


async def record_auth_attempt(db: AsyncSession, *, ip: str, path: str) -> None:
    """Insert one ``AuthAttemptLog`` row, no questions asked.

    Call BEFORE any unknown-vs-known branching so the per-IP counter
    sees unknown-email attempts too. The caller commits.

    Args:
        db: Async session.
        ip: Client IP (IPv4 or IPv6). Empty / ``"unknown"`` is allowed
            and falls into a single bucket — the rate limit then
            applies to all unidentified callers as a group.
        path: Request path, recorded for forensic correlation.
    """
    db.add(AuthAttemptLog(ip=ip or "unknown", path=path))


async def assert_per_ip_under_limit(db: AsyncSession, *, ip: str) -> bool:
    """Check the per-IP counter against ``PER_IP_THRESHOLD``.

    Counts rows in ``AuthAttemptLog`` whose ``ip`` matches and whose
    ``attempted_at`` is within the window. ``False`` means the caller
    must refuse the attempt without revealing the cause.
    """
    cutoff = datetime.now(UTC) - PER_IP_WINDOW
    result = await db.execute(
        select(func.count())
        .select_from(AuthAttemptLog)
        .where(
            AuthAttemptLog.ip == (ip or "unknown"),
            AuthAttemptLog.attempted_at >= cutoff,
        )
    )
    count = int(result.scalar_one() or 0)
    return count <= PER_IP_THRESHOLD


# --- per-(user, ip) lockout -------------------------------------------


async def assert_not_locked(
    db: AsyncSession, *, user_id: UUID, ip: str
) -> datetime | None:
    """Check the (user, ip) lockout state.

    Returns the ``locked_until`` timestamp if the pair is currently
    locked, otherwise ``None``. The caller renders the
    ``/auth/locked`` page when non-None.
    """
    now = datetime.now(UTC)
    row = await db.scalar(
        select(UserLockout).where(
            UserLockout.user_id == user_id,
            UserLockout.ip == (ip or "unknown"),
        )
    )
    if row is None or row.locked_until is None:
        return None
    if row.locked_until <= now:
        return None
    return row.locked_until


async def record_failure(
    db: AsyncSession, *, user_id: UUID, ip: str
) -> tuple[bool, bool]:
    """Increment the (user, ip) failure counter and lock if appropriate.

    Uses an idempotent UPSERT keyed on the
    ``uq_user_lockout_user_ip`` constraint. The same caller doing two
    failures in parallel each see ``failed_count`` advance once per
    call (Postgres serializes the conflict resolution).

    Returns:
        ``(locked_now, was_already_locked)`` — if ``locked_now`` is
        True, this call crossed the threshold and the row's
        ``locked_until`` is now pinned. ``was_already_locked`` is True
        if the row was already locked before this call (and is still
        locked) — useful for skipping duplicate side effects.
    """
    now = datetime.now(UTC)
    locked_until = now + LOCKOUT_DURATION

    # Pre-flight read: are we already locked? If so the user-facing
    # behavior is identical; we don't bother incrementing.
    existing = await db.scalar(
        select(UserLockout).where(
            UserLockout.user_id == user_id, UserLockout.ip == (ip or "unknown")
        )
    )
    was_already_locked = bool(
        existing and existing.locked_until is not None and existing.locked_until > now
    )
    if was_already_locked:
        return False, True

    # Window reset: if the existing row's window has expired, restart
    # the counter at 1.
    window_start = now - LOCKOUT_WINDOW
    if existing is not None and existing.first_failed_at < window_start:
        await db.execute(
            update(UserLockout)
            .where(UserLockout.id == existing.id)
            .values(failed_count=1, first_failed_at=now, locked_until=None)
        )
        return False, False

    # Idempotent UPSERT — first attempt creates the row; subsequent
    # attempts increment failed_count.
    stmt = pg_insert(UserLockout).values(
        user_id=user_id,
        ip=ip or "unknown",
        failed_count=1,
        first_failed_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_user_lockout_user_ip",
        set_={"failed_count": UserLockout.failed_count + 1},
    )
    await db.execute(stmt)

    # Read the post-UPSERT count, pin locked_until if over threshold.
    row = await db.scalar(
        select(UserLockout).where(
            UserLockout.user_id == user_id, UserLockout.ip == (ip or "unknown")
        )
    )
    if row is None:
        return False, False
    if row.failed_count >= FAILURE_THRESHOLD and row.locked_until is None:
        await db.execute(
            update(UserLockout)
            .where(UserLockout.id == row.id, UserLockout.locked_until.is_(None))
            .values(locked_until=locked_until)
        )
        await db.execute(
            update(User).where(User.id == user_id).values(has_active_lockout=True)
        )
        return True, False
    return False, False


async def record_success(db: AsyncSession, *, user_id: UUID, ip: str) -> None:
    """Clear the failure counter for this (user, ip) on success.

    Does NOT clear other-IP lockouts — design #67. The user's
    successful sign-in from IP B does not unlock IP A.
    """
    await db.execute(
        delete(UserLockout).where(
            UserLockout.user_id == user_id, UserLockout.ip == (ip or "unknown")
        )
    )
    # If no UserLockout rows remain, flip has_active_lockout off.
    remaining = await db.scalar(
        select(func.count())
        .select_from(UserLockout)
        .where(
            UserLockout.user_id == user_id,
            UserLockout.locked_until > datetime.now(UTC),
        )
    )
    if int(remaining or 0) == 0:
        await db.execute(
            update(User).where(User.id == user_id).values(has_active_lockout=False)
        )


# --- timing canary -----------------------------------------------------


def _burn_password_check_time_sync() -> None:
    """Verify the canary hash against a constant string.

    The argon2 verify is CPU-bound and the result is discarded — we
    only need the timing parity. Runs synchronously on the executor
    because that's where ``password_hasher.verify`` runs.
    """
    try:
        password_hasher.verify("decoy", PASSWORD_CHECK_CANARY_HASH)
    except Exception:  # noqa: BLE001 — outcome irrelevant; we want the cost
        return


async def burn_password_check_time() -> None:
    """Spend roughly one ``password_hasher.verify`` worth of CPU.

    Called from the unknown-email branch of ``/auth/login`` so the
    response time is statistically indistinguishable from the
    known-but-wrong-password branch (design #74).
    """
    await asyncio.get_running_loop().run_in_executor(
        None, _burn_password_check_time_sync
    )


# --- unlock email throttle --------------------------------------------


async def enqueue_unlock_email_if_due(
    db: AsyncSession, *, user_id: UUID
) -> tuple[str | None, UserUnlockToken | None]:
    """Atomic CAS — mint an unlock token iff the throttle window allows.

    The CAS condition is ``last_unlock_email_at IS NULL OR
    last_unlock_email_at <= now() - UNLOCK_EMAIL_THROTTLE``. Only the
    first concurrent caller wins. Subsequent calls within the window
    return ``(None, None)`` and the controller skips the email
    emission.

    Returns:
        ``(plain_token, row)`` on the winning call; ``(None, None)``
        when throttled. The plain token is the email link value; the
        row stores the HMAC hash.
    """
    now = datetime.now(UTC)
    throttle_cutoff = now - UNLOCK_EMAIL_THROTTLE
    claim = await db.execute(
        update(User)
        .where(
            User.id == user_id,
            (
                User.last_unlock_email_at.is_(None)
                | (User.last_unlock_email_at <= throttle_cutoff)
            ),
        )
        .values(last_unlock_email_at=now)
        .returning(User.id)
    )
    if claim.scalar_one_or_none() is None:
        return None, None

    plain_token = secrets.token_urlsafe(32)
    row = UserUnlockToken(
        user_id=user_id,
        token_hash=hmac_sha256(plain_token),
        expires_at=now + UNLOCK_TOKEN_TTL,
    )
    db.add(row)
    return plain_token, row


# --- admin force-unlock -----------------------------------------------


async def force_unlock_user(db: AsyncSession, *, user_id: UUID) -> int:
    """Clear EVERY lockout row for ``user_id`` and reset throttle.

    Returns the number of UserLockout rows deleted (for audit).
    Subsequent emails (forced unlock confirmation) are emitted by the
    caller via the signal layer.
    """
    deleted_result = await db.execute(
        delete(UserLockout)
        .where(UserLockout.user_id == user_id)
        .returning(UserLockout.id)
    )
    deleted = len(list(deleted_result.scalars().all()))
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(has_active_lockout=False, last_unlock_email_at=None)
    )
    return deleted
