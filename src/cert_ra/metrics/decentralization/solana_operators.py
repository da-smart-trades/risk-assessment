# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Solana operator-level data.

Solana validator identity (``nodePubkey``) is the natural operator key — one
node runs one validator. We pull current vote accounts and group by
``nodePubkey`` (vote accounts can sometimes share a node), then apply the
curated label file to attach human-readable names to the well-known
operators. Unmapped pubkeys show as a truncated slug.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from cert_ra.settings.rpc import get_rpc_settings

from .labels import labels_for
from .rated import RatedOperator


class _SolanaVoteAccount(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    vote_pubkey: str = Field(alias="votePubkey")
    node_pubkey: str = Field(alias="nodePubkey")
    activated_stake: int = Field(alias="activatedStake")


class _SolanaVoteAccountsResult(BaseModel):
    current: list[_SolanaVoteAccount]


class _SolanaVoteAccountsResponse(BaseModel):
    result: _SolanaVoteAccountsResult


def _short_pubkey(pubkey: str) -> str:
    if len(pubkey) <= 12:  # noqa: PLR2004
        return pubkey
    return f"{pubkey[:6]}…{pubkey[-4:]}"


async def fetch_solana_operators() -> list[RatedOperator]:
    """Fetch vote accounts, group by node identity, apply curated labels."""
    urls = get_rpc_settings().solana_urls
    if not urls:
        msg = "solana operators: no Solana RPC URLs configured"
        raise RuntimeError(msg)

    labels = labels_for("SOLANA")

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

            return _group_vote_accounts(parsed.result.current, labels)

    msg = "solana operators: all Solana RPC nodes failed"
    raise RuntimeError(msg) from last_exc


def _group_vote_accounts(
    accounts: list[_SolanaVoteAccount], labels: dict[str, str]
) -> list[RatedOperator]:
    grouped: dict[str, tuple[str, int, float, bool]] = {}
    for a in accounts:
        stake_sol = a.activated_stake / 1e9
        existing = grouped.get(a.node_pubkey)
        if existing is None:
            label = labels.get(a.node_pubkey)
            name = label or _short_pubkey(a.node_pubkey)
            grouped[a.node_pubkey] = (name, 1, stake_sol, label is not None)
        else:
            name, count, total, labeled = existing
            grouped[a.node_pubkey] = (name, count + 1, total + stake_sol, labeled)

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
