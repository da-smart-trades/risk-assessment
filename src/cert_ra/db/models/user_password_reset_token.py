# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Single-use signed-link tokens for the password-reset flow.

Issued by `POST /auth/forgot-password` (self-service) or by the admin Total
Recovery action. Click → set new password → all sessions invalidated. The
reset handler never establishes a session; the user must complete a full
sign-in (password + MFA) afterward.
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


class UserPasswordResetToken(UUIDAuditBase):
    """Single-use password-reset token issued via email.

    Distinct from EmailToken's PASSWORD_RESET token_type because the OIDC SSO
    design needs a per-flow table with stricter semantics (canonical helper
    lookup, atomic CAS consume in the same transaction as the hashed_password
    update). Future iterations may consolidate.

    Cascade direction: deleting a User cascades to delete their reset tokens.
    """

    __tablename__ = "user_password_reset_token"
    __table_args__ = (
        Index("ix_user_password_reset_token_token_hash", "token_hash"),
        {"comment": "Single-use password-reset tokens"},
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
        comment="HMAC-SHA-256 of the reset token",
    )

    expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
        comment="now() + 1 hour at insert",
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Set atomically with the User.hashed_password update",
    )

    # ORM Relationships
    user: Mapped[User] = relationship(
        foreign_keys=[user_id],
        lazy="noload",
    )
