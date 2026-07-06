# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Append-only audit log for operator_tenant_admin actions.

Every write action by an operator_tenant_admin against a customer team
produces a row here, synchronously inside the action's transaction. The
Slack + customer-security-contact fan-out runs as a Temporal workflow that
reads from this table.

FK behavior:
- actor_user_id → RESTRICT. Deleting an operator with audit history is
  blocked at the DB level; the row preserves the actor identity. Aligned
  with the operator_audit immutability rule (security checklist #32).
- target_team_id, target_user_id → SET NULL. Customer churn (team or user
  deletion) preserves the audit row; the JSONB payload retains enough
  context to remain meaningful after the FK is nulled.

DB-level REVOKE of UPDATE/DELETE on this table to the application role is
applied separately in PR-8 (not in this migration).

Operator team hardening.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - used at runtime for SQLAlchemy column type

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import DDL, ForeignKey, String, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cert_ra.db.operator_audit_ddl import (
    OPERATOR_AUDIT_APPEND_ONLY_CREATE_STATEMENTS,
    OPERATOR_AUDIT_APPEND_ONLY_DROP_STATEMENTS,
)

if TYPE_CHECKING:
    from .team import Team
    from .user import User


class OperatorAudit(UUIDAuditBase):
    """Append-only audit row for an operator_tenant_admin action."""

    __tablename__ = "operator_audit"
    __table_args__ = {  # noqa: RUF012
        "comment": "Append-only audit log for operator_tenant_admin actions"
    }
    __pii_columns__ = {"actor_ip"}  # noqa: RUF012

    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="restrict"),
        nullable=False,
        index=True,
        comment="Operator who performed the action (RESTRICT blocks deletion)",
    )

    actor_session_id: Mapped[str] = mapped_column(
        String(length=128),
        nullable=False,
        comment="Session ID of the actor for forensic tracing",
    )

    actor_ip: Mapped[str] = mapped_column(
        String(length=45),
        nullable=False,
        comment="Source IP of the action",
    )

    action: Mapped[str] = mapped_column(
        String(length=64),
        nullable=False,
        index=True,
        comment="Action identifier (e.g., 'force_unlock', 'reset_mfa_only')",
    )

    target_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("team.id", ondelete="set null"),
        nullable=True,
        index=True,
        comment="Target team; nulled on team deletion (audit survives)",
    )

    target_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="set null"),
        nullable=True,
        index=True,
        comment="Target user; nulled on user deletion (audit survives)",
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Action-specific context (provider, role change, etc.). "
        "Captures enough that the row is meaningful even after FKs are nulled. "
        "Never include secrets.",
    )

    # ORM Relationships
    actor: Mapped[User] = relationship(
        foreign_keys=[actor_user_id],
        lazy="noload",
    )
    target_team: Mapped[Team | None] = relationship(
        foreign_keys=[target_team_id],
        lazy="noload",
    )
    target_user: Mapped[User | None] = relationship(
        foreign_keys=[target_user_id],
        lazy="noload",
    )


# Append-only enforcement (AC #32). Created with the table on
# ``metadata.create_all`` (tests / dev); the same SQL ships in an Alembic
# migration for production. Postgres-only (plpgsql trigger).
for _stmt in OPERATOR_AUDIT_APPEND_ONLY_CREATE_STATEMENTS:
    event.listen(
        OperatorAudit.__table__,
        "after_create",
        DDL(_stmt).execute_if(dialect="postgresql"),
    )
for _stmt in OPERATOR_AUDIT_APPEND_ONLY_DROP_STATEMENTS:
    event.listen(
        OperatorAudit.__table__,
        "before_drop",
        DDL(_stmt).execute_if(dialect="postgresql"),
    )
