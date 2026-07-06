# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Direct JSON-RPC fetchers for token total supply.

Replaces the historical Dune ``mints - burns`` reconstruction (which did a
full-history scan of ``tokens.transfers`` and was the dominant Dune cost
driver for the token metrics workflow) with one canonical on-chain call:

* EVM: ``eth_call`` to ``totalSupply()`` on the ERC-20 contract.
* Solana: ``getTokenSupply`` on the SPL mint account.

Each fetcher accepts a list of RPC URLs (private first, public fallbacks
behind) and tries them in order; the first success wins. A failure on every
URL raises :class:`RuntimeError`, which the Temporal activity surfaces as a
retryable error.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field
from temporalio import activity

# ERC-20 ``totalSupply()`` selector — first 4 bytes of keccak256("totalSupply()").
# The function takes no arguments, so the calldata is just the selector.
_TOTAL_SUPPLY_SELECTOR = "0x18160ddd"
_RPC_TIMEOUT_SECONDS = 30.0


class _EthCallResponse(BaseModel):
    """Result of an ``eth_call`` JSON-RPC request (hex-encoded uint256)."""

    result: str | None = None


class _SolanaTokenSupplyValue(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    amount: str
    decimals: int


class _SolanaTokenSupplyResult(BaseModel):
    value: _SolanaTokenSupplyValue


class _SolanaTokenSupplyResponse(BaseModel):
    result: _SolanaTokenSupplyResult | None = Field(default=None)


async def _post_rpc(
    client: httpx.AsyncClient,
    url: str,
    method: str,
    params: list[object],
) -> bytes:
    response = await client.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    response.raise_for_status()
    return response.content


async def fetch_evm_total_supply(
    chain: str,
    contract_address: str,
    decimals: int,
    urls: list[str],
) -> float:
    """Fetch the current total supply for an ERC-20 token via ``eth_call``.

    Tries each URL in ``urls`` in order; raises ``RuntimeError`` if all fail.
    Returned value is scaled by ``10**decimals`` to match the units used by
    the legacy Dune-based supply reconstruction.
    """
    if not urls:
        msg = f"token_supply[{chain}]: no RPC URLs configured"
        raise RuntimeError(msg)

    params = [{"to": contract_address, "data": _TOTAL_SUPPLY_SELECTOR}, "latest"]

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_RPC_TIMEOUT_SECONDS) as client:
        for url in urls:
            try:
                raw = await _post_rpc(client, url, "eth_call", params)
                parsed = _EthCallResponse.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"token_supply[{chain}]: RPC failed url={url} error={exc}"
                )
                last_exc = exc
                continue
            if parsed.result is None or parsed.result == "0x":
                last_exc = RuntimeError(
                    f"token_supply[{chain}]: empty eth_call result from {url} "
                    f"(contract={contract_address})"
                )
                activity.logger.warning(str(last_exc))
                continue
            return int(parsed.result, 16) / (10**decimals)

    msg = f"token_supply[{chain}]: all RPC URLs failed (contract={contract_address})"
    raise RuntimeError(msg) from last_exc


async def fetch_solana_total_supply(
    mint_address: str,
    decimals: int,
    urls: list[str],
) -> float:
    """Fetch the current total supply for an SPL token via ``getTokenSupply``.

    Tries each URL in ``urls`` in order; raises ``RuntimeError`` if all fail.
    The RPC reports the raw integer ``amount`` as a string; we apply
    ``10**decimals`` ourselves to avoid losing precision through the
    ``uiAmount`` float field.
    """
    if not urls:
        msg = "token_supply[SOLANA]: no RPC URLs configured"
        raise RuntimeError(msg)

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=_RPC_TIMEOUT_SECONDS) as client:
        for url in urls:
            try:
                raw = await _post_rpc(client, url, "getTokenSupply", [mint_address])
                parsed = _SolanaTokenSupplyResponse.model_validate_json(raw)
            except Exception as exc:  # noqa: BLE001
                activity.logger.warning(
                    f"token_supply[SOLANA]: RPC failed url={url} error={exc}"
                )
                last_exc = exc
                continue
            if parsed.result is None:
                last_exc = RuntimeError(
                    f"token_supply[SOLANA]: empty getTokenSupply result from "
                    f"{url} (mint={mint_address})"
                )
                activity.logger.warning(str(last_exc))
                continue
            return int(parsed.result.value.amount) / (10**decimals)

    msg = f"token_supply[SOLANA]: all RPC URLs failed (mint={mint_address})"
    raise RuntimeError(msg) from last_exc
