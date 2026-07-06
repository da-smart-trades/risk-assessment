# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""MFA recovery codes — one-time codes for emergency sign-in.

Generated at MFA enrollment (10 codes per user); shown once; hashed with
argon2id (low-entropy codes need slow hashing + iterate-to-verify). Consumed
via `claim_recovery_code_used` which combines argon2 verify against each
unused candidate with an atomic CAS update on the matched row.

NOTE: the existing `User.backup_codes` JSONB column is reserved for the
legacy MFA flow. This per-row table is the new mechanism for the OIDC SSO
design.
"""

from __future__ import annotations

from datetime import (
    datetime,  # noqa: TC003 - used at runtime for SQLAlchemy column type
)
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime for SQLAlchemy column type

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .user import User


class UserRecoveryCode(UUIDAuditBase):
    """One-time MFA recovery code, argon2id-hashed.

    Cascade direction: deleting a User cascades to delete their recovery codes.
    """

    __tablename__ = "user_recovery_code"
    __table_args__ = {  # noqa: RUF012
        "comment": "MFA recovery codes (argon2id-hashed, one-time use)"
    }

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    code_hash: Mapped[str] = mapped_column(
        String(length=255),
        nullable=False,
        deferred=True,
        deferred_group="security_sensitive",
        comment="argon2id hash of the recovery code",
    )
    """Argon2id (not SHA-256) because recovery codes are low-entropy
    (~10 characters) and need slow hashing to resist brute force.
    Verified via iterate-then-CAS pattern bounded at N=10."""

    used_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Set atomically by claim_recovery_code_used",
    )

    # ORM Relationships
    user: Mapped[User] = relationship(
        foreign_keys=[user_id],
        lazy="noload",
    )
