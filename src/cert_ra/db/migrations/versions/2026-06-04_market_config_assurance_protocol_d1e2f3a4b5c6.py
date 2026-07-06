# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Add MarketConfig.assurance_protocol mapping

Revision ID: d1e2f3a4b5c6
Revises: c9e1a2b3d4f6
Create Date: 2026-06-04 00:00:00.000000+00:00

A market's ``protocol`` is a lowercase yarn slug (e.g. ``aave``) while
ASSURANCE manual metrics are keyed by the uppercase ``ProtocolType`` enum.
This nullable column is the operator-set mapping between the two: set it
when configuring a protocol to point its ASSURANCE lookups at a
``ProtocolType``, or leave it ``NULL`` to declare the protocol has no
ASSURANCE metrics.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

__all__ = ["downgrade", "upgrade"]

# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c9e1a2b3d4f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_config",
        sa.Column(
            "assurance_protocol",
            postgresql.ENUM(name="protocoltype", create_type=False),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("market_config", "assurance_protocol")
