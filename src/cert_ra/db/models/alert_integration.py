# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Boolean, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import AlertIntegrationKind

if TYPE_CHECKING:
    from .team import Team
    from .user import User


class AlertIntegration(UUIDAuditBase):
    """A delivery channel attached to a team — one row per integration.

    The ``config`` JSONB column is polymorphic — its shape is determined by
    ``kind`` and validated by the matching msgspec model in
    ``cert_ra.api.domain.alerts.integrations``. Sensitive fields
    (e.g. webhook secrets) are encrypted-at-rest at the service layer using
    ``cert_ra.api.lib.crypt``.

    A team has at most one ``is_primary=True`` integration per ``kind``, enforced
    by a partial unique index. Additional integrations beyond the primary are
    attached to specific alerts via the ``alert_integration_link`` join table.
    """

    __tablename__ = "alert_integration"
    __table_args__ = (
        Index(
            "uq_alert_integration_primary",
            "team_id",
            "kind",
            unique=True,
            postgresql_where=text("is_primary = true"),
        ),
        Index("ix_alert_integration_team", "team_id"),
    )

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[AlertIntegrationKind] = mapped_column(
        ENUM(AlertIntegrationKind, name="alertintegrationkind"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(length=255), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # -----------
    # ORM Relationships
    # ------------
    team: Mapped[Team] = relationship(
        foreign_keys=[team_id],
        lazy="joined",
    )
    creator: Mapped[User] = relationship(
        foreign_keys=[created_by],
        lazy="joined",
    )
    updater: Mapped[User] = relationship(
        foreign_keys=[updated_by],
        lazy="joined",
    )
