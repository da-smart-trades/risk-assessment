# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Add ETH_AUSDC/CUSDC/LINK/STETH/WSTETH metric types to metrictype enum

Revision ID: b8d2e3f4a5c1
Revises: f2a3b4c5d6e7
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
revision = "b8d2e3f4a5c1"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

_NEW_METRIC_VALUES = (
    "ETH_AUSDC_TOTAL_SUPPLY",
    "ETH_AUSDC_TRANSFER_COUNT",
    "ETH_AUSDC_UNIQUE_ADDRESSES",
    "ETH_AUSDC_VOLUME",
    "ETH_CUSDC_TOTAL_SUPPLY",
    "ETH_CUSDC_TRANSFER_COUNT",
    "ETH_CUSDC_UNIQUE_ADDRESSES",
    "ETH_CUSDC_VOLUME",
    "ETH_LINK_TOTAL_SUPPLY",
    "ETH_LINK_TRANSFER_COUNT",
    "ETH_LINK_UNIQUE_ADDRESSES",
    "ETH_LINK_VOLUME",
    "ETH_STETH_TOTAL_SUPPLY",
    "ETH_STETH_TRANSFER_COUNT",
    "ETH_STETH_UNIQUE_ADDRESSES",
    "ETH_STETH_VOLUME",
    "ETH_WSTETH_TOTAL_SUPPLY",
    "ETH_WSTETH_TRANSFER_COUNT",
    "ETH_WSTETH_UNIQUE_ADDRESSES",
    "ETH_WSTETH_VOLUME",
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
    for value in _NEW_METRIC_VALUES:
        op.execute(f"ALTER TYPE metrictype ADD VALUE IF NOT EXISTS '{value}'")


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
