# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""manual_metric_single_entity_and_free_category

Revision ID: 8abadb330266
Revises: bedb22e90a81
Create Date: 2026-05-29 10:02:36.820640+00:00

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
revision = "8abadb330266"
down_revision = "bedb22e90a81"
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


_OLD_VALUES = (
    "NETWORK",
    "CONSENSUS",
    "GOVERNANCE",
    "TOKEN_RISK",
    "ASSETS_ACTIVITY",
    "ANCHORS",
    "CONTROL",
    "ASSURANCE",
    "PROTOCOL_SCORE",
)
_NEW_VALUES = (
    "GOVERNANCE",
    "ANCHORS",
    "CONTROL",
    "ASSURANCE",
    "TOKEN_RISK",
    "PROTOCOL_SCORE",
)
_ENTITY_CATEGORY_CHECK = (
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
    "('ANCHORS','CONTROL','ASSURANCE','PROTOCOL_SCORE'))"
)


def schema_upgrades() -> None:
    """Schema upgrade migrations go here.

    1. Trim the metriccategory enum down to {GOVERNANCE, ANCHORS, CONTROL,
       ASSURANCE, TOKEN_RISK, PROTOCOL_SCORE}. Postgres has no direct
       ALTER TYPE DROP VALUE; we create a new type, swap the column, then
       drop the old type.
    2. Add the combined "exactly one entity AND category-valid-for-entity"
       CHECK constraint.

    Pre-existing data is already aligned (no rows use NETWORK / CONSENSUS /
    ASSETS_ACTIVITY); the cast is straight without coercion.
    """
    bind = op.get_bind()

    # 1. Replace the enum type.
    op.execute("ALTER TYPE metriccategory RENAME TO metriccategory_old")
    sa.Enum(*_NEW_VALUES, name="metriccategory").create(bind)
    op.execute(
        "ALTER TABLE manual_metric "
        "ALTER COLUMN category TYPE metriccategory "
        "USING category::text::metriccategory"
    )
    op.execute("DROP TYPE metriccategory_old")

    # 2. Combined entity/category validity constraint.
    op.create_check_constraint(
        "ck_manual_metric_entity_category",
        "manual_metric",
        _ENTITY_CATEGORY_CHECK,
    )


def schema_downgrades() -> None:
    """Schema downgrade migrations go here.

    Restores the broader (9-value) enum and removes the combined check.
    """
    bind = op.get_bind()

    op.drop_constraint(
        "ck_manual_metric_entity_category", "manual_metric", type_="check"
    )

    op.execute("ALTER TYPE metriccategory RENAME TO metriccategory_new")
    sa.Enum(*_OLD_VALUES, name="metriccategory").create(bind)
    op.execute(
        "ALTER TABLE manual_metric "
        "ALTER COLUMN category TYPE metriccategory "
        "USING category::text::metriccategory"
    )
    op.execute("DROP TYPE metriccategory_new")


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
