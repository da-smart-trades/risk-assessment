# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Add dashboard (named home pages) + re-parent favorites onto dashboards

Revision ID: f3a91c7e2b08
Revises: 621e8f5ddb57
Create Date: 2026-06-04 00:00:00.000000+00:00

Introduces the ``dashboard`` table (named, ownable, team-shareable home pages)
and re-parents ``user_favorite_metric`` from ``user_id`` onto ``dashboard_id``.
Existing favorites are preserved: every user that currently has favorites gets a
default "My favorites" dashboard, and their favorites are moved onto it.
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
revision = "f3a91c7e2b08"
down_revision = "621e8f5ddb57"
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
    """Create the dashboard table and add the (nullable) favorite columns."""
    op.create_table(
        "dashboard",
        sa.Column("id", sa.GUID(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_id", sa.GUID(length=16), nullable=False),
        sa.Column("team_id", sa.GUID(length=16), nullable=True),
        sa.Column(
            "visibility", sa.String(length=16), server_default="private", nullable=False
        ),
        sa.Column(
            "is_default", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTimeUTC(timezone=True), nullable=False),
        sa.CheckConstraint(
            "visibility <> 'team' OR team_id IS NOT NULL",
            name=op.f("ck_dashboard_team_visibility_requires_team"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user_account.id"],
            name=op.f("fk_dashboard_owner_id_user_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name=op.f("fk_dashboard_team_id_team"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard")),
        sa.UniqueConstraint("owner_id", "name", name="uq_dashboard_owner_name"),
    )
    with op.batch_alter_table("dashboard", schema=None) as batch_op:
        batch_op.create_index("ix_dashboard_owner_id", ["owner_id"], unique=False)
        batch_op.create_index("ix_dashboard_team_id", ["team_id"], unique=False)
        batch_op.create_index(
            "uq_dashboard_owner_default",
            ["owner_id"],
            unique=True,
            postgresql_where=sa.text("is_default"),
        )

    # Add the favorite -> dashboard link as nullable so existing rows can be
    # backfilled in data_upgrades() before we enforce NOT NULL.
    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("dashboard_id", sa.GUID(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column("position", sa.Integer(), server_default="0", nullable=False)
        )


def schema_downgrades() -> None:
    """Reverse schema_upgrades (run after data_downgrades restores user_id)."""
    with op.batch_alter_table("dashboard", schema=None) as batch_op:
        batch_op.drop_index("uq_dashboard_owner_default")
        batch_op.drop_index("ix_dashboard_team_id")
        batch_op.drop_index("ix_dashboard_owner_id")
    op.drop_table("dashboard")


def data_upgrades() -> None:
    """Backfill default dashboards, move favorites onto them, finalize constraints."""
    # 1. One default "My favorites" dashboard per user that has favorites.
    op.execute(
        """
        INSERT INTO dashboard (id, owner_id, name, visibility, is_default, position, created_at, updated_at)
        SELECT gen_random_uuid(), ufm.user_id, 'My favorites', 'private', true, 0, now(), now()
        FROM (SELECT DISTINCT user_id FROM user_favorite_metric) AS ufm
        """
    )
    # 2. Point every favorite at its owner's default dashboard.
    op.execute(
        """
        UPDATE user_favorite_metric AS ufm
        SET dashboard_id = d.id
        FROM dashboard AS d
        WHERE d.owner_id = ufm.user_id AND d.is_default = true
        """
    )

    # 3. Finalize: drop the old per-user indexes + FK + column, enforce the
    #    dashboard link, and recreate the uniqueness indexes per dashboard.
    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.drop_index("uq_user_favorite_metric_auto")
        batch_op.drop_index("uq_user_favorite_metric_manual")
        batch_op.drop_index("ix_user_favorite_metric_user_id")
        batch_op.drop_constraint(
            "fk_user_favorite_metric_user_id_user_account", type_="foreignkey"
        )
        batch_op.drop_column("user_id")
        batch_op.alter_column(
            "dashboard_id", existing_type=sa.GUID(length=16), nullable=False
        )
        batch_op.create_foreign_key(
            op.f("fk_user_favorite_metric_dashboard_id_dashboard"),
            "dashboard",
            ["dashboard_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_user_favorite_metric_dashboard_id", ["dashboard_id"], unique=False
        )
        batch_op.create_index(
            "uq_user_favorite_metric_auto",
            ["dashboard_id", "metric_type", "chain", "token"],
            unique=True,
            postgresql_where=sa.text("manual_metric_id IS NULL"),
            postgresql_nulls_not_distinct=True,
        )
        batch_op.create_index(
            "uq_user_favorite_metric_manual",
            ["dashboard_id", "manual_metric_id"],
            unique=True,
            postgresql_where=sa.text("manual_metric_id IS NOT NULL"),
        )


def data_downgrades() -> None:
    """Restore the per-user favorite addressing before the dashboard table drops."""
    # Re-add user_id (nullable for backfill), then map each favorite back to the
    # owner of its dashboard.
    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.drop_index("uq_user_favorite_metric_manual")
        batch_op.drop_index("uq_user_favorite_metric_auto")
        batch_op.drop_index("ix_user_favorite_metric_dashboard_id")
        batch_op.drop_constraint(
            op.f("fk_user_favorite_metric_dashboard_id_dashboard"), type_="foreignkey"
        )
        batch_op.add_column(sa.Column("user_id", sa.GUID(length=16), nullable=True))
    op.execute(
        """
        UPDATE user_favorite_metric AS ufm
        SET user_id = d.owner_id
        FROM dashboard AS d
        WHERE d.id = ufm.dashboard_id
        """
    )
    with op.batch_alter_table("user_favorite_metric", schema=None) as batch_op:
        batch_op.alter_column(
            "user_id", existing_type=sa.GUID(length=16), nullable=False
        )
        batch_op.create_foreign_key(
            "fk_user_favorite_metric_user_id_user_account",
            "user_account",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_user_favorite_metric_user_id", ["user_id"], unique=False
        )
        batch_op.create_index(
            "uq_user_favorite_metric_manual",
            ["user_id", "manual_metric_id"],
            unique=True,
            postgresql_where=sa.text("manual_metric_id IS NOT NULL"),
        )
        batch_op.create_index(
            "uq_user_favorite_metric_auto",
            ["user_id", "metric_type", "chain", "token"],
            unique=True,
            postgresql_nulls_not_distinct=True,
        )
        batch_op.drop_column("position")
        batch_op.drop_column("dashboard_id")
