# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import NotificationStatus

if TYPE_CHECKING:
    from .alert_history import AlertHistory
    from .alert_integration import AlertIntegration


class Notification(UUIDAuditBase):
    """A single attempted delivery of an alert event to one integration.

    The dispatcher worker drains rows in ``PENDING`` / ``RETRYING`` and drives
    them to ``SENT`` or ``FAILED``. ``SUPPRESSED`` is reserved for rate-limit
    drops (e.g. team-level notification cap). One row per
    ``(alert_history_id, integration_id)`` pair — the evaluator never reuses
    rows; failed attempts increment ``attempt_count`` in place and stay
    ``RETRYING`` until the retry budget is exhausted.
    """

    __tablename__ = "notification"
    __table_args__ = (
        Index(
            "ix_notification_pending",
            "status",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'RETRYING')"),
        ),
        Index("ix_notification_history", "alert_history_id"),
    )

    alert_history_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_history.id", ondelete="CASCADE"),
        nullable=False,
    )
    integration_id: Mapped[UUID] = mapped_column(
        ForeignKey("alert_integration.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[NotificationStatus] = mapped_column(
        ENUM(NotificationStatus, name="notificationstatus"),
        nullable=False,
        default=NotificationStatus.PENDING,
        server_default=NotificationStatus.PENDING.value,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # -----------
    # ORM Relationships
    # ------------
    history: Mapped[AlertHistory] = relationship(
        foreign_keys=[alert_history_id],
        lazy="joined",
    )
    integration: Mapped[AlertIntegration] = relationship(
        foreign_keys=[integration_id],
        lazy="joined",
    )
