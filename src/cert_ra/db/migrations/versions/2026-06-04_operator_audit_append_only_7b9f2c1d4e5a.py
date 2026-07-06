# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""operator_audit append-only trigger (PR-8, AC #32)

Revision ID: 7b9f2c1d4e5a
Revises: 621e8f5ddb57
Create Date: 2026-06-04 00:00:00.000000+00:00

Makes ``operator_audit`` immutable to the application: a BEFORE UPDATE OR
DELETE trigger rejects every DELETE and every UPDATE except the FK
SET-NULL cascade on ``target_team_id`` / ``target_user_id`` (so customer
churn still nulls the FKs without destroying the audit row — AC #121).

The trigger SQL is shared with a SQLAlchemy ``after_create`` event on the
model (so the schema built via ``metadata.create_all`` in tests/dev is
also protected). Single source: ``cert_ra/db/operator_audit_ddl.py``.

Postgres-only (plpgsql).
"""

from __future__ import annotations

from alembic import op

from cert_ra.db.operator_audit_ddl import (
    OPERATOR_AUDIT_APPEND_ONLY_CREATE_STATEMENTS,
    OPERATOR_AUDIT_APPEND_ONLY_DROP_STATEMENTS,
)

__all__ = ["downgrade", "upgrade"]

# revision identifiers, used by Alembic.
revision = "7b9f2c1d4e5a"
down_revision = "621e8f5ddb57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the append-only trigger + function (separate statements)."""
    for stmt in OPERATOR_AUDIT_APPEND_ONLY_CREATE_STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    """Drop the append-only trigger + function (separate statements)."""
    for stmt in OPERATOR_AUDIT_APPEND_ONLY_DROP_STATEMENTS:
        op.execute(stmt)
