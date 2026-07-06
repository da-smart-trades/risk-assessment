# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""add market_score, extend favorites with market_config_id, add MARKET_SCORE category

Revision ID: 32332e9639c2
Revises: f3a91c7e2b08
Create Date: 2026-06-04 09:02:03.785482+00:00

Phase 4 of automated-market-metrics.

Ordering note: this migration is chained after ``f3a91c7e2b08``
(named-dashboards), which re-parents ``user_favorite_metric`` from
``user_id`` onto ``dashboard_id``. Both features independently reshaped
``user_favorite_metric``; depending on the dashboard migration here (rather
than branching off ``621e8f5ddb57`` as a sibling) gives a single, buildable
order, and the favorite indexes below are keyed on ``dashboard_id`` to match
the post-dashboard schema (and the ``UserFavoriteMetric`` model).

* New ``market_score`` table — one row per computed Probability of
  Default. The favorites resolver reads the latest row per market_config
  to render the star value; the show page reads the time series for the
  trend chart.
* ``user_favorite_metric`` gets a third XOR'd target column
  ``market_config_id`` so users can favorite an entire market and have
  the value resolve to the latest ``MarketScore.final_pd``.
* ``MARKET_SCORE`` added to the ``metriccategory`` Postgres ENUM. The
  value is reserved for the automated PD; it never appears on a
  ``manual_metric`` row at the application layer.
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
from alembic_postgresql_enum import TableReference
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
revision = "32332e9639c2"
# Depends on the named-dashboards migration (which drops user_favorite_metric.
# user_id and adds dashboard_id) so the favorite-index ops below run against
# the dashboard-keyed schema. Was a sibling of f3a91c7e2b08 off 621e8f5ddb57,
# which produced an unbuildable order (this migration referenced the dropped
# user_id column).
down_revision = "f3a91c7e2b08"
branch_labels = None
depends_on = None


_METRICCATEGORY_NEW = [
    "GOVERNANCE",
    "ANCHORS",
    "CONTROL",
    "ASSURANCE",
    "TOKEN_RISK",
    "PROTOCOL_SCORE",
    "MARKET_SCORE",
]
_METRICCATEGORY_OLD = _METRICCATEGORY_NEW[:-1]
_METRICCATEGORY_AFFECTED = [
    TableReference(
        table_schema="public",
        table_name="manual_metric",
        column_name="category",
    ),
]


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
    # --- market_score -----------------------------------------------------
    op.create_table(
        "market_score",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("market_config_id", sa.GUID(length=16), nullable=False),
        sa.Column("source_amk_snapshot_id", sa.GUID(length=16), nullable=False),
        sa.Column("final_pd", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("anchors_term", sa.Numeric(precision=8, scale=6), nullable=False),
        sa.Column("control_term", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("assurance_term", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.CheckConstraint(
            "anchors_term >= 0",
            name=op.f("ck_market_score_ck_market_score_anchors_term_nonneg"),
        ),
        sa.CheckConstraint(
            "assurance_term >= 0",
            name=op.f("ck_market_score_ck_market_score_assurance_term_nonneg"),
        ),
        sa.CheckConstraint(
            "control_term >= 0",
            name=op.f("ck_market_score_ck_market_score_control_term_nonneg"),
        ),
        sa.CheckConstraint(
            "final_pd >= 0",
            name=op.f("ck_market_score_ck_market_score_final_pd_nonneg"),
        ),
        sa.ForeignKeyConstraint(
            ["market_config_id"],
            ["market_config.id"],
            name=op.f("fk_market_score_market_config_id_market_config"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_amk_snapshot_id"],
            ["automated_market_snapshot.id"],
            name=op.f(
                "fk_market_score_source_amk_snapshot_id_automated_market_snapshot"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_score")),
    )
    with op.batch_alter_table("market_score", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_market_score_market_config_id"),
            ["market_config_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_market_score_market_created",
            ["market_config_id", "created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_market_score_source_amk_snapshot_id"),
            ["source_amk_snapshot_id"],
            unique=False,
        )

    # --- user_favorite_metric : add market_config_id target ---------------
    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("market_config_id", sa.GUID(length=16), nullable=True)
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_user_favorite_metric_market_config_id_market_config"),
            "market_config",
            ["market_config_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # Drop and recreate the XOR constraint with the third (market) branch.
    op.drop_constraint(
        "ck_user_favorite_metric_target_xor",
        "user_favorite_metric",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_favorite_metric_target_xor",
        "user_favorite_metric",
        "(CASE WHEN metric_type IS NOT NULL THEN 1 ELSE 0 END) + "
        "(CASE WHEN manual_metric_id IS NOT NULL THEN 1 ELSE 0 END) + "
        "(CASE WHEN market_config_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
    )

    # The pre-existing auto-favorite partial index excluded only manual
    # favorites. We need to also exclude market favorites so they don't
    # collide on the (metric_type=NULL, chain=NULL, token=NULL) tuple.
    op.drop_index(
        "uq_user_favorite_metric_auto",
        table_name="user_favorite_metric",
        postgresql_where=sa.text("manual_metric_id IS NULL"),
    )
    op.create_index(
        "uq_user_favorite_metric_auto",
        "user_favorite_metric",
        ["dashboard_id", "metric_type", "chain", "token"],
        unique=True,
        postgresql_where=sa.text(
            "manual_metric_id IS NULL AND market_config_id IS NULL"
        ),
        postgresql_nulls_not_distinct=True,
    )

    # Per-market uniqueness: a dashboard can favorite a given market at most once.
    op.create_index(
        "uq_user_favorite_metric_market",
        "user_favorite_metric",
        ["dashboard_id", "market_config_id"],
        unique=True,
        postgresql_where=sa.text("market_config_id IS NOT NULL"),
    )

    # --- metriccategory ENUM gets MARKET_SCORE ----------------------------
    # ``ck_manual_metric_ck_manual_metric_entity_category`` references the
    # ENUM literals directly, so ``sync_enum_values`` would fail with
    # ``DependentObjectsStillExist``. Drop the constraint, sync the enum,
    # then recreate the constraint pinned to the same allowed values
    # (MARKET_SCORE is intentionally *not* added — the value lives only at
    # the application layer for now).
    op.drop_constraint(
        "ck_manual_metric_entity_category",
        "manual_metric",
        type_="check",
    )
    op.sync_enum_values(
        enum_schema="public",
        enum_name="metriccategory",
        new_values=_METRICCATEGORY_NEW,
        affected_columns=_METRICCATEGORY_AFFECTED,
        enum_values_to_rename=[],
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


def schema_downgrades() -> None:
    """Schema downgrade migrations go here."""
    op.drop_constraint(
        "ck_manual_metric_entity_category",
        "manual_metric",
        type_="check",
    )
    op.sync_enum_values(
        enum_schema="public",
        enum_name="metriccategory",
        new_values=_METRICCATEGORY_OLD,
        affected_columns=_METRICCATEGORY_AFFECTED,
        enum_values_to_rename=[],
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

    op.drop_index(
        "uq_user_favorite_metric_market",
        table_name="user_favorite_metric",
        postgresql_where=sa.text("market_config_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_user_favorite_metric_auto",
        table_name="user_favorite_metric",
        postgresql_where=sa.text(
            "manual_metric_id IS NULL AND market_config_id IS NULL"
        ),
    )
    op.create_index(
        "uq_user_favorite_metric_auto",
        "user_favorite_metric",
        ["dashboard_id", "metric_type", "chain", "token"],
        unique=True,
        postgresql_where=sa.text("manual_metric_id IS NULL"),
        postgresql_nulls_not_distinct=True,
    )

    op.drop_constraint(
        "ck_user_favorite_metric_target_xor",
        "user_favorite_metric",
        type_="check",
    )
    op.create_check_constraint(
        "ck_user_favorite_metric_target_xor",
        "user_favorite_metric",
        "(metric_type IS NOT NULL AND manual_metric_id IS NULL) "
        "OR (metric_type IS NULL AND manual_metric_id IS NOT NULL)",
    )

    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_user_favorite_metric_market_config_id_market_config"),
            type_="foreignkey",
        )
        batch_op.drop_column("market_config_id")

    with op.batch_alter_table("market_score", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_market_score_source_amk_snapshot_id"))
        batch_op.drop_index("ix_market_score_market_created")
        batch_op.drop_index(batch_op.f("ix_market_score_market_config_id"))
    op.drop_table("market_score")


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
