# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Add TOKEN_SCORE enum value; expand token-scoped metric category constraint

Revision ID: f2a3b4c5d6e7
Revises: e8c4a1b9d7f2
Create Date: 2026-06-07 00:00:00.000000+00:00

TOKEN_SCORE is a system-computed probability-of-default stored on token
rows with is_published=False. The seeder writes it on every upgrade; it
is never created through normal user flows.

Allowed categories per entity type after this migration:
  chain    → GOVERNANCE
  token    → ANCHORS / CONTROL / ASSURANCE / TOKEN_RISK / PROTOCOL_SCORE
             / TOKEN_SCORE
  protocol → ANCHORS / CONTROL / ASSURANCE / PROTOCOL_SCORE
"""

from __future__ import annotations

import warnings

from alembic import op

__all__ = ["downgrade", "upgrade"]

revision = "f2a3b4c5d6e7"
down_revision = "e8c4a1b9d7f2"
branch_labels = None
depends_on = None

_OLD = (
    "(chain IS NOT NULL AND token IS NULL AND protocol IS NULL "
    "AND category = 'GOVERNANCE') "
    "OR (chain IS NULL AND token IS NOT NULL AND protocol IS NULL "
    "AND category IN ('ANCHORS','CONTROL','ASSURANCE','TOKEN_RISK')) "
    "OR (chain IS NULL AND token IS NULL AND protocol IS NOT NULL "
    "AND category IN ('ANCHORS','CONTROL','ASSURANCE','PROTOCOL_SCORE'))"
)

_NEW = (
    "(chain IS NOT NULL AND token IS NULL AND protocol IS NULL "
    "AND category = 'GOVERNANCE') "
    "OR (chain IS NULL AND token IS NOT NULL AND protocol IS NULL "
    "AND category IN ('ANCHORS','CONTROL','ASSURANCE','TOKEN_RISK',"
    "'PROTOCOL_SCORE','TOKEN_SCORE')) "
    "OR (chain IS NULL AND token IS NULL AND protocol IS NOT NULL "
    "AND category IN ('ANCHORS','CONTROL','ASSURANCE','PROTOCOL_SCORE'))"
)


def upgrade() -> None:
    # Postgres rejects ALTER TYPE ... ADD VALUE followed by a reference to
    # the new value in the same transaction (even when the reference is a
    # bare string literal in a CHECK constraint). autocommit_block runs each
    # DDL in its own transaction so the ADD VALUE commits before the check
    # constraint is created — matches the pattern in
    # 2026-05-12_add_metric_category_values_a3f1c8e209d4.py.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE tokentype ADD VALUE IF NOT EXISTS 'CBBTC'")
            op.execute(
                "ALTER TYPE metriccategory ADD VALUE IF NOT EXISTS 'TOKEN_SCORE'"
            )
            op.drop_constraint(
                "ck_manual_metric_entity_category", "manual_metric", type_="check"
            )
            op.create_check_constraint(
                "ck_manual_metric_entity_category", "manual_metric", _NEW
            )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; only restore
    # the check constraint to the pre-migration shape.
    op.drop_constraint(
        "ck_manual_metric_entity_category", "manual_metric", type_="check"
    )
    op.create_check_constraint(
        "ck_manual_metric_entity_category", "manual_metric", _OLD
    )
