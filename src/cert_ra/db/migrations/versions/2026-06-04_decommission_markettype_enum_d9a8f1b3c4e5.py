# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""decommission MarketType enum and manual_metric.market column

Revision ID: d9a8f1b3c4e5
Revises: 32332e9639c2
Create Date: 2026-06-04 11:00:00.000000+00:00

Phase 7 of automated-market-metrics. Removes the static four-market
``MarketType`` enum and its column on ``manual_metric``. Markets are
now dynamic via the ``market_config`` table; manual metrics no longer
target markets at all (ASSURANCE multipliers for the PD calculator are
scoped at the protocol level).

**Destructive**: this migration deletes every ``manual_metric`` row
where ``market IS NOT NULL`` before dropping the column. Take a DB
backup before applying in any environment with production data.

The downgrade re-creates the enum and the column but cannot restore
the deleted rows.
"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from advanced_alchemy.types import (
    GUID,
    ORA_JSONB,
    DateTimeUTC,
    EncryptedString,
    EncryptedText,
    FernetBackend,
    PasswordHash,
    StoredObject,
)
from advanced_alchemy.types.encrypted_string import PGCryptoBackend
from advanced_alchemy.types.password_hash.pwdlib import PwdlibHasher
from alembic import op
from sqlalchemy import Text  # noqa: F401
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence  # noqa: F401

__all__ = [
    "data_downgrades",
    "data_upgrades",
    "downgrade",
    "schema_downgrades",
    "schema_upgrades",
    "upgrade",
]

sa.GUID = GUID
sa.DateTimeUTC = DateTimeUTC
sa.ORA_JSONB = ORA_JSONB
sa.EncryptedString = EncryptedString
sa.EncryptedText = EncryptedText
sa.StoredObject = StoredObject
sa.PasswordHash = PasswordHash
sa.PwdlibHasher = PwdlibHasher
sa.FernetBackend = FernetBackend
sa.PGCryptoBackend = PGCryptoBackend


# revision identifiers, used by Alembic.
revision = "d9a8f1b3c4e5"
down_revision = "32332e9639c2"
branch_labels = None
depends_on = None


_MARKETTYPE_VALUES = (
    "MORPHO_USDC_WSTETH",
    "MORPHO_USDC_CBBTC",
    "AAVE_USDC",
    "COMPOUND_USDC",
)


def upgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            schema_upgrades()
            data_upgrades()


def downgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            data_downgrades()
            schema_downgrades()


def schema_upgrades() -> None:
    """Drop the market column + enum, rewriting the entity-category CHECK."""
    bind = op.get_bind()

    # 1. Delete every market-scoped manual_metric row. The CHECK forces
    #    each row to set exactly one entity column, so these are
    #    unambiguously the market-targeted ones.
    bind.execute(sa.text("DELETE FROM manual_metric WHERE market IS NOT NULL"))

    # 2. Drop and recreate the entity_category CHECK without the
    #    ``market`` branch. The original constraint name carries the
    #    project's doubled prefix (see Phase 4 migration for the
    #    rationale); we strip the ``market`` branch and keep the rest.
    op.drop_constraint(
        "ck_manual_metric_entity_category",
        "manual_metric",
        type_="check",
    )
    op.create_check_constraint(
        "ck_manual_metric_entity_category",
        "manual_metric",
        "(chain IS NOT NULL AND token IS NULL AND protocol IS NULL "
        "AND category = 'GOVERNANCE') "
        "OR (chain IS NULL AND token IS NOT NULL AND protocol IS NULL "
        "AND category IN ('ANCHORS','CONTROL','ASSURANCE','TOKEN_RISK')) "
        "OR (chain IS NULL AND token IS NULL AND protocol IS NOT NULL "
        "AND category IN ('ANCHORS','CONTROL','ASSURANCE','PROTOCOL_SCORE'))",
    )

    # 3. Drop the market column.
    with op.batch_alter_table("manual_metric", schema=None) as batch_op:
        batch_op.drop_index("ix_manual_metric_market")
        batch_op.drop_column("market")

    # 4. Drop the now-unused ENUM type.
    op.execute("DROP TYPE markettype")


def schema_downgrades() -> None:
    """Recreate the column + enum.

    The deleted rows are not restored — the downgrade only puts the
    schema back. Document this loudly in any rollback runbook.
    """
    enum_literals = ", ".join(f"'{v}'" for v in _MARKETTYPE_VALUES)
    op.execute(f"CREATE TYPE markettype AS ENUM ({enum_literals})")

    with op.batch_alter_table("manual_metric", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "market",
                postgresql.ENUM(
                    *_MARKETTYPE_VALUES,
                    name="markettype",
                    create_type=False,
                ),
                nullable=True,
            )
        )
        batch_op.create_index("ix_manual_metric_market", ["market"], unique=False)

    op.drop_constraint(
        "ck_manual_metric_entity_category",
        "manual_metric",
        type_="check",
    )
    op.create_check_constraint(
        "ck_manual_metric_entity_category",
        "manual_metric",
        "(chain IS NOT NULL AND token IS NULL AND protocol IS NULL "
        "AND market IS NULL AND category = 'GOVERNANCE') "
        "OR (chain IS NULL AND token IS NOT NULL AND protocol IS NULL "
        "AND market IS NULL AND category IN "
        "('ANCHORS','CONTROL','ASSURANCE','TOKEN_RISK')) "
        "OR (chain IS NULL AND token IS NULL AND protocol IS NOT NULL "
        "AND market IS NULL AND category IN "
        "('ANCHORS','CONTROL','ASSURANCE','PROTOCOL_SCORE')) "
        "OR (chain IS NULL AND token IS NULL AND protocol IS NULL "
        "AND market IS NOT NULL AND category IN "
        "('ANCHORS','CONTROL','ASSURANCE','PROTOCOL_SCORE'))",
    )


def data_upgrades() -> None:
    """No additional data work — the row deletion is inline in schema_upgrades."""


def data_downgrades() -> None:
    """The market-scoped rows deleted by this migration cannot be recovered."""
