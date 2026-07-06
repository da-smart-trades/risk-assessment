# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel

# Per-(chain, event_type) combinations supported by the governance workflow.
#
# Ethereum tracks two upgrade-pipeline signals:
#   - ``confirmed_eips`` — count of EIPs cited in the meta-EIP for the next
#     mainnet hardfork (i.e. confirmed for inclusion). Trended over time.
#   - ``last_call_eips`` — count of EIPs across the repo whose frontmatter
#     ``status`` is ``Last Call``, i.e. likely to be finalized soon.
# Arbitrum tracks forum proposals plus Timelock execution and Security
# Council emergency events. Base tracks UpgradeExecutor events. Solana
# tracks open SIMD PRs.
SUPPORTED_EVENTS: tuple[tuple[str, str], ...] = (
    ("ETHEREUM", "confirmed_eips"),
    ("ETHEREUM", "last_call_eips"),
    ("ARBITRUM", "proposals"),
    ("ARBITRUM", "execution"),
    ("ARBITRUM", "emergency"),
    ("BASE", "execution"),
    ("SOLANA", "proposals"),
)


class GovernanceParams(BaseModel):
    """Workflow input identifying which governance feed to refresh."""

    chain: str
    event_type: str


class GovernanceResult(BaseModel):
    """Governance event count fetched from the upstream feed."""

    chain: str
    event_type: str
    count: int
