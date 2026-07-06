# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Manual metric

Revision ID: 2c59a33bd674
Revises: f3b7f817e514
Create Date: 2026-04-28 11:00:00.000000+00:00

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
revision = "2c59a33bd674"
down_revision = "f3b7f817e514"
branch_labels = None
depends_on = None


_TOKENTYPE_VALUES = (
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
)
_METRICCATEGORY_VALUES = (
    "NETWORK",
    "CONSENSUS",
    "GOVERNANCE",
    "TOKEN_RISK",
    "ASSETS_ACTIVITY",
)
_CHAINTYPE_VALUES = (
    "ARBITRUM",
    "ETHEREUM",
    "SOLANA",
    "BASE",
    "INK",
    "UNICHAIN",
    "POLYGON",
    "AVALANCHE_C",
    "OPTIMISM",
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
    sa.Enum(*_TOKENTYPE_VALUES, name="tokentype").create(op.get_bind())
    sa.Enum(*_METRICCATEGORY_VALUES, name="metriccategory").create(op.get_bind())

    op.create_table(
        "manual_metric",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("desc", sa.Text(), nullable=False),
        sa.Column(
            "chain",
            postgresql.ENUM(*_CHAINTYPE_VALUES, name="chaintype", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "token",
            postgresql.ENUM(*_TOKENTYPE_VALUES, name="tokentype", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "category",
            postgresql.ENUM(
                *_METRICCATEGORY_VALUES, name="metriccategory", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("sub_category", sa.String(length=100), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.GUID(length=16), nullable=False),
        sa.Column("updated_by", sa.GUID(length=16), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.CheckConstraint(
            "risk_score IS NULL OR (risk_score BETWEEN 1 AND 5)",
            name="ck_manual_metric_risk_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user_account.id"],
            name=op.f("fk_manual_metric_created_by_user_account"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["user_account.id"],
            name=op.f("fk_manual_metric_updated_by_user_account"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manual_metric")),
    )
    with op.batch_alter_table("manual_metric", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_manual_metric_chain"), ["chain"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_manual_metric_token"), ["token"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_manual_metric_category"), ["category"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_manual_metric_sub_category"),
            ["sub_category"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_manual_metric_risk_score"),
            ["risk_score"],
            unique=False,
        )
        batch_op.create_index(
            "ix_manual_metric_chain_token_category",
            ["chain", "token", "category"],
            unique=False,
        )


def schema_downgrades() -> None:
    """Schema downgrade migrations go here."""
    with op.batch_alter_table("manual_metric", schema=None) as batch_op:
        batch_op.drop_index("ix_manual_metric_chain_token_category")
        batch_op.drop_index(batch_op.f("ix_manual_metric_risk_score"))
        batch_op.drop_index(batch_op.f("ix_manual_metric_sub_category"))
        batch_op.drop_index(batch_op.f("ix_manual_metric_category"))
        batch_op.drop_index(batch_op.f("ix_manual_metric_token"))
        batch_op.drop_index(batch_op.f("ix_manual_metric_chain"))

    op.drop_table("manual_metric")
    sa.Enum(*_METRICCATEGORY_VALUES, name="metriccategory").drop(op.get_bind())
    sa.Enum(*_TOKENTYPE_VALUES, name="tokentype").drop(op.get_bind())


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
