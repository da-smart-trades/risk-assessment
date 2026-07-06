# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Alerts: polymorphic targets + stddev rule kind

* Adds ``STDDEV_DEVIATION`` to the ``alertrulekind`` enum.
* Adds a new ``alerttargetkind`` enum (METRIC, MARKET_PD, MARKET_ANCHOR,
  MARKET_CONTROL).
* Adds ``alert.target_kind`` + ``alert.target_config`` (JSONB), backfills
  them from the existing ``(chain, token, metric_type)`` triple, then drops
  those three columns and the matching index.
* Switches the secondary-index set on ``alert`` to a ``(target_kind, is_enabled)``
  btree + a GIN over ``target_config``.

Revision ID: a7f3b2c1d9e0
Revises: d1e2f3a4b5c6
Create Date: 2026-06-04 00:00:00.000000+00:00

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
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

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
revision = "a7f3b2c1d9e0"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


_TARGET_KIND_VALUES = ("METRIC", "MARKET_PD", "MARKET_ANCHOR", "MARKET_CONTROL")


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
    """Schema upgrade migrations go here."""
    # 1. Extend the rule-kind enum with STDDEV_DEVIATION.
    op.execute(
        "ALTER TYPE alertrulekind ADD VALUE IF NOT EXISTS 'STDDEV_DEVIATION'"
    )

    # 2. Create the target-kind enum.
    sa.Enum(*_TARGET_KIND_VALUES, name="alerttargetkind").create(
        op.get_bind(), checkfirst=True
    )

    # 3. Add the new target columns as nullable; backfill below; then NOT NULL.
    op.add_column(
        "alert",
        sa.Column(
            "target_kind",
            postgresql.ENUM(
                *_TARGET_KIND_VALUES,
                name="alerttargetkind",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "alert",
        sa.Column(
            "target_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # 4. Backfill: every existing row is a METRIC target. Build the JSONB
    # from the legacy chain / token / metric_type columns. ``jsonb_strip_nulls``
    # drops chain / token when they were NULL, matching the new struct shape.
    op.execute(
        """
        UPDATE alert
        SET
            target_kind = 'METRIC'::alerttargetkind,
            target_config = jsonb_strip_nulls(
                jsonb_build_object(
                    'type', 'METRIC',
                    'metricType', metric_type::text,
                    'chain', chain::text,
                    'token', token::text
                )
            )
        WHERE target_kind IS NULL
        """
    )

    # 5. Lock the columns down.
    op.alter_column("alert", "target_kind", nullable=False)
    op.alter_column("alert", "target_config", nullable=False)

    # 6. Replace the metric-tuple index with the new target indexes.
    op.drop_index("ix_alert_metric", table_name="alert")
    op.create_index(
        "ix_alert_target_kind_enabled",
        "alert",
        ["target_kind", "is_enabled"],
    )
    op.create_index(
        "ix_alert_target_config_gin",
        "alert",
        ["target_config"],
        postgresql_using="gin",
    )

    # 7. Drop the legacy columns now that target_config carries the data.
    op.drop_column("alert", "metric_type")
    op.drop_column("alert", "chain")
    op.drop_column("alert", "token")


def schema_downgrades() -> None:
    """Schema downgrade migrations go here.

    PostgreSQL does not support removing enum values directly, so the
    ``STDDEV_DEVIATION`` value added to ``alertrulekind`` is left in place.
    Any rule rows using it must be deleted by hand before downgrading.
    """
    # 1. Re-add the legacy columns as nullable so backfill can run.
    op.add_column(
        "alert",
        sa.Column(
            "chain",
            postgresql.ENUM(
                "ARBITRUM",
                "ETHEREUM",
                "SOLANA",
                "BASE",
                "INK",
                "UNICHAIN",
                "POLYGON",
                "AVALANCHE_C",
                "OPTIMISM",
                name="chaintype",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "alert",
        sa.Column(
            "token",
            postgresql.ENUM(
                "WETH",
                "USDE",
                "AAVE",
                "UNI",
                "USDC",
                "USDT0",
                "AUSDC",
                "CUSDC",
                "LINK",
                "STETH",
                "WSTETH",
                name="tokentype",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "alert",
        sa.Column(
            "metric_type",
            postgresql.ENUM(name="metrictype", create_type=False),
            nullable=True,
        ),
    )

    # 2. Backfill the legacy columns from target_config. Rows with non-METRIC
    # target kinds cannot be expressed in the old schema, so they are deleted.
    op.execute("DELETE FROM alert WHERE target_kind <> 'METRIC'")
    op.execute(
        """
        UPDATE alert
        SET
            metric_type = (target_config ->> 'metricType')::metrictype,
            chain = NULLIF(target_config ->> 'chain', '')::chaintype,
            token = NULLIF(target_config ->> 'token', '')::tokentype
        """
    )
    op.alter_column("alert", "metric_type", nullable=False)

    # 3. Restore the legacy index and drop the new ones.
    op.create_index("ix_alert_metric", "alert", ["chain", "token", "metric_type"])
    op.drop_index("ix_alert_target_config_gin", table_name="alert")
    op.drop_index("ix_alert_target_kind_enabled", table_name="alert")

    # 4. Drop the new columns and the target-kind enum.
    op.drop_column("alert", "target_config")
    op.drop_column("alert", "target_kind")
    sa.Enum(name="alerttargetkind").drop(op.get_bind(), checkfirst=True)


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
