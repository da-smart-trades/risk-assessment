# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""add market_config, automated_market_snapshot, weighting_profile, weighting_profile_entry

Revision ID: 621e8f5ddb57
Revises: 6847e91bc66b
Create Date: 2026-06-03 16:47:46.693866+00:00

Adds the four tables that back the automated-market-metrics feature:

* ``market_config`` — operator-curated dynamic market list (replaces the
  legacy ``MarketType`` enum; the enum itself is dropped in a later phase
  once consumers have migrated).
* ``automated_market_snapshot`` — time-series snapshots produced by the
  5-minute collector and the hourly scorer Temporal workflows.
* ``weighting_profile`` / ``weighting_profile_entry`` — partial weight
  overrides for the PD calculator, resolved by precedence at calc time.

This migration is additive only — it does not touch existing tables.
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
revision = "621e8f5ddb57"
down_revision = "6847e91bc66b"
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
    # Create the new ENUM types before the tables that use them.
    sa.Enum(
        "ANCHOR", "CONTROL", "ASSURANCE", name="weightingprofileentrycategory"
    ).create(op.get_bind())
    sa.Enum("MARKET", "PROTOCOL", name="weightingprofilescope").create(op.get_bind())
    sa.Enum("COLLECT", "SCORE", name="marketsnapshotkind").create(op.get_bind())

    # --- market_config -----------------------------------------------------
    op.create_table(
        "market_config",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("protocol", sa.String(length=64), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("market_id_hex", sa.String(length=66), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by", sa.GUID(length=16), nullable=False),
        sa.Column("updated_by", sa.GUID(length=16), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.CheckConstraint(
            "protocol ~ '^[a-z0-9_-]+$'",
            name=op.f("ck_market_config_ck_market_config_protocol_lowercase_kebab"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user_account.id"],
            name=op.f("fk_market_config_created_by_user_account"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["user_account.id"],
            name=op.f("fk_market_config_updated_by_user_account"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_config")),
        sa.UniqueConstraint(
            "protocol",
            "chain_id",
            "market_id_hex",
            name="uq_market_config_natural_key",
        ),
    )
    with op.batch_alter_table("market_config", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_market_config_enabled"), ["enabled"], unique=False
        )
        batch_op.create_index(
            "ix_market_config_enabled_protocol",
            ["enabled", "protocol"],
            unique=False,
        )

    # --- automated_market_snapshot ----------------------------------------
    op.create_table(
        "automated_market_snapshot",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("market_config_id", sa.GUID(length=16), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "COLLECT", "SCORE", name="marketsnapshotkind", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("score", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'SCORE' AND score IS NOT NULL) "
            "OR (kind = 'COLLECT' AND score IS NULL)",
            name=op.f(
                "ck_automated_market_snapshot_ck_amk_snapshot_score_for_score_kind"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["market_config_id"],
            ["market_config.id"],
            name=op.f("fk_automated_market_snapshot_market_config_id_market_config"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automated_market_snapshot")),
    )
    with op.batch_alter_table("automated_market_snapshot", schema=None) as batch_op:
        batch_op.create_index(
            "ix_amk_snapshot_market_kind_created",
            ["market_config_id", "kind", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_automated_market_snapshot_kind"),
            ["kind"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_automated_market_snapshot_market_config_id"),
            ["market_config_id"],
            unique=False,
        )

    # --- weighting_profile ------------------------------------------------
    op.create_table(
        "weighting_profile",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("team_id", sa.GUID(length=16), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "scope",
            postgresql.ENUM(
                "MARKET",
                "PROTOCOL",
                name="weightingprofilescope",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("target_protocol", sa.String(length=64), nullable=True),
        sa.Column("target_market_config_id", sa.GUID(length=16), nullable=True),
        sa.Column("created_by", sa.GUID(length=16), nullable=False),
        sa.Column("updated_by", sa.GUID(length=16), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(scope = 'MARKET' AND target_market_config_id IS NOT NULL "
            "AND target_protocol IS NULL) "
            "OR (scope = 'PROTOCOL' AND target_protocol IS NOT NULL "
            "AND target_market_config_id IS NULL)",
            name=op.f("ck_weighting_profile_ck_weighting_profile_scope_target"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user_account.id"],
            name=op.f("fk_weighting_profile_created_by_user_account"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_market_config_id"],
            ["market_config.id"],
            name=op.f("fk_weighting_profile_target_market_config_id_market_config"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name=op.f("fk_weighting_profile_team_id_team"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["user_account.id"],
            name=op.f("fk_weighting_profile_updated_by_user_account"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weighting_profile")),
        sa.UniqueConstraint(
            "team_id",
            "scope",
            "target_protocol",
            "target_market_config_id",
            "name",
            name="uq_weighting_profile_team_scope_target_name",
        ),
    )
    with op.batch_alter_table("weighting_profile", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_weighting_profile_target_market_config_id"),
            ["target_market_config_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_weighting_profile_team_id"),
            ["team_id"],
            unique=False,
        )

    # --- weighting_profile_entry ------------------------------------------
    op.create_table(
        "weighting_profile_entry",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("weighting_profile_id", sa.GUID(length=16), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(
                "ANCHOR",
                "CONTROL",
                "ASSURANCE",
                name="weightingprofileentrycategory",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("sub_category", sa.String(length=255), nullable=False),
        sa.Column("weight", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.CheckConstraint(
            "weight >= 0",
            name=op.f(
                "ck_weighting_profile_entry_ck_weighting_profile_entry_weight_nonneg"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["weighting_profile_id"],
            ["weighting_profile.id"],
            name=op.f(
                "fk_weighting_profile_entry_weighting_profile_id_weighting_profile"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weighting_profile_entry")),
        sa.UniqueConstraint(
            "weighting_profile_id",
            "category",
            "sub_category",
            name="uq_weighting_profile_entry_natural_key",
        ),
    )
    with op.batch_alter_table("weighting_profile_entry", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_weighting_profile_entry_weighting_profile_id"),
            ["weighting_profile_id"],
            unique=False,
        )


def schema_downgrades() -> None:
    """Schema downgrade migrations go here."""
    with op.batch_alter_table("weighting_profile_entry", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_weighting_profile_entry_weighting_profile_id")
        )
    op.drop_table("weighting_profile_entry")

    with op.batch_alter_table("weighting_profile", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_weighting_profile_team_id"))
        batch_op.drop_index(batch_op.f("ix_weighting_profile_target_market_config_id"))
    op.drop_table("weighting_profile")

    with op.batch_alter_table("automated_market_snapshot", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_automated_market_snapshot_market_config_id"))
        batch_op.drop_index(batch_op.f("ix_automated_market_snapshot_kind"))
        batch_op.drop_index("ix_amk_snapshot_market_kind_created")
    op.drop_table("automated_market_snapshot")

    with op.batch_alter_table("market_config", schema=None) as batch_op:
        batch_op.drop_index("ix_market_config_enabled_protocol")
        batch_op.drop_index(batch_op.f("ix_market_config_enabled"))
    op.drop_table("market_config")

    sa.Enum("COLLECT", "SCORE", name="marketsnapshotkind").drop(op.get_bind())
    sa.Enum("MARKET", "PROTOCOL", name="weightingprofilescope").drop(op.get_bind())
    sa.Enum(
        "ANCHOR", "CONTROL", "ASSURANCE", name="weightingprofileentrycategory"
    ).drop(op.get_bind())


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
