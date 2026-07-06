# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Rated Network operator-level data for Ethereum.

Rated exposes per-entity aggregated validator data — pools (Lido, Rocket
Pool), custodians (Coinbase, Binance, Kraken), and solo stakers. We use it to
collapse the ~1M raw Beacon validator slots into a few hundred operating
entities so the Nakamoto coefficient reflects who can actually coordinate.

The endpoint we hit is ``GET /v0/eth/operators`` (offset/size pagination,
Bearer auth).
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from cert_ra.settings.rated import get_rated_settings


class RatedOperator(BaseModel):
    """One operator entity with aggregated stake.

    The shape is shared across chains even though the source differs (Rated
    for Ethereum, Polygon Staking API for Polygon, P-Chain RPC for Avalanche,
    ``getVoteAccounts`` for Solana). Per-chain fetchers construct this
    model directly — JSON parsing is handled upstream.

    ``total_effective_balance_eth`` carries the chain's native stake unit
    (ETH, POL, AVAX, SOL) — the column name is preserved for back-compat.
    """

    operator_id: str
    """Stable identifier for the entity (chain-specific format)."""
    name: str
    """Human-readable label; falls back to a truncated id when absent."""
    validator_count: int = 0
    """Active validators operated by this entity."""
    total_effective_balance_eth: float = 0.0
    """Aggregate stake in the chain's native unit."""
    labeled: bool = True
    """Whether ``name`` came from an authoritative label (true) or is a
    fall-back rendering of the raw identifier (false). Drives the coverage
    indicator on the operator panel."""


class _RatedOperatorRaw(BaseModel):
    """Loose schema for the Rated response — fields vary across windows."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow"
    )

    operator_id: str | None = Field(alias="operatorId", default=None)
    id: str | None = None
    name: str | None = None
    validator_count: int | None = Field(alias="validatorCount", default=None)
    total_effective_balance: float | None = Field(
        alias="totalEffectiveBalance", default=None
    )


class _RatedOperatorsPage(BaseModel):
    data: list[_RatedOperatorRaw]
    next: str | None = None


# Effective balance can come back as ETH or gwei. The two ranges don't
# overlap in practice — a single validator is 32 ETH or 32e9 gwei, and even
# the largest pool (~10M ETH) tops out at 1e7 ETH well below 32e9 gwei. So
# anything above 1e10 is treated as gwei.
_GWEI_THRESHOLD = 1e10


def _normalize_operator(raw: _RatedOperatorRaw) -> RatedOperator | None:
    operator_id = raw.operator_id or raw.id
    if not operator_id:
        return None
    balance = raw.total_effective_balance or 0.0
    if balance >= _GWEI_THRESHOLD:
        balance = balance / 1e9
    return RatedOperator(
        operator_id=operator_id,
        name=raw.name or operator_id,
        validator_count=raw.validator_count or 0,
        total_effective_balance_eth=balance,
    )


async def fetch_ethereum_operators() -> list[RatedOperator]:
    """Page through ``/eth/operators`` and return one row per operating entity.

    Raises ``RuntimeError`` when no API key is configured — the caller is
    expected to gate the workflow on this.
    """
    settings = get_rated_settings()
    if settings.api_key is None:
        msg = "rated: no API key configured (CERT_RA_RATED_API_KEY)"
        raise RuntimeError(msg)

    headers = {
        "Authorization": f"Bearer {settings.api_key.get_secret_value()}",
        "Accept": "application/json",
    }
    operators: list[RatedOperator] = []
    offset = 0

    async with httpx.AsyncClient(timeout=60.0, headers=headers) as client:
        while True:
            resp = await client.get(
                f"{settings.base_url}/eth/operators",
                params={
                    "window": settings.window,
                    "size": settings.page_size,
                    "from": offset,
                    "idType": "depositAddress",
                    "poolType": "all",
                },
            )
            resp.raise_for_status()
            page = _RatedOperatorsPage.model_validate_json(resp.content)

            if not page.data:
                break

            for raw in page.data:
                normalized = _normalize_operator(raw)
                if normalized is not None:
                    operators.append(normalized)

            if len(page.data) < settings.page_size:
                break
            offset += len(page.data)

    return operators
