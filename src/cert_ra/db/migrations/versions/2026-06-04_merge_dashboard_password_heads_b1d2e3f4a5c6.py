# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""merge dashboard + password-change migration heads

Revision ID: b1d2e3f4a5c6
Revises: f3a91c7e2b08, a3c9d1e2f4b5
Create Date: 2026-06-04 00:00:00.000000+00:00

No-op merge revision. The named-dashboards work (``f3a91c7e2b08``) and the
operator-audit / must-change-password line (``a3c9d1e2f4b5``) both branched off
``621e8f5ddb57``, leaving two Alembic heads after the branches were merged in
git. This collapses the revision graph back to a single head so
``database upgrade`` can target ``head`` again. It applies no schema changes.
"""

from __future__ import annotations

__all__ = ["downgrade", "upgrade"]

# revision identifiers, used by Alembic.
revision = "b1d2e3f4a5c6"
down_revision = ("f3a91c7e2b08", "a3c9d1e2f4b5")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op: this revision only reunites two divergent heads."""


def downgrade() -> None:
    """No-op: re-splits into the two parent heads."""
