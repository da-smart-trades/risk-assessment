# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Single-use signed-link tokens for the unlock-via-email recovery flow.

When `record_failure` triggers the first active lockout for a user (within
the throttle window), an unlock email is enqueued with a one-time link.
Clicking the link clears every `UserLockout` row for the user atomically.
"""

from __future__ import annotations

from datetime import (
    datetime,  # noqa: TC003 - used at runtime for SQLAlchemy column type
)
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime for SQLAlchemy column type

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .user import User


class UserUnlockToken(UUIDAuditBase):
    """Single-use unlock token issued via email.

    Cascade direction: deleting a User cascades to delete their unlock tokens.
    Deleting an unlock token never affects the user.
    """

    __tablename__ = "user_unlock_token"
    __table_args__ = (
        Index("ix_user_unlock_token_token_hash", "token_hash"),
        {"comment": "Single-use unlock tokens for the unlock-via-email flow"},
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        unique=True,
        comment="HMAC-SHA-256 of the unlock token",
    )

    expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
        comment="now() + 24 hours at insert",
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Set atomically on first click; subsequent clicks rejected",
    )

    # ORM Relationships
    user: Mapped[User] = relationship(
        foreign_keys=[user_id],
        lazy="noload",
    )
