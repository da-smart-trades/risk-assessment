# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime for SQLAlchemy column type

from advanced_alchemy.base import UUIDAuditBase
from advanced_alchemy.types import EncryptedString, FileObject
from advanced_alchemy.types.file_object.data_type import StoredObject
from sqlalchemy import ForeignKey, String, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.settings.api import get_app_settings
from cert_ra.settings.db import get_storage_settings

if TYPE_CHECKING:
    from .oauth_account import UserOauthAccount
    from .team_member import TeamMember
    from .user_role import UserRole


class User(UUIDAuditBase):
    __tablename__ = "user_account"
    __table_args__ = {"comment": "User accounts for application access"}  # noqa: RUF012
    __pii_columns__ = {"name", "email", "avatar", "totp_secret"}  # noqa: RUF012

    email: Mapped[str] = mapped_column(unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(nullable=True, default=None)
    hashed_password: Mapped[str | None] = mapped_column(
        String(length=255),
        nullable=True,
        default=None,
        deferred=True,
        deferred_group="security_sensitive",
    )
    avatar: Mapped[FileObject | None] = mapped_column(
        StoredObject(backend="avatars"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        default=False, server_default=false(), nullable=False
    )
    """Force a password rotation on next login. Set for the break-glass
    root account at bootstrap so its first sign-in rotates the seeded
    password (PR-8 break-glass root)."""
    is_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    verified_at: Mapped[date] = mapped_column(nullable=True, default=None)
    joined_at: Mapped[date] = mapped_column(default=lambda: datetime.now(UTC).date())

    # OIDC SSO design — admin-driven provisioning + lockout tracking
    invited_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="set null"),
        nullable=True,
        default=None,
        comment="Admin who provisioned this user; NULLed if that admin is deleted",
    )

    invited_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="When the user was provisioned by an admin",
    )

    activated_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="When the user first completed sign-in (password set OR OIDC linked). "
        "Once non-NULL, the activation invitation is dead.",
    )
    """Set atomically by claim_user_activation. Equivalent to
    'has hashed_password OR has any UserOauthAccount row'; cheap to query."""

    has_active_lockout: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="True iff at least one UserLockout row for this user has "
        "locked_until > now(). Drives the unlock-email throttle.",
    )

    last_unlock_email_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="Most recent unlock-email enqueue timestamp; gates the "
        "throttle in enqueue_unlock_email_if_due",
    )

    # Multi-Factor Authentication (MFA/TOTP)
    totp_secret: Mapped[str | None] = mapped_column(
        EncryptedString(key=get_app_settings().secret_key),
        nullable=True,
        default=None,
        deferred=True,
        deferred_group="security_sensitive",
        comment="Encrypted TOTP secret key for MFA",
    )
    """Encrypted TOTP secret for generating time-based one-time passwords."""

    is_two_factor_enabled: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="Whether MFA is enabled for this user",
    )
    """Whether multi-factor authentication is currently active."""

    two_factor_confirmed_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="When MFA was confirmed/enabled",
    )
    """Timestamp when MFA was successfully configured."""

    backup_codes: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        deferred=True,
        deferred_group="security_sensitive",
        comment="Hashed backup codes for MFA recovery",
    )
    """JSON array of hashed backup codes for account recovery."""

    # -----------
    # ORM Relationships
    # ------------

    roles: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        lazy="selectin",
        uselist=True,
        cascade="all, delete",
    )
    teams: Mapped[list[TeamMember]] = relationship(
        back_populates="user",
        lazy="selectin",
        uselist=True,
        cascade="all, delete",
        viewonly=True,
    )
    oauth_accounts: Mapped[list[UserOauthAccount]] = relationship(
        back_populates="user",
        lazy="noload",
        cascade="all, delete",
        uselist=True,
    )

    @hybrid_property
    def has_password(self) -> bool:
        """If user has pswd."""
        return self.hashed_password is not None

    @hybrid_property
    def has_mfa(self) -> bool:
        """Check if user has MFA enabled.

        Note: This only checks is_two_factor_enabled to avoid loading deferred totp_secret.
        For full verification (including secret presence), use is_two_factor_enabled
        after loading credentials with undefer_group("security_sensitive").

        Returns:
            True if MFA is enabled, False otherwise.
        """
        return self.is_two_factor_enabled

    @hybrid_property
    def is_operator_member(self) -> bool:
        """True if user is a member of the operator team (or superuser).

        Used by the Inertia ``auth.user`` shared prop to gate the top-level
        Manual Metrics sidebar entry — non-operator users see manual metrics
        only inside chain/token detail pages.

        Returns:
            True if the user belongs to any operator team.
        """
        if self.is_superuser:
            return True
        return any(m.team.is_operator for m in self.teams)

    @hybrid_property
    def is_operator_editor(self) -> bool:
        """True if user has operator-team write access (owner/ADMIN/EDITOR or superuser).

        Used by the Inertia ``auth.user`` shared prop to gate the operator
        admin sub-section in the sidebar. Mirrors the
        ``requires_operator_editor`` guard in
        ``cert_ra.api.domain.teams.guards``.

        Returns:
            True if the user can write operator-only content.
        """
        if self.is_superuser:
            return True
        return any(
            m.team.is_operator and (m.is_owner or m.role in ("admin", "editor"))
            for m in self.teams
        )

    @hybrid_property
    def is_any_team_editor(self) -> bool:
        """True if the user can edit any team's content (any scope).

        Used by the Inertia ``auth.user`` shared prop to gate the manual-metrics
        admin sidebar entry — visible to any user who can edit at least one
        team's content (owner/ADMIN/EDITOR), independent of operator membership.

        Returns:
            True if the user is a superuser or has owner/ADMIN/EDITOR on any team.
        """
        if self.is_superuser:
            return True
        return any(m.is_owner or m.role in ("admin", "editor") for m in self.teams)

    @hybrid_property
    def avatar_url(self) -> str:
        """Get avatar URL - uploaded file or Gravatar fallback.

        For local storage, returns static file path.
        For cloud storage (S3, GCS, Azure), returns a signed URL.

        Returns:
            URL string for avatar image.
        """
        if self.avatar is not None:
            if get_storage_settings().is_cloud_storage:
                return self.avatar.sign(
                    expires_in=get_storage_settings().signed_url_expiry
                )
            return f"/uploads/{self.avatar.filename}"
        return self._get_gravatar_url()

    def _get_gravatar_url(self, size: int = 250) -> str:
        """Generate Gravatar URL from email.

        Args:
            size: Image size in pixels.

        Returns:
            Gravatar URL string.
        """
        email_hash = hashlib.md5(self.email.lower().strip().encode()).hexdigest()  # noqa: S324
        return f"https://www.gravatar.com/avatar/{email_hash}?s={size}&d=identicon"
