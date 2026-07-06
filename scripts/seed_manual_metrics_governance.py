#!/usr/bin/env python3
"""Seed manual GOVERNANCE metrics for chains from a CSV.

Thin wrapper around :mod:`cert_ra.db.seed_manual_metrics_governance`, kept
for the existing local workflow. Prefer the ``certora-risk-seed-governance``
console command, which is what ``infra/scripts/initial-setup.sh`` invokes
in-cluster at first install.

Governance metrics are seeded once: the default run is a no-op if any
GOVERNANCE row already exists. Pass ``--force`` to replace them (local
development affordance for iterating on the CSV).

Usage:
    uv run python scripts/seed_manual_metrics_governance.py             # guarded
    uv run python scripts/seed_manual_metrics_governance.py --force     # replace
    uv run python scripts/seed_manual_metrics_governance.py file.csv    # explicit
"""

from __future__ import annotations

from cert_ra.db.seed_manual_metrics_governance import main

if __name__ == "__main__":
    main()
