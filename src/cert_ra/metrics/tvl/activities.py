# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from decimal import Decimal

import httpx
from temporalio import activity

from cert_ra.db.models import TVL
from cert_ra.metrics._session import session_factory
from cert_ra.types import ChainType

from .schemas import SUPPORTED_CHAINS, TVLResult

_DEFILLAMA_URL = "https://api.llama.fi/v2/chains"

# DefiLlama returns chains under display names; map them to ``ChainType`` values.
_DEFILLAMA_NAME_TO_CHAIN: dict[str, str] = {
    "Ethereum": "ETHEREUM",
    "Arbitrum": "ARBITRUM",
    "Base": "BASE",
    "Ink": "INK",
    "Unichain": "UNICHAIN",
    "Polygon": "POLYGON",
    "Avalanche": "AVALANCHE_C",
    "OP Mainnet": "OPTIMISM",
    "Optimism": "OPTIMISM",
    "Solana": "SOLANA",
}


@activity.defn
async def fetch_tvl(chain: str) -> TVLResult:
    """Fetch the latest TVL value for ``chain`` from DefiLlama.

    Raises:
        ValueError: if ``chain`` is not in :data:`SUPPORTED_CHAINS`.
        RuntimeError: if DefiLlama does not report a TVL row for the chain.
    """
    chain_upper = chain.upper()
    if chain_upper not in SUPPORTED_CHAINS:
        msg = f"tvl: chain {chain_upper} not supported"
        raise ValueError(msg)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_DEFILLAMA_URL)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, list):
        msg = f"tvl: unexpected DefiLlama payload shape ({type(payload)!r})"
        raise TypeError(msg)

    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        mapped = _DEFILLAMA_NAME_TO_CHAIN.get(name, name.upper())
        if mapped != chain_upper:
            continue
        raw_value = entry.get("tvl")
        if raw_value is None:
            continue
        return TVLResult(chain=chain_upper, value=float(raw_value))

    msg = f"tvl: DefiLlama did not return a TVL row for {chain_upper}"
    raise RuntimeError(msg)


@activity.defn
async def store_tvl(result: TVLResult) -> None:
    """Persist a TVL snapshot to the database."""
    async with session_factory()() as session:
        session.add(
            TVL(
                chain=ChainType(result.chain),
                value=Decimal(str(result.value)),
            )
        )
        await session.commit()
