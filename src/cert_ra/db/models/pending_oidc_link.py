# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Server-side state for the password → OIDC discovery-mode link-confirm flow.

A row is inserted when a password user signs in via OIDC for the first time:
the resolver finds them by email, sees `hashed_password IS NOT NULL`, raises
`PendingLinkRequired`, and the controller stashes the validated OIDC identity
here keyed by a short-TTL cookie token. The user proves password ownership at
`/auth/link-confirm`; on success the row's `consumed_at` is set atomically
and a `UserOauthAccount` is created.

The cookie carries only the high-entropy token; all identity payload lives
in this row.
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


class PendingOidcLink(UUIDAuditBase):
    """Pending password→OIDC link awaiting password confirmation.

    Cascade direction: deleting the target user cascades to delete this row
    (parent → child). Deleting this row never affects the user.
    """

    __tablename__ = "pending_oidc_link"
    __table_args__ = (
        Index("ix_pending_oidc_link_token_hash", "token_hash"),
        {"comment": "Pending password→OIDC link awaiting password confirmation"},
    )
    __pii_columns__ = {"email", "name"}  # noqa: RUF012

    target_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )
    """The existing password user this OIDC identity will link to."""

    provider: Mapped[str] = mapped_column(String(length=32), nullable=False)
    """OIDC provider name ('google', 'microsoft', 'github')."""

    subject: Mapped[str] = mapped_column(String(length=255), nullable=False)
    """Provider-stable subject (the `sub` claim, or user ID for GitHub)."""

    email: Mapped[str] = mapped_column(String(length=320), nullable=False)
    """Validated email from the OIDC token. Re-verified on consume."""

    name: Mapped[str | None] = mapped_column(String(length=255), nullable=True)
    """Display name from the OIDC token (best-effort)."""

    token_hash: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        unique=True,
        comment="HMAC-SHA-256 of the cookie token",
    )
    """HMAC-SHA-256 of the cookie token. The plaintext lives only in the
    user's browser cookie. SHA-256 (not argon2id) because the token is
    high-entropy and we need deterministic equality lookup."""

    expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
        comment="now() + 10 minutes at insert",
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Set atomically on successful link-confirm",
    )
    """Set atomically by `claim_pending_link_consumed`. Once non-NULL, the
    row is dead and a captured cookie cannot be replayed."""

    failed_attempts: Mapped[int] = mapped_column(
        default=0,
        nullable=False,
        comment="Wrong-password attempts at /auth/link-confirm",
    )
    """Incremented atomically on each wrong-password attempt. At 3, the
    row is revoked (consumed_at set with outcome interpretation)."""

    # ORM Relationships
    target_user: Mapped[User] = relationship(
        foreign_keys=[target_user_id],
        lazy="noload",
    )
