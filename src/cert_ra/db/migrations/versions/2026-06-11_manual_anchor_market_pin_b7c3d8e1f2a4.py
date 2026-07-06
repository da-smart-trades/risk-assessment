# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""manual_metric: add deleted flag + optional market pin; retire ANCHORS rows

Revision ID: b7c3d8e1f2a4
Revises: a1f4c7e9b2d6
Create Date: 2026-06-11 00:00:00.000000+00:00

Wires manual ANCHORS metrics into the market Probability-of-Default
calculation. Three schema additions on ``manual_metric``:

* ``deleted`` (bool, default false) — a soft-delete flag. Deleted rows
  are excluded from every read query, the UI, and all PD math.
* ``market_chain_id`` / ``market_id_hex`` — an optional pin to one
  discovered market. NULL/NULL means the row applies to every market of
  its ``protocol`` (mapped via ``MarketConfig.assurance_protocol``); both
  set means the row applies only to that single market. A new CHECK
  (``ck_manual_metric_market_pin``) enforces both-or-neither and restricts
  pins to protocol-scoped ANCHORS rows.

Data migration — **retire pre-existing ANCHORS rows**: until now,
``category = 'ANCHORS'`` manual metrics were accepted by the form and DB
but consumed by nothing (dead data). This change makes ANCHORS rows live
against the anchors term. To avoid silently moving every market's PD when
the feature turns on, every ANCHORS row that exists at migration time is
marked ``deleted = true``. They stay in the table for later cleanup but
never surface or affect a calculation. New ANCHORS rows created after this
migration are live.

The downgrade drops the columns + constraint. The ``deleted`` flag it set
on legacy rows is lost with the column, which is acceptable — those rows
were dead before this migration too.
"""

from __future__ import annotations

import warnings

import sqlalchemy as sa
from alembic import op

__all__ = ["downgrade", "upgrade"]

# revision identifiers, used by Alembic.
revision = "b7c3d8e1f2a4"
down_revision = "a1f4c7e9b2d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.batch_alter_table("manual_metric", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "deleted",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )
            batch_op.add_column(
                sa.Column("market_chain_id", sa.BigInteger(), nullable=True)
            )
            batch_op.add_column(
                sa.Column("market_id_hex", sa.String(length=66), nullable=True)
            )
            batch_op.create_index("ix_manual_metric_deleted", ["deleted"], unique=False)
            batch_op.create_index(
                "ix_manual_metric_market_chain_id",
                ["market_chain_id"],
                unique=False,
            )
            batch_op.create_index(
                "ix_manual_metric_market_id_hex",
                ["market_id_hex"],
                unique=False,
            )
        op.create_check_constraint(
            "ck_manual_metric_market_pin",
            "manual_metric",
            "(market_chain_id IS NULL AND market_id_hex IS NULL) "
            "OR (market_chain_id IS NOT NULL AND market_id_hex IS NOT NULL "
            "AND protocol IS NOT NULL AND category = 'ANCHORS')",
        )
        # Retire every ANCHORS row that predates this feature so turning it
        # on does not silently change any market's PD.
        op.execute(
            """
            UPDATE manual_metric
            SET deleted = true
            WHERE category = 'ANCHORS'
            """
        )


def downgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        op.drop_constraint(
            "ck_manual_metric_market_pin",
            "manual_metric",
            type_="check",
        )
        with op.batch_alter_table("manual_metric", schema=None) as batch_op:
            batch_op.drop_index("ix_manual_metric_market_id_hex")
            batch_op.drop_index("ix_manual_metric_market_chain_id")
            batch_op.drop_index("ix_manual_metric_deleted")
            batch_op.drop_column("market_id_hex")
            batch_op.drop_column("market_chain_id")
            batch_op.drop_column("deleted")
