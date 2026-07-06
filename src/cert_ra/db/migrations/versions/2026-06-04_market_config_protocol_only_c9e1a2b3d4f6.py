# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""market_config becomes protocol-only; snapshot/score/favorite/weighting_profile own chain+market+label

Revision ID: c9e1a2b3d4f6
Revises: e7b3c2a1d5f9
Create Date: 2026-06-04 19:00:00.000000+00:00

The operator admin used to manually register one row per
``(protocol, chain_id, market_id_hex)`` in ``market_config``. The new
model: operators configure *protocols*, and the collector/scorer
workflow runs ``yarn <protocol>`` on every tick to discover the live
``(chainId, marketId, label)`` set from the lending-markets-rating
project.

Shape change:

* ``market_config`` keeps ``protocol`` + ``enabled`` + audit columns
  only; ``chain_id``, ``market_id_hex``, and ``label`` move out. The
  natural key tightens to ``(protocol)``.
* ``automated_market_snapshot`` and ``market_score`` gain
  ``chain_id`` / ``market_id_hex`` / ``label`` (NOT NULL) so each
  persisted artifact carries the specific market it scored and the
  human-readable label to show users.
* ``user_favorite_metric`` gains
  ``favorite_chain_id`` / ``favorite_market_id_hex`` / ``favorite_label``
  (nullable; required when ``market_config_id`` is set) so favorites
  pin a specific market within the protocol and cache its display
  label at favorite-creation time.
* ``weighting_profile`` gains the same trio (``target_chain_id`` /
  ``target_market_id_hex`` / ``target_label``) for MARKET-scope
  profiles so the precedence resolver can match a specific market and
  the editor UI can render the label.

Pre-prod data: every existing ``market_config`` row (and its
downstream snapshots, scores, favorites, MARKET-scope weighting
profiles) is dropped before the schema change so the NOT NULL adds
don't need a backfill. Auto / manual favorites and PROTOCOL-scope
weighting profiles are preserved.

Downgrade restores the old column layout and drops the new fields. No
data backfill on downgrade — the old per-market admin rows can't be
reconstructed without re-running yarn.
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
revision = "c9e1a2b3d4f6"
down_revision = "e7b3c2a1d5f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            data_upgrades()
            schema_upgrades()


def downgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            schema_downgrades()
            data_downgrades()


def schema_upgrades() -> None:
    """Apply the protocol-only / per-row-denormalisation schema changes."""
    # --- market_config: tighten natural key to (protocol) -----------------
    op.execute(
        "ALTER TABLE market_config "
        "DROP CONSTRAINT IF EXISTS uq_market_config_natural_key"
    )
    op.execute("DROP INDEX IF EXISTS ix_market_config_enabled_protocol")
    with op.batch_alter_table("market_config", schema=None) as batch_op:
        batch_op.drop_column("chain_id")
        batch_op.drop_column("market_id_hex")
        batch_op.drop_column("label")
    op.execute(
        "ALTER TABLE market_config "
        "ADD CONSTRAINT uq_market_config_protocol UNIQUE (protocol)"
    )

    # --- automated_market_snapshot: gain (chain_id, market_id_hex, label) -
    with op.batch_alter_table("automated_market_snapshot", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chain_id", sa.BigInteger(), nullable=False))
        batch_op.add_column(
            sa.Column("market_id_hex", sa.String(length=66), nullable=False)
        )
        batch_op.add_column(sa.Column("label", sa.String(length=255), nullable=False))
        batch_op.drop_index("ix_amk_snapshot_market_kind_created")
        batch_op.create_index(
            "ix_amk_snapshot_market_kind_created",
            ["market_config_id", "chain_id", "market_id_hex", "kind", "created_at"],
            unique=False,
        )

    # --- market_score: same trio -----------------------------------------
    with op.batch_alter_table("market_score", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chain_id", sa.BigInteger(), nullable=False))
        batch_op.add_column(
            sa.Column("market_id_hex", sa.String(length=66), nullable=False)
        )
        batch_op.add_column(sa.Column("label", sa.String(length=255), nullable=False))
        batch_op.drop_index("ix_market_score_market_created")
        batch_op.create_index(
            "ix_market_score_market_created",
            ["market_config_id", "chain_id", "market_id_hex", "created_at"],
            unique=False,
        )

    # --- user_favorite_metric: per-market identity + label cache ---------
    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("favorite_chain_id", sa.BigInteger(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("favorite_market_id_hex", sa.String(length=66), nullable=True)
        )
        batch_op.add_column(
            sa.Column("favorite_label", sa.String(length=255), nullable=True)
        )
    op.execute(
        "ALTER TABLE user_favorite_metric "
        "ADD CONSTRAINT ck_user_favorite_metric_market_fields CHECK ("
        "(market_config_id IS NULL "
        "AND favorite_chain_id IS NULL "
        "AND favorite_market_id_hex IS NULL "
        "AND favorite_label IS NULL) "
        "OR (market_config_id IS NOT NULL "
        "AND favorite_chain_id IS NOT NULL "
        "AND favorite_market_id_hex IS NOT NULL "
        "AND favorite_label IS NOT NULL))"
    )
    op.execute("DROP INDEX IF EXISTS uq_user_favorite_metric_market")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_favorite_metric_market "
        "ON user_favorite_metric "
        "(dashboard_id, market_config_id, favorite_chain_id, favorite_market_id_hex) "
        "WHERE market_config_id IS NOT NULL"
    )

    # --- weighting_profile: MARKET-scope gains chain/market/label --------
    op.execute(
        "ALTER TABLE weighting_profile "
        "DROP CONSTRAINT IF EXISTS uq_weighting_profile_team_scope_target_name"
    )
    op.execute(
        "ALTER TABLE weighting_profile "
        "DROP CONSTRAINT IF EXISTS ck_weighting_profile_scope_target"
    )
    with op.batch_alter_table("weighting_profile", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("target_chain_id", sa.BigInteger(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("target_market_id_hex", sa.String(length=66), nullable=True)
        )
        batch_op.add_column(
            sa.Column("target_label", sa.String(length=255), nullable=True)
        )
    op.execute(
        "ALTER TABLE weighting_profile "
        "ADD CONSTRAINT uq_weighting_profile_team_scope_target_name UNIQUE ("
        "team_id, scope, target_protocol, target_market_config_id, "
        "target_chain_id, target_market_id_hex, name)"
    )
    op.execute(
        "ALTER TABLE weighting_profile "
        "ADD CONSTRAINT ck_weighting_profile_scope_target CHECK ("
        "(scope = 'MARKET' AND target_market_config_id IS NOT NULL "
        "AND target_chain_id IS NOT NULL "
        "AND target_market_id_hex IS NOT NULL "
        "AND target_label IS NOT NULL "
        "AND target_protocol IS NULL) "
        "OR (scope = 'PROTOCOL' AND target_protocol IS NOT NULL "
        "AND target_market_config_id IS NULL "
        "AND target_chain_id IS NULL "
        "AND target_market_id_hex IS NULL "
        "AND target_label IS NULL))"
    )


def schema_downgrades() -> None:
    """Revert to the legacy per-market admin layout."""
    # weighting_profile
    op.execute(
        "ALTER TABLE weighting_profile "
        "DROP CONSTRAINT IF EXISTS ck_weighting_profile_scope_target"
    )
    op.execute(
        "ALTER TABLE weighting_profile "
        "DROP CONSTRAINT IF EXISTS uq_weighting_profile_team_scope_target_name"
    )
    with op.batch_alter_table("weighting_profile", schema=None) as batch_op:
        batch_op.drop_column("target_label")
        batch_op.drop_column("target_market_id_hex")
        batch_op.drop_column("target_chain_id")
    op.execute(
        "ALTER TABLE weighting_profile "
        "ADD CONSTRAINT uq_weighting_profile_team_scope_target_name UNIQUE ("
        "team_id, scope, target_protocol, target_market_config_id, name)"
    )
    op.execute(
        "ALTER TABLE weighting_profile "
        "ADD CONSTRAINT ck_weighting_profile_scope_target CHECK ("
        "(scope = 'MARKET' AND target_market_config_id IS NOT NULL "
        "AND target_protocol IS NULL) "
        "OR (scope = 'PROTOCOL' AND target_protocol IS NOT NULL "
        "AND target_market_config_id IS NULL))"
    )

    # user_favorite_metric
    op.execute("DROP INDEX IF EXISTS uq_user_favorite_metric_market")
    op.execute(
        "CREATE UNIQUE INDEX uq_user_favorite_metric_market "
        "ON user_favorite_metric "
        "(dashboard_id, market_config_id) "
        "WHERE market_config_id IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE user_favorite_metric "
        "DROP CONSTRAINT IF EXISTS ck_user_favorite_metric_market_fields"
    )
    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.drop_column("favorite_label")
        batch_op.drop_column("favorite_market_id_hex")
        batch_op.drop_column("favorite_chain_id")

    # market_score
    with op.batch_alter_table("market_score", schema=None) as batch_op:
        batch_op.drop_index("ix_market_score_market_created")
        batch_op.create_index(
            "ix_market_score_market_created",
            ["market_config_id", "created_at"],
            unique=False,
        )
        batch_op.drop_column("label")
        batch_op.drop_column("market_id_hex")
        batch_op.drop_column("chain_id")

    # automated_market_snapshot
    with op.batch_alter_table("automated_market_snapshot", schema=None) as batch_op:
        batch_op.drop_index("ix_amk_snapshot_market_kind_created")
        batch_op.create_index(
            "ix_amk_snapshot_market_kind_created",
            ["market_config_id", "kind", "created_at"],
            unique=False,
        )
        batch_op.drop_column("label")
        batch_op.drop_column("market_id_hex")
        batch_op.drop_column("chain_id")

    # market_config: bring back the per-market columns + natural key.
    op.execute(
        "ALTER TABLE market_config DROP CONSTRAINT IF EXISTS uq_market_config_protocol"
    )
    with op.batch_alter_table("market_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chain_id", sa.BigInteger(), nullable=False))
        batch_op.add_column(
            sa.Column("market_id_hex", sa.String(length=66), nullable=False)
        )
        batch_op.add_column(sa.Column("label", sa.String(length=255), nullable=False))
    op.execute(
        "ALTER TABLE market_config "
        "ADD CONSTRAINT uq_market_config_natural_key UNIQUE "
        "(protocol, chain_id, market_id_hex)"
    )
    op.execute(
        "CREATE INDEX ix_market_config_enabled_protocol "
        "ON market_config (enabled, protocol)"
    )


def data_upgrades() -> None:
    """Drop pre-prod per-market rows so the NOT NULL adds don't need backfill.

    Order matters — start from the deepest dependents and walk up to
    ``market_config``. ``user_favorite_metric`` is filtered so that
    only the market-targeted favorites are dropped; auto and manual
    favorites are preserved across the migration.
    """
    op.execute("DELETE FROM user_favorite_metric WHERE market_config_id IS NOT NULL")
    op.execute("DELETE FROM market_score")
    op.execute("DELETE FROM automated_market_snapshot")
    op.execute("DELETE FROM weighting_profile WHERE scope = 'MARKET'")
    op.execute("DELETE FROM market_config")


def data_downgrades() -> None:
    """No data restoration on downgrade — the old admin rows are not recoverable."""
