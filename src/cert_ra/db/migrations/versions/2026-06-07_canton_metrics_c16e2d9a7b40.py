# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Add Canton chain + finality_canton / decentralization_canton tables

Revision ID: c16e2d9a7b40
Revises: c9e3f4a5b6d2
Create Date: 2026-06-07 00:00:00.000000+00:00

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
revision = "c16e2d9a7b40"
down_revision = "c9e3f4a5b6d2"
branch_labels = None
depends_on = None


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
    # New ChainType value (used by the reused ``throughput`` table for Canton).
    # ``ADD VALUE`` must run outside a transaction block — hence the
    # ``autocommit_block`` wrapping in ``upgrade``.
    op.execute("ALTER TYPE chaintype ADD VALUE IF NOT EXISTS 'CANTON'")

    op.create_table(
        "finality_canton",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("latest_round_number", sa.BigInteger(), nullable=False),
        sa.Column("round_advance_seconds", sa.Float(), nullable=False),
        sa.Column("round_window_seconds", sa.Float(), nullable=False),
        sa.Column("open_round_count", sa.BigInteger(), nullable=False),
        sa.Column("ledger_freshness_seconds", sa.Float(), nullable=False),
        sa.Column("live_sv_count", sa.BigInteger(), nullable=False),
        sa.Column("voting_threshold", sa.BigInteger(), nullable=False),
        sa.Column("sv_quorum_margin", sa.BigInteger(), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_finality_canton")),
    )
    with op.batch_alter_table("finality_canton", schema=None) as batch_op:
        batch_op.create_index(
            "ix_finality_canton_created_at", ["created_at"], unique=False
        )

    op.create_table(
        "decentralization_canton",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("sv_count", sa.BigInteger(), nullable=False),
        sa.Column("validator_count", sa.BigInteger(), nullable=False),
        sa.Column("voting_threshold", sa.BigInteger(), nullable=False),
        sa.Column("gov_nakamoto_safety", sa.BigInteger(), nullable=False),
        sa.Column("gov_nakamoto_liveness", sa.BigInteger(), nullable=False),
        sa.Column("distinct_sequencer_count", sa.BigInteger(), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decentralization_canton")),
    )
    with op.batch_alter_table("decentralization_canton", schema=None) as batch_op:
        batch_op.create_index(
            "ix_decentralization_canton_created_at", ["created_at"], unique=False
        )


def schema_downgrades() -> None:
    """Schema downgrade migrations go here.

    The ``CANTON`` value added to the ``chaintype`` enum is intentionally left
    in place: PostgreSQL cannot drop an enum value without recreating the type
    and recasting every column that uses it, which is out of scope here.
    """
    with op.batch_alter_table("decentralization_canton", schema=None) as batch_op:
        batch_op.drop_index("ix_decentralization_canton_created_at")
    op.drop_table("decentralization_canton")

    with op.batch_alter_table("finality_canton", schema=None) as batch_op:
        batch_op.drop_index("ix_finality_canton_created_at")
    op.drop_table("finality_canton")


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
