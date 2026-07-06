# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel

# Chains for which DefiLlama publishes a TVL value.
SUPPORTED_CHAINS: tuple[str, ...] = (
    "ETHEREUM",
    "ARBITRUM",
    "BASE",
    "INK",
    "UNICHAIN",
    "POLYGON",
    "AVALANCHE_C",
    "OPTIMISM",
    "SOLANA",
)


class TVLParams(BaseModel):
    """Single-chain TVL workflow input."""

    chain: str


class TVLResult(BaseModel):
    """TVL snapshot fetched from DefiLlama for a single chain."""

    chain: str
    value: float
