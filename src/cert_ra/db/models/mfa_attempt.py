# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Server-side per-MFA-attempt state — single-use, atomically consumed.

A row is inserted on successful password verification (password+MFA path) or
on `/auth/passkey/challenge` (passwordless path). Carries the WebAuthn
challenge bound to this attempt; the cookie carries only the lookup token.

Single-attempt invariant: each row supports exactly one verify POST.
`claim_mfa_attempt_consumed` runs once and records the outcome.
"""

from __future__ import annotations

from datetime import (
    datetime,  # noqa: TC003 - used at runtime for SQLAlchemy column type
)
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime for SQLAlchemy column type

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import ForeignKey, Index, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .user import User


class MfaAttempt(UUIDAuditBase):
    """A single MFA verification attempt.

    `user_id` is nullable: for the passwordless passkey flow the row is created
    before the user is identified (the assertion's credential_id resolves to a
    UserPasskey on verify). For the password+MFA flow, user_id is set from the
    user who passed the password step.

    Cascade direction: deleting a User cascades to their MfaAttempt rows.
    Deleting an MfaAttempt never affects the user.
    """

    __tablename__ = "mfa_attempt"
    __table_args__ = (
        Index("ix_mfa_attempt_token_hash", "token_hash"),
        {"comment": "Server-side per-attempt state for MFA verification"},
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=True,
        index=True,
    )
    """The user who passed the password step (password+MFA path), or NULL
    for the passwordless path (the credential identifies the user on verify)."""

    token_hash: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        unique=True,
        comment="HMAC-SHA-256 of the cookie token",
    )

    webauthn_challenge: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        default=None,
        comment="32 random bytes; NULL for TOTP/recovery-only attempts",
    )
    """Generated when the prompt page is rendered and pinned to the
    assertion via `clientDataJSON.challenge`. NULL if the user is choosing
    TOTP/recovery only on a TOTP-only enrolled account."""

    expires_at: Mapped[datetime] = mapped_column(
        nullable=False,
        comment="now() + 5 minutes at insert",
    )

    consumed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Set atomically on the first verify POST (success or fail)",
    )
    """After this, the row is dead — a captured assertion cannot be replayed."""

    outcome: Mapped[str | None] = mapped_column(
        String(length=16),
        nullable=True,
        default=None,
        comment="'success' | 'fail' | 'expired'",
    )
    """Set alongside consumed_at for audit/diagnostic purposes."""

    # ORM Relationships
    user: Mapped[User | None] = relationship(
        foreign_keys=[user_id],
        lazy="noload",
    )
