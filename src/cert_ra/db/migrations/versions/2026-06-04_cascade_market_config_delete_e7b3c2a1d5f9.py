# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""cascade delete on market_config + snapshot FKs, merge AMM into the main head

Revision ID: e7b3c2a1d5f9
Revises: d9a8f1b3c4e5, b1d2e3f4a5c6
Create Date: 2026-06-04 14:00:00.000000+00:00

Two jobs in one revision:

1. **Merge the two outstanding alembic heads** — Phase 7 of automated-
   market-metrics (``d9a8f1b3c4e5``) and the post-rebase
   dashboard + password-change merge (``b1d2e3f4a5c6``, which already
   collapses dashboards ``f3a91c7e2b08`` with operator-audit /
   must-change-password ``a3c9d1e2f4b5``). Both branched independently
   from Phase 1, so the revision tree has two unresolved heads after
   this branch rebases onto the main branch. This revision reconciles
   them so ``database upgrade`` lands on a single head.
2. **Make ``market_config`` deletion reachable from the admin UI.**
   The original ``ON DELETE RESTRICT`` policy on
   ``automated_market_snapshot.market_config_id`` and
   ``market_score.source_amk_snapshot_id`` made the delete chain
   unreachable: an operator hit a foreign-key violation the moment any
   snapshot existed, with no UI path to purge. Both columns become
   ``ON DELETE CASCADE``, so deleting a ``MarketConfig`` row deletes
   its snapshot history and PD rows in one transaction.

Downgrade restores ``RESTRICT`` on both columns and re-introduces the
dual-head state (callers fixing dashboard/password or AMM history
would then need to add a new merge revision themselves).
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
revision = "e7b3c2a1d5f9"
down_revision = ("d9a8f1b3c4e5", "b1d2e3f4a5c6")
branch_labels = None
depends_on = None


# Note: PostgreSQL truncates constraint names to 63 chars and appends a
# 4-char hash, so the second FK lives under its truncated name rather
# than the logical one Alembic would normally generate.
_AMK_FK = "fk_automated_market_snapshot_market_config_id_market_config"
_MS_FK_STORED = "fk_market_score_source_amk_snapshot_id_automated_market_c571"
_MS_FK_LOGICAL = "fk_market_score_source_amk_snapshot_id_automated_market_snapshot"


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
    """Swap RESTRICT → CASCADE on both downstream FKs.

    Uses raw ``DROP CONSTRAINT IF EXISTS`` so the migration is
    idempotent for any dev DB that has been partially patched. PG
    silently truncates the >63-char create name, which is how the
    original constraint name ended up with a hash suffix — we accept
    whatever it gives us this time.
    """
    # automated_market_snapshot.market_config_id
    op.execute(
        f"ALTER TABLE automated_market_snapshot DROP CONSTRAINT IF EXISTS {_AMK_FK}"
    )
    op.execute(
        f"ALTER TABLE automated_market_snapshot "
        f"ADD CONSTRAINT {_AMK_FK} "
        f"FOREIGN KEY (market_config_id) REFERENCES market_config (id) "
        f"ON DELETE CASCADE"
    )

    # market_score.source_amk_snapshot_id — the original PG name was
    # truncated with a hash suffix; the new one is truncated without.
    # Drop whichever (or both) happen to be present.
    op.execute(f"ALTER TABLE market_score DROP CONSTRAINT IF EXISTS {_MS_FK_STORED}")
    op.execute(
        "ALTER TABLE market_score DROP CONSTRAINT IF EXISTS "
        "fk_market_score_source_amk_snapshot_id_automated_market_snapsho"
    )
    op.execute(
        f"ALTER TABLE market_score "
        f"ADD CONSTRAINT {_MS_FK_LOGICAL} "
        f"FOREIGN KEY (source_amk_snapshot_id) "
        f"REFERENCES automated_market_snapshot (id) "
        f"ON DELETE CASCADE"
    )


def schema_downgrades() -> None:
    """Restore RESTRICT on both FKs."""
    op.execute(f"ALTER TABLE market_score DROP CONSTRAINT IF EXISTS {_MS_FK_LOGICAL}")
    op.execute(f"ALTER TABLE market_score DROP CONSTRAINT IF EXISTS {_MS_FK_STORED}")
    op.execute(
        "ALTER TABLE market_score DROP CONSTRAINT IF EXISTS "
        "fk_market_score_source_amk_snapshot_id_automated_market_snapsho"
    )
    op.execute(
        f"ALTER TABLE market_score "
        f"ADD CONSTRAINT {_MS_FK_LOGICAL} "
        f"FOREIGN KEY (source_amk_snapshot_id) "
        f"REFERENCES automated_market_snapshot (id) "
        f"ON DELETE RESTRICT"
    )

    op.execute(
        f"ALTER TABLE automated_market_snapshot DROP CONSTRAINT IF EXISTS {_AMK_FK}"
    )
    op.execute(
        f"ALTER TABLE automated_market_snapshot "
        f"ADD CONSTRAINT {_AMK_FK} "
        f"FOREIGN KEY (market_config_id) REFERENCES market_config (id) "
        f"ON DELETE RESTRICT"
    )


def data_upgrades() -> None:
    """No additional data work."""


def data_downgrades() -> None:
    """No additional data work."""
