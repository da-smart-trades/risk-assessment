# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Add ANCHORS, CONTROL, ASSURANCE, PROTOCOL_SCORE to metriccategory enum

Revision ID: a3f1c8e209d4
Revises: beb256ba6cc2
Create Date: 2026-05-12 00:00:00.000000+00:00

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
revision = "a3f1c8e209d4"
down_revision = "beb256ba6cc2"
branch_labels = None
depends_on = None

_NEW_CATEGORY_VALUES = ("ANCHORS", "CONTROL", "ASSURANCE", "PROTOCOL_SCORE")


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
    for value in _NEW_CATEGORY_VALUES:
        op.execute(f"ALTER TYPE metriccategory ADD VALUE IF NOT EXISTS '{value}'")


def schema_downgrades() -> None:
    """Schema downgrade migrations go here.

    PostgreSQL does not support removing enum values directly.
    To remove them would require recreating the type and casting all columns,
    which is left as a manual operation if needed.
    """


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here!"""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here!"""
