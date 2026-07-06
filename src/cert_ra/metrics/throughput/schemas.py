# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from pydantic import BaseModel

# Chains supported by the throughput fetcher. EVM chains are sourced from
# execution-layer JSON-RPC via :func:`cert_ra.metrics.throughput.evm_rpc.fetch_evm_throughput`.
# Solana is sourced from Dune's ``solana.transactions`` table because its
# RPC dialect doesn't expose a clean ``eth_feeHistory`` equivalent.
SUPPORTED_CHAINS: tuple[str, ...] = (
    "ETHEREUM",
    "ARBITRUM",
    "SOLANA",
    "INK",
    "UNICHAIN",
    "POLYGON",
    "AVALANCHE_C",
    "OPTIMISM",
    "BASE",
    # Canton is sourced from the Splice Scan API (see ``throughput/canton.py``):
    # updates/sec → TPS, rounds/sec → BPS, amulet price → gas price.
    "CANTON",
)


class ThroughputResult(BaseModel):
    """Throughput snapshot fetched together from a Dune transactions query."""

    chain: str
    gas_price: float
    transactions_per_second: float
    blocks_per_second: float


class ThroughputParams(BaseModel):
    """Single-chain throughput workflow input."""

    chain: str
