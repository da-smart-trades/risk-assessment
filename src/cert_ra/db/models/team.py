# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

# ruff: noqa: N802
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from advanced_alchemy.base import UUIDAuditBase
from advanced_alchemy.mixins import SlugKey
from sqlalchemy import ColumnElement, String, and_, false, func, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.db.models.team_tag import team_tag

if TYPE_CHECKING:
    from .tag import Tag
    from .team_invitation import TeamInvitation
    from .team_member import TeamMember


class Team(UUIDAuditBase, SlugKey):
    """A group of users with common permissions.

    Users can create and invite users to a team.
    """

    __tablename__ = "team"
    __pii_columns__ = {"name", "description", "security_contact_email"}  # noqa: RUF012
    name: Mapped[str] = mapped_column(nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(
        String(length=500), nullable=True, default=None
    )
    domain: Mapped[str | None] = mapped_column(
        String(length=253), nullable=True, default=None
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_operator: Mapped[bool] = mapped_column(
        default=False, server_default=false(), nullable=False, index=True
    )

    # OIDC SSO design — per-team IDP enforcement (#5)
    enforced_provider: Mapped[str | None] = mapped_column(
        String(length=32),
        nullable=True,
        default=None,
        comment="'google' | 'microsoft' | 'github' — only this provider may "
        "authenticate this team's members. NULL = any configured provider.",
    )
    """If set, members can only sign in via this provider."""

    enforced_provider_set_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
        default=None,
        comment="When enforced_provider was most recently set/changed; used "
        "by the stuck-members admin view to identify members who haven't "
        "completed self-migration since the policy changed",
    )

    security_contact_email: Mapped[str | None] = mapped_column(
        String(length=320),
        nullable=True,
        default=None,
        comment="Customer's nominated security contact for operator-action "
        "and out-of-domain-provision fan-out alerts",
    )

    allowed_email_domains: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
        comment="Lowercase email domains team admins may provision without "
        "out-of-domain confirmation. Editable by team owners and "
        "operator_tenant_admin (not by team admins).",
    )
    """Domain allowlist for the soft-allowlist check on member provisioning."""

    # -----------
    # ORM Relationships
    # ------------
    members: Mapped[list[TeamMember]] = relationship(
        back_populates="team",
        cascade="all, delete",
        passive_deletes=True,
        lazy="selectin",
    )
    invitations: Mapped[list[TeamInvitation]] = relationship(
        back_populates="team", cascade="all, delete"
    )
    pending_invitations: Mapped[list[TeamInvitation]] = relationship(
        primaryjoin=lambda: _pending_invitations_join(),  # noqa: PLW0108
        foreign_keys=lambda: [_TeamInvitation().team_id],
        viewonly=True,
        lazy="noload",
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary=lambda: team_tag,
        back_populates="teams",
        cascade="all, delete",
        passive_deletes=True,
    )


def _TeamInvitation() -> type[TeamInvitation]:
    """Lazy import to avoid circular dependency.

    Returns:
        TeamInvitation class.
    """
    from .team_invitation import TeamInvitation

    return TeamInvitation


def _pending_invitations_join() -> ColumnElement[bool]:
    """Build the join condition for pending invitations.

    Filters for invitations that are:
    - Not accepted
    - Not expired (expires_at is NULL or > now())

    Returns:
        SQLAlchemy ColumnElement representing the join condition.
    """
    inv = _TeamInvitation()
    return and_(
        Team.id == inv.team_id,
        inv.is_accepted.is_(False),
        or_(inv.expires_at.is_(None), inv.expires_at > func.now()),
    )
