# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel

# Per-(chain, token) combinations supported by the token-activity workflow.
# Mirrors the matrix from the old setup: USDC on every chain that has a
# USDC contract / mint, USDT0 on the LayerZero-bridged chains, and the
# Ethereum-only token-risk tokens (WETH/USDe/AAVE/UNI).
SUPPORTED_PAIRS: tuple[tuple[str, str], ...] = (
    # USDC — all 9 chains
    ("ETHEREUM", "USDC"),
    ("ARBITRUM", "USDC"),
    ("BASE", "USDC"),
    ("INK", "USDC"),
    ("UNICHAIN", "USDC"),
    ("POLYGON", "USDC"),
    ("AVALANCHE_C", "USDC"),
    ("OPTIMISM", "USDC"),
    ("SOLANA", "USDC"),
    # USDT0 — LayerZero-bridged chains
    ("ETHEREUM", "USDT0"),
    ("INK", "USDT0"),
    ("UNICHAIN", "USDT0"),
    ("OPTIMISM", "USDT0"),
    ("POLYGON", "USDT0"),
    # Ethereum-only token risk tokens
    ("ETHEREUM", "WETH"),
    ("ETHEREUM", "USDE"),
    ("ETHEREUM", "AAVE"),
    ("ETHEREUM", "UNI"),
    ("ETHEREUM", "AUSDC"),
    ("ETHEREUM", "CUSDC"),
    ("ETHEREUM", "LINK"),
    ("ETHEREUM", "STETH"),
    ("ETHEREUM", "WSTETH"),
    ("ETHEREUM", "CBBTC"),
)


class TokenActivityParams(BaseModel):
    """Workflow input: which ``(chain, token)`` pair to refresh."""

    chain: str
    token: str


class TokenActivityResult(BaseModel):
    """One ``(chain, token, metric_type, value)`` snapshot."""

    chain: str
    token: str
    metric_type: str
    value: float


class TokenActivityBatchResult(BaseModel):
    """All metrics fetched in a single Dune call for a ``(chain, token)`` pair."""

    results: list[TokenActivityResult]
