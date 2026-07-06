# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""add user_account.must_change_password (break-glass root)

Revision ID: a3c9d1e2f4b5
Revises: 7b9f2c1d4e5a
Create Date: 2026-06-04 00:00:00.000000+00:00

Adds the boolean ``must_change_password`` flag to ``user_account``. The
break-glass root account is bootstrapped with it set, so its first login
forces a password rotation. Additive only; defaults to false.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

__all__ = ["downgrade", "upgrade"]

revision = "a3c9d1e2f4b5"
down_revision = "7b9f2c1d4e5a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the must_change_password column."""
    op.add_column(
        "user_account",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop the must_change_password column."""
    op.drop_column("user_account", "must_change_password")
