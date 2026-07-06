# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""WebAuthn / FIDO2 passkey credentials for MFA and passwordless sign-in."""

from __future__ import annotations

from datetime import (
    datetime,  # noqa: TC003 - used at runtime for SQLAlchemy column type
)
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime for SQLAlchemy column type

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .user import User


class UserPasskey(UUIDAuditBase):
    """A registered WebAuthn credential (passkey).

    A user may have many passkeys. The `credential_id` is globally unique
    and serves as the discoverable-credential lookup key for passwordless
    sign-in.

    Cascade direction: deleting a User cascades to delete their passkeys.
    """

    __tablename__ = "user_passkey"
    __table_args__ = {  # noqa: RUF012
        "comment": "WebAuthn / FIDO2 passkeys for MFA and passwordless sign-in"
    }
    __pii_columns__ = {"device_name"}  # noqa: RUF012

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    credential_id: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        unique=True,
        comment="WebAuthn credential ID — globally unique",
    )

    public_key: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        comment="Authenticator public key (CBOR-encoded)",
    )

    sign_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
        comment="Last observed signature counter (anti-clone defense)",
    )
    """Updated on every successful assertion when the authenticator increments.
    Platform authenticators (Touch ID, Face ID, Windows Hello) commonly return
    0 always; the verifier accepts both-zero without enforcing monotonicity.
    See design.md security checklist #20."""

    aaguid: Mapped[str | None] = mapped_column(
        String(length=64),
        nullable=True,
        default=None,
        comment="Authenticator AAGUID (model identifier)",
    )

    transports: Mapped[str | None] = mapped_column(
        String(length=128),
        nullable=True,
        default=None,
        comment="Comma-separated authenticator transports (usb,nfc,ble,internal)",
    )

    device_name: Mapped[str] = mapped_column(
        String(length=128),
        nullable=False,
        comment="User-supplied label (e.g., 'MacBook Touch ID')",
    )

    last_used_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
    )

    # ORM Relationships
    user: Mapped[User] = relationship(
        foreign_keys=[user_id],
        lazy="noload",
    )
