# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Add protocol and market columns to manual_metric

Revision ID: beb256ba6cc2
Revises: b517b723c6be
Create Date: 2026-05-12 12:00:00.000000+00:00

"""

import warnings

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
revision = "beb256ba6cc2"
down_revision = "b517b723c6be"
branch_labels = None
depends_on = None


_PROTOCOLTYPE_VALUES = (
    "AAVE_V3",
    "MORPHO_V2",
    "COMPOUND_V3",
    "DRIFT_V2",
)
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
    """Schema upgrade migrations go here."""
    sa.Enum(*_PROTOCOLTYPE_VALUES, name="protocoltype").create(op.get_bind())
    sa.Enum(*_MARKETTYPE_VALUES, name="markettype").create(op.get_bind())

    op.add_column(
        "manual_metric",
        sa.Column(
            "protocol",
            postgresql.ENUM(
                *_PROTOCOLTYPE_VALUES, name="protocoltype", create_type=False
            ),
            nullable=True,
        ),
    )
    op.add_column(
        "manual_metric",
        sa.Column(
            "market",
            postgresql.ENUM(*_MARKETTYPE_VALUES, name="markettype", create_type=False),
            nullable=True,
        ),
    )

    with op.batch_alter_table("manual_metric", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_manual_metric_protocol"), ["protocol"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_manual_metric_market"), ["market"], unique=False
        )


def schema_downgrades() -> None:
    """Schema downgrade migrations go here."""
    with op.batch_alter_table("manual_metric", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_manual_metric_market"))
        batch_op.drop_index(batch_op.f("ix_manual_metric_protocol"))

    op.drop_column("manual_metric", "market")
    op.drop_column("manual_metric", "protocol")

    sa.Enum(*_MARKETTYPE_VALUES, name="markettype").drop(op.get_bind())
    sa.Enum(*_PROTOCOLTYPE_VALUES, name="protocoltype").drop(op.get_bind())


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
