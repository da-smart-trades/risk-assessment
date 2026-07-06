# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Server-side state for the enforcement self-migration flow.

A row is inserted when an OIDC sign-in via provider X resolves to a user
whose team enforces provider Y. The handler stashes the validated X identity
here keyed by a short-TTL cookie token, then redirects the user to authenticate
at provider Y. On the Y callback, the row is consumed atomically and the user's
UserOauthAccount is swapped from X to Y.

The cookie carries only the high-entropy token; identity payload lives in
this row.
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


class PendingProviderSwitch(UUIDAuditBase):
    """Pending OIDC-provider switch driven by team enforcement.

    Cascade direction: deleting the target user cascades to delete this row
    (parent → child). Deleting this row never affects the user.
    """

    __tablename__ = "pending_provider_switch"
    __table_args__ = (
        Index("ix_pending_provider_switch_token_hash", "token_hash"),
        {"comment": "Pending OIDC-provider switch awaiting target-provider callback"},
    )
    __pii_columns__ = {"source_email"}  # noqa: RUF012

    target_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )
    """The user whose UserOauthAccount will be swapped on consume."""

    source_provider: Mapped[str] = mapped_column(String(length=32), nullable=False)
    """The provider the user currently signed in via (the wrong one)."""

    source_subject: Mapped[str] = mapped_column(String(length=255), nullable=False)
    """Provider-stable subject from the source OIDC handshake."""

    source_email: Mapped[str] = mapped_column(String(length=320), nullable=False)
    """Email from the source OIDC handshake. Must match the target on consume."""

    target_provider: Mapped[str] = mapped_column(String(length=32), nullable=False)
    """The provider the team enforces; the user must authenticate here next."""

    token_hash: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        unique=True,
        comment="HMAC-SHA-256 of the cookie token",
    )

    expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
        comment="now() + 10 minutes at insert",
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Set atomically on successful target-provider verification",
    )

    # ORM Relationships
    target_user: Mapped[User] = relationship(
        foreign_keys=[target_user_id],
        lazy="noload",
    )
