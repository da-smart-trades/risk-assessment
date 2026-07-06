# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Top-level Temporal workflows for infrastructure/auth concerns.

Distinct from ``cert_ra.metrics.<feature>.workflows`` which holds the
data-collection workflows. This package owns workflows that aren't
tied to a specific metric — currently just the shared hourly cleanup.
"""
