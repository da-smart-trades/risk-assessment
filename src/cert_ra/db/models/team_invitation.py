# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.db.models.team_roles import TeamRoles

if TYPE_CHECKING:
    from .team import Team
    from .user import User


class InvitationKind(StrEnum):
    """Kind of team invitation.

    FIRST_TIME_ACTIVATION: invitation activates a new, never-signed-in
    User. The TeamMember row was created alongside the User at
    provisioning time.

    CROSS_TEAM_JOIN: invitation asks an already-activated User on a
    different team to explicitly consent to joining this team. No
    TeamMember row exists until acceptance.
    """

    FIRST_TIME_ACTIVATION = "first_time_activation"
    CROSS_TEAM_JOIN = "cross_team_join"


class TeamInvitation(UUIDAuditBase):
    """Team Invitation with secure token-based acceptance.

    Invitations are sent via email with a hashed token. The plain token
    is only sent to the invitee once. Invitations can be accepted or
    rejected, and expire after a configurable period.
    """

    __tablename__ = "team_invitation"
    __table_args__ = (
        Index("ix_team_invitation_token", "token_hash"),
        Index("ix_team_invitation_email_team", "email", "team_id"),
    )

    team_id: Mapped[UUID] = mapped_column(ForeignKey("team.id", ondelete="cascade"))
    email: Mapped[str] = mapped_column(index=True)
    role: Mapped[TeamRoles] = mapped_column(String(length=50), default=TeamRoles.MEMBER)
    is_accepted: Mapped[bool] = mapped_column(default=False)
    invited_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="set null")
    )
    invited_by_email: Mapped[str]

    # Token-based invitation security
    token_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="HMAC-SHA-256 hash of invitation token",
    )
    """HMAC-SHA-256 of the invitation token. Plain token never stored."""

    expires_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        comment="When this invitation expires",
    )
    """Expiration timestamp for the invitation."""

    accepted_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="When the invitation was accepted",
    )
    """Timestamp when the invitation was accepted."""

    # OIDC SSO design — invitation kind + tracking for atomic activation
    kind: Mapped[InvitationKind | None] = mapped_column(
        String(length=32),
        nullable=True,
        default=None,
        comment="Invitation kind (FIRST_TIME_ACTIVATION | CROSS_TEAM_JOIN). "
        "NULL for legacy rows pre-dating the OIDC SSO design.",
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=True,
        default=None,
        comment="Pre-provisioned User row this invitation activates. NULL "
        "for legacy rows; new-flow invitations always set this.",
    )
    """For FIRST_TIME_ACTIVATION: the not-yet-activated User. For
    CROSS_TEAM_JOIN: the already-activated User being invited to a new team."""

    force_provider: Mapped[str | None] = mapped_column(
        String(length=32),
        nullable=True,
        default=None,
        comment="If set, the invite link redirects directly to this IdP's "
        "OIDC flow. Meaningful only for FIRST_TIME_ACTIVATION.",
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="When the invitation was revoked (admin reissue, decline, "
        "etc.). Distinct from accepted_at; both are exclusive terminal states.",
    )

    out_of_domain_override: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        comment="True iff the admin clicked through the out-of-domain "
        "confirmation modal at provisioning time (#2)",
    )

    # -----------
    # ORM Relationships
    # ------------
    team: Mapped[Team] = relationship(
        foreign_keys="TeamInvitation.team_id", lazy="joined"
    )
    invited_by: Mapped[User | None] = relationship(
        foreign_keys="TeamInvitation.invited_by_id", uselist=False
    )

    @property
    def is_expired(self) -> bool:
        """Check if the invitation has expired."""
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at.replace(tzinfo=UTC)

    @property
    def is_pending(self) -> bool:
        """Check if the invitation is still pending (not accepted and not expired)."""
        return not self.is_accepted and not self.is_expired
