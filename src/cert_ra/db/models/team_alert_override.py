# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import Boolean, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .alert import Alert
    from .alert_integration import AlertIntegration
    from .team import Team


class TeamAlertOverride(UUIDAuditBase):
    """A team's per-template toggle / integration override.

    Operator templates (``alert.is_template = true``) are visible to every team
    by default. A team can opt out of a specific template by inserting a row
    here with ``is_enabled = false``, or route notifications for that template
    to a non-default integration via ``integration_id``. Team-defined alerts
    do *not* use this table — they carry ``is_enabled`` on the row itself.

    Uses the same ``UUIDAuditBase + UniqueConstraint`` pattern as
    ``TeamMember``: a surrogate UUID id with a unique ``(team_id, alert_id)``
    business key.
    """

    __tablename__ = "team_alert_override"
    __table_args__ = (UniqueConstraint("team_id", "alert_id"),)

    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"),
        nullable=False,
    )
    alert_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    integration_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("alert_integration.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -----------
    # ORM Relationships
    # ------------
    team: Mapped[Team] = relationship(foreign_keys=[team_id], lazy="joined")
    alert: Mapped[Alert] = relationship(foreign_keys=[alert_id], lazy="joined")
    integration: Mapped[AlertIntegration | None] = relationship(
        foreign_keys=[integration_id],
        lazy="joined",
    )
