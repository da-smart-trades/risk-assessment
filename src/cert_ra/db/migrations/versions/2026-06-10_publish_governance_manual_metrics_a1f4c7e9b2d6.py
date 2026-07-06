# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Publish existing operator-owned GOVERNANCE manual metrics

Revision ID: a1f4c7e9b2d6
Revises: c16e2d9a7b40
Create Date: 2026-06-10 00:00:00.000000+00:00

The governance seeder (``certora-risk-seed-governance``) historically never
set ``is_published``, so every governance row it wrote landed as a draft
(``is_published=False``, the column's server default). The chain dashboard,
markets assurance, and weighting profiles all filter on
``is_published=True``, so those rows existed in ``manual_metric`` but never
surfaced anywhere — operators saw only the automatically collected
governance events. The protocol / token-metrics seeders were fixed to set
``is_published=True`` in 6e94c88, but governance was missed; the seeder is
fixed alongside this migration.

The seed-once guard in the governance seeder means a re-seed will NOT touch
an environment that already has governance rows, so the seeder fix alone
cannot reach already-installed environments (prod / staging). This data
migration publishes them: it flips shared (``team_id IS NULL``) GOVERNANCE
rows to ``is_published=True``.

Scope rationale:
  - ``team_id IS NULL`` only — team-owned governance metrics stay under the
    owning team's publish workflow; we never auto-publish a team's drafts.
  - Because these rows have been invisible since they were seeded, there
    are no intentional operator drafts among them to preserve.

Idempotent: re-running only touches rows still ``is_published=False``.
The downgrade is intentionally a no-op — we will not un-publish rows, since
doing so would also hide any governance metrics an operator has since
curated through the UI.
"""

from __future__ import annotations

import warnings

from alembic import op

__all__ = ["downgrade", "upgrade"]

# revision identifiers, used by Alembic.
revision = "a1f4c7e9b2d6"
down_revision = "c16e2d9a7b40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        op.execute(
            """
            UPDATE manual_metric
            SET is_published = true
            WHERE category = 'GOVERNANCE'
              AND team_id IS NULL
              AND is_published = false
            """
        )


def downgrade() -> None:
    # Intentional no-op: un-publishing would also hide governance metrics an
    # operator has curated through the UI since this migration ran.
    pass
