# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Per-(user, ip) sign-in lockout state.

Replaces the previously-proposed user_account-level lockout columns. The
same user can have multiple concurrent lockout rows (one per attacker IP)
without conflicting with legitimate IPs.
"""

from __future__ import annotations

from datetime import (
    datetime,  # noqa: TC003 - used at runtime for SQLAlchemy column type
)
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime for SQLAlchemy column type

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .user import User


class UserLockout(UUIDAuditBase):
    """Per-(user, ip) failure counters and lockout timestamp.

    A row exists for every (user, ip) pair that has accumulated at least one
    failed sign-in within the lockout window. When `failed_count` crosses the
    threshold, `locked_until` is set atomically and sign-in from THIS ip is
    refused; other IPs remain unaffected.

    Cascade direction: deleting a User cascades to delete their UserLockout
    rows. Deleting a UserLockout never affects the user.
    """

    __tablename__ = "user_lockout"
    __table_args__ = (
        UniqueConstraint("user_id", "ip", name="uq_user_lockout_user_ip"),
        {"comment": "Per-(user, ip) failure counters and lockout state"},
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    ip: Mapped[str] = mapped_column(
        String(length=45),
        nullable=False,
        comment="IPv4 or IPv6 address of the failed attempt source",
    )

    failed_count: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="Failures in the current window",
    )

    first_failed_at: Mapped[datetime] = mapped_column(
        nullable=False,
        comment="Timestamp of the first failure in the current window",
    )
    """Used to compute window expiry. If now() - first_failed_at > window,
    the counter resets atomically on the next failure."""

    locked_until: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Lockout expiry; NULL means not currently locked",
    )

    # ORM Relationships
    user: Mapped[User] = relationship(
        foreign_keys=[user_id],
        lazy="noload",
    )
