# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Avalanche operator-level data.

Avalanche's P-Chain doesn't carry validator names on-chain. Multiple
validators (NodeIDs) controlled by the same entity typically share a
``rewardAddress``, so we group by that address and apply curated labels
to attach human-readable names to the big operators (Coinbase, Figment,
exchanges, etc.). Unmapped reward addresses show up as a truncated
``P-avax1…`` slug.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from cert_ra.settings.rpc import get_rpc_settings

from .labels import labels_for
from .rated import RatedOperator


class _AvalancheValidator(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    node_id: str = Field(alias="nodeID")
    reward_address: str | None = Field(alias="rewardAddress", default=None)
    delegator_weight: int = 0
    stake_amount: int | None = None


class _AvalancheResult(BaseModel):
    validators: list[_AvalancheValidator]


class _AvalancheResponse(BaseModel):
    result: _AvalancheResult


def _short_addr(addr: str) -> str:
    """Compress a P-Chain address to ``P-avax1abcd…wxyz`` for display."""
    if len(addr) <= 14:  # noqa: PLR2004
        return addr
    return f"{addr[:10]}…{addr[-4:]}"


async def fetch_avalanche_operators() -> list[RatedOperator]:
    """Group P-Chain validators by reward address and label known operators.

    Stakes are summed across all validators (own stake + delegator weight)
    sharing a reward address. Validators with no reward address fall back to
    their NodeID as the operator key.
    """
    urls = get_rpc_settings().avalanche_p_urls
    if not urls:
        msg = "avalanche operators: no Avalanche RPC URLs configured"
        raise RuntimeError(msg)

    labels = labels_for("AVALANCHE_C")

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

            return _group_validators(parsed.result.validators, labels)

    msg = "avalanche operators: all P-Chain RPC nodes failed"
    raise RuntimeError(msg) from last_exc


def _group_validators(
    validators: list[_AvalancheValidator], labels: dict[str, str]
) -> list[RatedOperator]:
    grouped: dict[str, tuple[str, int, float, bool]] = {}
    for v in validators:
        key = v.reward_address or v.node_id
        weight_navax = (v.stake_amount or 0) + v.delegator_weight
        weight_avax = weight_navax / 1_000_000_000
        existing = grouped.get(key)
        if existing is None:
            label = labels.get(key)
            name = label or (
                _short_addr(v.reward_address) if v.reward_address else v.node_id
            )
            grouped[key] = (name, 1, weight_avax, label is not None)
        else:
            name, count, total, labeled = existing
            grouped[key] = (name, count + 1, total + weight_avax, labeled)

    return [
        RatedOperator(
            operator_id=key,
            name=name,
            validator_count=count,
            total_effective_balance_eth=stake,
            labeled=labeled,
        )
        for key, (name, count, stake, labeled) in grouped.items()
    ]
