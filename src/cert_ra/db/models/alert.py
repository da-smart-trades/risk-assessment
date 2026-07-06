# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase, orm_registry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.types import (
    AlertRuleKind,
    AlertSeverity,
    AlertTargetKind,
)

if TYPE_CHECKING:
    from .alert_integration import AlertIntegration
    from .team import Team
    from .user import User


alert_integration_link: Table = Table(
    "alert_integration_link",
    orm_registry.metadata,
    Column(
        "alert_id",
        ForeignKey("alert.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "integration_id",
        ForeignKey("alert_integration.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
"""Many-to-many between an alert and its non-primary integrations.

The team's primary integration for the relevant kind is applied implicitly by
the dispatcher; this join table records *additional* integrations a user has
attached to a specific alert.
"""


class Alert(UUIDAuditBase):
    """An alert rule — either operator-defined (template) or team-defined.

    A row with ``is_template=True`` and ``team_id IS NULL`` is an operator
    template visible to every team. A row with ``is_template=False`` and
    ``team_id`` set belongs to that team. The XOR is enforced at the DB layer
    via a check constraint.

    Two JSONB columns are polymorphic:

    * ``rule_config`` — shape determined by ``rule_kind`` and validated by the
      matching msgspec model in ``cert_ra.api.domain.alerts.rules``.
    * ``target_config`` — shape determined by ``target_kind`` and validated by
      the matching msgspec model in ``cert_ra.api.domain.alerts.targets``.

    Both columns are read by the evaluator activities via the registries in
    those modules and the value-source registry in
    ``cert_ra.alerts._value_sources``.
    """

    __tablename__ = "alert"
    __table_args__ = (
        # Non-template (team-owned) rules — speeds up the team's listing.
        Index(
            "ix_alert_team_enabled",
            "team_id",
            "is_enabled",
            postgresql_where=text("is_template = false"),
        ),
        # Templates — speeds up the evaluator's "all enabled templates" scan.
        Index(
            "ix_alert_template_enabled",
            "is_enabled",
            postgresql_where=text("is_template = true"),
        ),
        # Evaluator hot path: filter rules by target kind before dispatching.
        Index(
            "ix_alert_target_kind_enabled",
            "target_kind",
            "is_enabled",
        ),
        # JSONB lookups (filtering by metric_type, market_config_id, etc.).
        Index(
            "ix_alert_target_config_gin",
            "target_config",
            postgresql_using="gin",
        ),
        Index(
            "ix_alert_rule_config_gin",
            "rule_config",
            postgresql_using="gin",
        ),
        CheckConstraint(
            "(is_template = true AND team_id IS NULL) "
            "OR (is_template = false AND team_id IS NOT NULL)",
            name="template_team_xor",
        ),
    )

    team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    is_template: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    name: Mapped[str] = mapped_column(String(length=255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target_kind: Mapped[AlertTargetKind] = mapped_column(
        ENUM(AlertTargetKind, name="alerttargetkind", create_type=False),
        nullable=False,
    )
    target_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rule_kind: Mapped[AlertRuleKind] = mapped_column(
        ENUM(AlertRuleKind, name="alertrulekind"),
        nullable=False,
    )
    rule_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        ENUM(AlertSeverity, name="alertseverity"),
        nullable=False,
        default=AlertSeverity.WARNING,
        server_default=AlertSeverity.WARNING.value,
    )
    is_enabled: Mapped[bool] = mapped_column(
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
    team: Mapped[Team | None] = relationship(
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
    integrations: Mapped[list[AlertIntegration]] = relationship(
        secondary=lambda: alert_integration_link,
        lazy="selectin",
    )
