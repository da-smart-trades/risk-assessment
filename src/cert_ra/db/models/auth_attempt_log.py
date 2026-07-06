# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Per-IP authentication-attempt log for the secondary rate limit.

Every attempt against `/auth/login`, `/auth/mfa`, `/auth/link-confirm`,
`/auth/*/callback`, and `/auth/unlock/<token>` writes a row here. The
per-IP secondary limit (30 attempts / 5 min per source IP) counts rows
in the recent window.

Increments fire for unknown emails too, so attackers can't enumerate
faster than the per-IP rate (security checklist #76).
"""

from __future__ import annotations

from datetime import UTC, datetime

from advanced_alchemy.base import UUIDBase
from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column


class AuthAttemptLog(UUIDBase):
    """Per-IP, per-timestamp authentication attempt log.

    No FK to user_account: unknown-email attempts have no user yet (and
    revealing whether they did would break anti-enumeration). Identifying
    a specific attempt forensically requires correlating with application
    logs by `attempted_at`.

    Uses UUIDBase (not UUIDAuditBase) — no created_at/updated_at audit
    columns since the row IS a timestamp record. `attempted_at` is the
    canonical timestamp.
    """

    __tablename__ = "auth_attempt_log"
    __table_args__ = (
        Index("ix_auth_attempt_log_ip_attempted_at", "ip", "attempted_at"),
        {"comment": "Per-IP authentication-attempt log for rate limiting"},
    )

    ip: Mapped[str] = mapped_column(
        String(length=45),
        nullable=False,
        comment="IPv4 or IPv6 source address",
    )

    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        comment="When the attempt arrived",
    )

    path: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        comment="Request path (e.g., '/auth/login') for forensic correlation",
    )
