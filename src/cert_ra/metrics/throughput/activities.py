# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

from temporalio import activity

from cert_ra.db.models import Throughput
from cert_ra.metrics._session import session_factory
from cert_ra.settings.rpc import get_rpc_settings
from cert_ra.types import ChainType

from .dune import run_dune_query
from .evm_rpc import fetch_evm_throughput
from .schemas import SUPPORTED_CHAINS, ThroughputResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from cert_ra.settings.rpc import RPCSettings

# Per-EVM-chain dispatch table: (slot_seconds, urls_resolver). Slot times are
# nominal block intervals from each chain's spec / published parameters and
# are used to convert the shared lookback windows in ``evm_rpc.py`` into the
# right block counts per chain.
#
# Ink and Unichain use single-URL settings (``ink_url`` / ``unichain_url``)
# because they're tied to provider-specific endpoints for ``optimism_syncStatus``;
# the resolver wraps the value into a one-element list.
_EVM_CHAINS: dict[str, tuple[float, Callable[[RPCSettings], list[str]]]] = {
    "ETHEREUM": (12.0, lambda s: s.ethereum_urls),
    "ARBITRUM": (0.25, lambda s: s.arbitrum_urls),
    "BASE": (2.0, lambda s: s.base_urls),
    "POLYGON": (2.1, lambda s: s.polygon_urls),
    "OPTIMISM": (2.0, lambda s: s.optimism_urls),
    "AVALANCHE_C": (2.0, lambda s: s.avalanche_c_urls),
    "INK": (1.0, lambda s: [s.ink_url]),
    "UNICHAIN": (1.0, lambda s: [s.unichain_url]),
}


def _build_solana_dune_query() -> str:
    """SQL for Solana's ``solana.transactions`` table on Dune.

    Solana uses different column names than EVM chains (``fee`` and
    ``block_slot``) and is the last chain still sourced from Dune (the others
    use JSON-RPC via :func:`fetch_evm_throughput`).
    """
    return """
SELECT avg(fee) AS avg_gas_price,
    (max(block_slot) - min(block_slot))
        / CAST(date_diff('second', min(block_time), max(block_time)) AS DOUBLE)
        AS blocks_per_second,
    count(*)
        / CAST(date_diff('second', min(block_time), max(block_time)) AS DOUBLE)
        AS transactions_per_second
FROM solana.transactions
WHERE block_date >= current_date
  AND block_time >= NOW() - INTERVAL '1' HOUR
"""


async def _fetch_solana_via_dune() -> ThroughputResult:
    """Fetch Solana throughput from Dune's ``solana.transactions`` table."""
    rows = await run_dune_query(_build_solana_dune_query())
    if not rows:
        msg = "throughput: no rows returned from Dune for SOLANA"
        raise RuntimeError(msg)

    row = rows[0]
    gas_price = row.get("avg_gas_price")
    tps = row.get("transactions_per_second")
    bps = row.get("blocks_per_second")
    if gas_price is None or tps is None or bps is None:
        msg = (
            f"throughput: null values from Dune for SOLANA "
            f"(gas_price={gas_price}, tps={tps}, bps={bps})"
        )
        raise RuntimeError(msg)

    return ThroughputResult(
        chain="SOLANA",
        gas_price=float(gas_price),  # type: ignore[arg-type]
        transactions_per_second=float(tps),  # type: ignore[arg-type]
        blocks_per_second=float(bps),  # type: ignore[arg-type]
    )


@activity.defn
async def fetch_throughput(chain: str) -> ThroughputResult:
    """Fetch gas price, TPS, and BPS for a chain.

    EVM chains (Ethereum, Arbitrum, Base, Polygon, Optimism, Avalanche
    C-Chain, Ink, Unichain) are sourced from execution-layer JSON-RPC via
    :func:`fetch_evm_throughput`. Solana is still sourced from Dune's
    ``solana.transactions`` table because the Solana RPC dialect doesn't
    expose a clean equivalent of ``eth_feeHistory``.
    """
    chain_upper = chain.upper()
    if chain_upper not in SUPPORTED_CHAINS:
        msg = f"throughput: chain {chain_upper} not supported"
        raise ValueError(msg)

    if chain_upper == ChainType.SOLANA.value:
        return await _fetch_solana_via_dune()

    if chain_upper == ChainType.CANTON.value:
        from .canton import fetch_canton_throughput

        return await fetch_canton_throughput()

    if chain_upper not in _EVM_CHAINS:
        msg = f"throughput: no RPC fetcher configured for {chain_upper}"
        raise RuntimeError(msg)

    slot_seconds, urls_resolver = _EVM_CHAINS[chain_upper]
    urls = urls_resolver(get_rpc_settings())
    return await fetch_evm_throughput(chain_upper, slot_seconds, urls)


@activity.defn
async def store_throughput(result: ThroughputResult) -> None:
    """Persist a throughput snapshot to the database."""
    async with session_factory()() as session:
        session.add(
            Throughput(
                chain=ChainType(result.chain),
                gas_price=result.gas_price,
                transactions_per_second=result.transactions_per_second,
                blocks_per_second=result.blocks_per_second,
            )
        )
        await session.commit()
