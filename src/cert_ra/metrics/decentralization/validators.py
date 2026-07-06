# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Per-chain validator stake fetchers.

Each fetcher returns ``ValidatorStakes`` — a list of ``(id, stake)`` pairs
where ``stake`` is denominated in the chain's native unit (ETH, SOL, POL,
AVAX). Callers are expected to filter zero-stake entries before computing
decentralization metrics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from cert_ra.settings.rpc import get_rpc_settings

if TYPE_CHECKING:
    from .schemas import ValidatorStakes

# ---------------------------------------------------------------------------
# Ethereum — Beacon API
# ---------------------------------------------------------------------------

_ETH_ACTIVE_STATUSES = frozenset({"active_ongoing", "active_exiting", "active_slashed"})


async def fetch_ethereum_stakes() -> ValidatorStakes:
    """Fetch ``(validator_index, effective_balance_eth)`` for active validators.

    Tries each configured Ethereum URL in order. Each URL is expected to serve
    the Beacon API at ``/eth/v1/beacon/states/head/validators``.
    """
    urls = get_rpc_settings().ethereum_urls
    if not urls:
        msg = "decentralization: no Ethereum RPC URLs configured"
        raise RuntimeError(msg)

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for url in urls:
            try:
                resp = await client.get(
                    f"{url}/eth/v1/beacon/states/head/validators",
                    params={"status": "active_ongoing"},
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue

            return [
                (
                    v["index"],
                    int(v["validator"]["effective_balance"]) / 1e9,
                )
                for v in data
                if v.get("status") in _ETH_ACTIVE_STATUSES
            ]

    msg = "decentralization: all Ethereum beacon nodes failed"
    raise RuntimeError(msg) from last_exc


# ---------------------------------------------------------------------------
# Solana — getVoteAccounts
# ---------------------------------------------------------------------------


class _SolanaVoteAccount(BaseModel):
    vote_pubkey: str = Field(alias="votePubkey")
    activated_stake: int = Field(alias="activatedStake")


class _SolanaVoteAccountsResult(BaseModel):
    current: list[_SolanaVoteAccount]


class _SolanaVoteAccountsResponse(BaseModel):
    result: _SolanaVoteAccountsResult


async def fetch_solana_stakes() -> ValidatorStakes:
    """Fetch ``(vote_pubkey, activated_stake_sol)`` for current vote accounts."""
    urls = get_rpc_settings().solana_urls
    if not urls:
        msg = "decentralization: no Solana RPC URLs configured"
        raise RuntimeError(msg)

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for url in urls:
            try:
                resp = await client.post(
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getVoteAccounts",
                        "params": [{"commitment": "finalized"}],
                    },
                )
                resp.raise_for_status()
                parsed = _SolanaVoteAccountsResponse.model_validate_json(resp.content)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue

            return [
                (v.vote_pubkey, v.activated_stake / 1e9) for v in parsed.result.current
            ]

    msg = "decentralization: all Solana RPC nodes failed"
    raise RuntimeError(msg) from last_exc


# ---------------------------------------------------------------------------
# Polygon — Staking API v2
# ---------------------------------------------------------------------------

_POLYGON_STAKING_URL = "https://staking-api.polygon.technology/api/v2/validators"


class _PolygonValidator(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    id: int
    total_staked: float


class _PolygonSummary(BaseModel):
    total: int
    size: int


class _PolygonResponse(BaseModel):
    success: bool
    result: list[_PolygonValidator]
    summary: _PolygonSummary


async def fetch_polygon_stakes() -> ValidatorStakes:
    """Fetch ``(validator_id, total_staked_pol)`` by paging the Polygon Staking API.

    ``total_staked`` is returned in wei and converted to POL (1e-18).
    """
    stakes: ValidatorStakes = []
    offset = 0
    limit = 200

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            resp = await client.get(
                _POLYGON_STAKING_URL, params={"offset": offset, "limit": limit}
            )
            resp.raise_for_status()
            parsed = _PolygonResponse.model_validate_json(resp.content)
            if not parsed.success:
                msg = "decentralization: Polygon staking API reported success=false"
                raise RuntimeError(msg)

            if not parsed.result:
                break

            stakes.extend((str(v.id), v.total_staked / 1e18) for v in parsed.result)

            offset += parsed.summary.size
            if parsed.summary.total and offset >= parsed.summary.total:
                break

    return stakes


# ---------------------------------------------------------------------------
# Avalanche — platform.getCurrentValidators
# ---------------------------------------------------------------------------


class _AvalancheValidator(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    node_id: str = Field(alias="nodeID")
    delegator_weight: int = 0
    stake_amount: int | None = None


class _AvalancheResult(BaseModel):
    validators: list[_AvalancheValidator]


class _AvalancheResponse(BaseModel):
    result: _AvalancheResult


async def fetch_avalanche_stakes() -> ValidatorStakes:
    """Fetch ``(node_id, total_weight_avax)`` — own stake + delegated weight."""
    urls = get_rpc_settings().avalanche_p_urls
    if not urls:
        msg = "decentralization: no Avalanche RPC URLs configured"
        raise RuntimeError(msg)

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in urls:
            try:
                resp = await client.post(
                    url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "platform.getCurrentValidators",
                        "params": {"subnetID": None},
                        "id": 1,
                    },
                )
                resp.raise_for_status()
                parsed = _AvalancheResponse.model_validate_json(resp.content)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue

            return [
                (
                    v.node_id,
                    ((v.stake_amount or 0) + v.delegator_weight) / 1_000_000_000,
                )
                for v in parsed.result.validators
            ]

    msg = "decentralization: all Avalanche RPC nodes failed"
    raise RuntimeError(msg) from last_exc
