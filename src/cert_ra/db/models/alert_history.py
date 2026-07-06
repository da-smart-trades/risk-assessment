# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import DateTime, Float, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import AlertHistoryStatus

if TYPE_CHECKING:
    from .alert import Alert
    from .team import Team


class AlertHistory(UUIDAuditBase):
    """One observation of an alert at evaluator time.

    The evaluator follows edge-trigger semantics: a row is written only when
    the rule transitions ``OK → TRIGGERED`` or ``TRIGGERED → RECOVERED``.
    Stale snapshots produce ``ERROR`` rows so gaps surface explicitly.

    ``team_id`` is denormalised onto every row even for templates, because the
    consuming team is what matters at read time and it lets us serve the
    "team's alert history" page from a single index.
    """

    __tablename__ = "alert_history"
    __table_args__ = (
        Index(
            "ix_alert_history_alert_evaluated",
            "alert_id",
            "evaluated_at",
        ),
        Index(
            "ix_alert_history_team_evaluated",
            "team_id",
            "evaluated_at",
        ),
        Index(
            "ix_alert_history_active",
            "alert_id",
            postgresql_where=text("status = 'TRIGGERED'"),
        ),
    )

    alert_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[UUID] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[AlertHistoryStatus] = mapped_column(
        ENUM(AlertHistoryStatus, name="alerthistorystatus"),
        nullable=False,
    )
    metric_value: Mapped[float | None] = mapped_column(Float(), nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float(), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # -----------
    # ORM Relationships
    # ------------
    alert: Mapped[Alert] = relationship(foreign_keys=[alert_id], lazy="joined")
    team: Mapped[Team] = relationship(foreign_keys=[team_id], lazy="joined")
