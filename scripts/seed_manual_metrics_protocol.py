#!/usr/bin/env python3
"""Replace manual metrics for protocols from JSON payloads.

Thin wrapper around :mod:`cert_ra.db.seed_manual_metrics`, kept for the
existing local workflow. Prefer the ``certora-risk-seed-metrics`` console
command, which seeds every packaged protocol payload when run with no
arguments and is what ``infra/scripts/upgrade.sh`` invokes in-cluster.

Usage:
    uv run python scripts/seed_manual_metrics_protocol.py            # all
    uv run python scripts/seed_manual_metrics_protocol.py file.json  # one
"""

from __future__ import annotations

from cert_ra.db.seed_manual_metrics import main

if __name__ == "__main__":
    main()
