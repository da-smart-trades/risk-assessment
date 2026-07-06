#!/usr/bin/env python3
"""Replace manual TOKEN metrics from the packaged JSON fixtures.

Dev-friendly wrapper around :mod:`cert_ra.db.seed_manual_metrics_token_metrics`.
For each token the seeder deletes every existing ``manual_metric`` row for
that token, inserts the Evidence / Risk score / Multiplier / SCORE display
rows from ``src/cert_ra/db/fixtures/tokens/*.json``, then computes
``PD_final`` and stores it as a TOKEN_SCORE / PD_FINAL row. A superuser must
already exist in the database (it stamps created_by / updated_by).

Unlike the ``certora-risk-seed-token-metrics`` console command (which
``infra/scripts/upgrade.sh`` runs in-cluster, where DATABASE_* / CERT_RA_DB_*
are already exported), this wrapper is meant to be run with a bare
``uv run`` — which does NOT load ``.env``. So it loads the repo-root ``.env``
itself (existing process env still wins) and defaults the Postgres SSL mode
to ``disable`` for the local docker container, which serves plaintext.
Override either by exporting the corresponding env var first.

Usage:
    uv run python scripts/seed_token_metrics.py            # all packaged tokens
    uv run python scripts/seed_token_metrics.py file.json  # one token payload
    uv run python scripts/seed_token_metrics.py path/dir/  # every *.json in dir
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load repo-root .env without overriding anything already exported (direnv,
# an explicit `export CERT_RA_DB_URL=...`, CI, etc. all still take precedence).
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env", override=False)

# Local docker Postgres serves plaintext; DatabaseSettings defaults ssl_mode
# to "require", which fails against it. Default to "disable" for dev unless
# the operator asked for something stricter.
os.environ.setdefault("CERT_RA_DB_SSL_MODE", "disable")

from cert_ra.db.seed_manual_metrics_token_metrics import main  # noqa: E402

if __name__ == "__main__":
    main()
