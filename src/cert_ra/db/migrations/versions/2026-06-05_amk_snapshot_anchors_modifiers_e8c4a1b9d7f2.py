# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Rename automated_market_snapshot metrics/evidence -> anchors/modifiers

Revision ID: e8c4a1b9d7f2
Revises: a7f3b2c1d9e0
Create Date: 2026-06-05 00:00:00.000000+00:00

The yarn collector emits two top-level dicts, ``anchors`` and
``modifiers`` (each a category -> metric-dict tree). The snapshot table
previously stored ``metrics`` / ``evidence`` — names that never matched
the real payload, so :class:`CollectorPayload` silently dropped the data
and both columns were always ``{}``. Rename the columns to their true
meaning; the per-row data carries over unchanged (it was empty anyway).
"""

from __future__ import annotations

from alembic import op

__all__ = ["downgrade", "upgrade"]

# revision identifiers, used by Alembic.
revision = "e8c4a1b9d7f2"
down_revision = "a7f3b2c1d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("automated_market_snapshot", "evidence", new_column_name="anchors")
    op.alter_column("automated_market_snapshot", "metrics", new_column_name="modifiers")


def downgrade() -> None:
    op.alter_column("automated_market_snapshot", "anchors", new_column_name="evidence")
    op.alter_column("automated_market_snapshot", "modifiers", new_column_name="metrics")
