# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from collections.abc import Callable, Mapping  # noqa: TC003
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from temporalio import activity

from cert_ra.db.models import TokenActivity
from cert_ra.metrics._session import session_factory
from cert_ra.metrics.throughput.dune import run_dune_query
from cert_ra.settings.rpc import RPCSettings, get_rpc_settings
from cert_ra.types import ChainType, MetricType, TokenType

from .schemas import (
    SUPPORTED_PAIRS,
    TokenActivityBatchResult,
    TokenActivityParams,
    TokenActivityResult,
)
from .supply_rpc import fetch_evm_total_supply, fetch_solana_total_supply

# Dune queries for token transfers lag real-time by ~3 hours; mirror the
# old-setup constants so the polling window is consistent.
_DUNE_LAG_BUFFER = timedelta(hours=3)
_DEFAULT_LOOKBACK = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class _EvmTokenConfig:
    """Dune query parameters for an EVM ``(chain, token)`` pair."""

    dune_chain: str
    contract_address: str
    decimals: int


@dataclass(frozen=True, slots=True)
class _SolanaTokenConfig:
    """Dune query parameters for a Solana ``(chain, token)`` pair."""

    mint_address: str
    decimals: int


_TokenConfig = _EvmTokenConfig | _SolanaTokenConfig


# Mapping of ``(chain, token)`` to Dune query parameters. Sourced from the
# old-setup ``cert_ra.configurations`` modules.
_TOKEN_CONFIGS: Mapping[tuple[str, str], _TokenConfig] = {
    # --- USDC ---------------------------------------------------------------
    ("ETHEREUM", "USDC"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        decimals=6,
    ),
    ("ARBITRUM", "USDC"): _EvmTokenConfig(
        dune_chain="arbitrum",
        contract_address="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
        decimals=6,
    ),
    ("BASE", "USDC"): _EvmTokenConfig(
        dune_chain="base",
        contract_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        decimals=6,
    ),
    ("INK", "USDC"): _EvmTokenConfig(
        dune_chain="ink",
        contract_address="0x2D270e6886d130D724215A266106e6832161EAEd",
        decimals=6,
    ),
    ("UNICHAIN", "USDC"): _EvmTokenConfig(
        dune_chain="unichain",
        contract_address="0x078d782b760474a361dda0af3839290b0ef57ad6",
        decimals=6,
    ),
    ("POLYGON", "USDC"): _EvmTokenConfig(
        dune_chain="polygon",
        contract_address="0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
        decimals=6,
    ),
    ("AVALANCHE_C", "USDC"): _EvmTokenConfig(
        dune_chain="avalanche_c",
        contract_address="0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",
        decimals=6,
    ),
    ("OPTIMISM", "USDC"): _EvmTokenConfig(
        dune_chain="optimism",
        contract_address="0x0b2c639c533813f4aa9d7837caf62653d097ff85",
        decimals=6,
    ),
    ("SOLANA", "USDC"): _SolanaTokenConfig(
        mint_address="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        decimals=6,
    ),
    # --- USDT0 --------------------------------------------------------------
    ("ETHEREUM", "USDT0"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
        decimals=6,
    ),
    ("INK", "USDT0"): _EvmTokenConfig(
        dune_chain="ink",
        contract_address="0x0200C29006150606B650577BBE7B6248F58470c1",
        decimals=6,
    ),
    ("UNICHAIN", "USDT0"): _EvmTokenConfig(
        dune_chain="unichain",
        contract_address="0x9151434b16b9763660705744891fA906F660EcC5",
        decimals=6,
    ),
    ("OPTIMISM", "USDT0"): _EvmTokenConfig(
        dune_chain="optimism",
        contract_address="0x01bFF41798a0BcF287b996046Ca68b395DbC1071",
        decimals=6,
    ),
    ("POLYGON", "USDT0"): _EvmTokenConfig(
        dune_chain="polygon",
        contract_address="0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
        decimals=6,
    ),
    # --- Ethereum-only token risk tokens ------------------------------------
    ("ETHEREUM", "WETH"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        decimals=18,
    ),
    ("ETHEREUM", "USDE"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0x4c9EDD5852cd905f086C759E8383e09bff1E68B3",
        decimals=18,
    ),
    ("ETHEREUM", "AAVE"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",
        decimals=18,
    ),
    ("ETHEREUM", "UNI"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        decimals=18,
    ),
    # aUSDC tracks the Aave V3 ``aEthUSDC`` receipt token. v2's aUSDC
    # (0xBcca60bB...) is legacy; AAVE_V3 is the only Aave version listed in
    # ProtocolType so v3 is the right pair here.
    ("ETHEREUM", "AUSDC"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
        decimals=6,
    ),
    # cUSDC is the Compound V2 cToken — v3 doesn't issue cTokens (it uses
    # the "Comet" base contracts under a different naming scheme), so cUSDC
    # is unambiguously v2. cTokens use 8 decimals regardless of underlying.
    ("ETHEREUM", "CUSDC"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0x39AA39c021dfbaE8faC545936693aC917d5E7563",
        decimals=8,
    ),
    ("ETHEREUM", "LINK"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0x514910771AF9Ca656af840dff83E8264EcF986CA",
        decimals=18,
    ),
    # stETH is Lido's rebasing receipt token. ``totalSupply()`` returns the
    # total pooled ETH (i.e., the rebased stETH amount), which is the right
    # headline figure for "amount of stETH in circulation".
    ("ETHEREUM", "STETH"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84",
        decimals=18,
    ),
    ("ETHEREUM", "WSTETH"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0",
        decimals=18,
    ),
    # Coinbase wrapped BTC. Deployed via CREATE2 so the same address is used
    # on Base; we still treat this as Ethereum-only here since the existing
    # token-risk pattern is Ethereum-only.
    ("ETHEREUM", "CBBTC"): _EvmTokenConfig(
        dune_chain="ethereum",
        contract_address="0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
        decimals=8,
    ),
}


# Per-token list of metric types to emit. Each metric type maps to a column
# returned by the Dune SQL query. Order is preserved across chains.
_USDC_METRICS = (
    MetricType.USDC_INFLOW,
    MetricType.USDC_OUTFLOW,
    MetricType.USDC_UNIQUE_ADDRESSES,
    MetricType.USDC_TRANSACTION_COUNT,
    MetricType.USDC_TOTAL_SUPPLY,
)
_USDT0_METRICS = (
    MetricType.USDT0_INFLOW,
    MetricType.USDT0_OUTFLOW,
    MetricType.USDT0_UNIQUE_ADDRESSES,
    MetricType.USDT0_TRANSACTION_COUNT,
    MetricType.USDT0_TOTAL_AMOUNT_TRANSFERS,
)
_WETH_METRICS = (
    MetricType.ETH_WETH_INFLOW,
    MetricType.ETH_WETH_OUTFLOW,
    MetricType.ETH_WETH_TOTAL_SUPPLY,
)
_USDE_METRICS = (
    MetricType.ETH_USDE_TOTAL_SUPPLY,
    MetricType.ETH_USDE_TRANSFER_COUNT,
    MetricType.ETH_USDE_UNIQUE_ADDRESSES,
    MetricType.ETH_USDE_VOLUME,
)
_AAVE_METRICS = (
    MetricType.ETH_AAVE_TOTAL_SUPPLY,
    MetricType.ETH_AAVE_TRANSFER_COUNT,
    MetricType.ETH_AAVE_UNIQUE_ADDRESSES,
    MetricType.ETH_AAVE_VOLUME,
)
_UNI_METRICS = (
    MetricType.ETH_UNI_TOTAL_SUPPLY,
    MetricType.ETH_UNI_TRANSFER_COUNT,
    MetricType.ETH_UNI_UNIQUE_ADDRESSES,
    MetricType.ETH_UNI_VOLUME,
)
_AUSDC_METRICS = (
    MetricType.ETH_AUSDC_TOTAL_SUPPLY,
    MetricType.ETH_AUSDC_TRANSFER_COUNT,
    MetricType.ETH_AUSDC_UNIQUE_ADDRESSES,
    MetricType.ETH_AUSDC_VOLUME,
)
_CUSDC_METRICS = (
    MetricType.ETH_CUSDC_TOTAL_SUPPLY,
    MetricType.ETH_CUSDC_TRANSFER_COUNT,
    MetricType.ETH_CUSDC_UNIQUE_ADDRESSES,
    MetricType.ETH_CUSDC_VOLUME,
)
_LINK_METRICS = (
    MetricType.ETH_LINK_TOTAL_SUPPLY,
    MetricType.ETH_LINK_TRANSFER_COUNT,
    MetricType.ETH_LINK_UNIQUE_ADDRESSES,
    MetricType.ETH_LINK_VOLUME,
)
_STETH_METRICS = (
    MetricType.ETH_STETH_TOTAL_SUPPLY,
    MetricType.ETH_STETH_TRANSFER_COUNT,
    MetricType.ETH_STETH_UNIQUE_ADDRESSES,
    MetricType.ETH_STETH_VOLUME,
)
_WSTETH_METRICS = (
    MetricType.ETH_WSTETH_TOTAL_SUPPLY,
    MetricType.ETH_WSTETH_TRANSFER_COUNT,
    MetricType.ETH_WSTETH_UNIQUE_ADDRESSES,
    MetricType.ETH_WSTETH_VOLUME,
)
_CBBTC_METRICS = (
    MetricType.ETH_CBBTC_TOTAL_SUPPLY,
    MetricType.ETH_CBBTC_TRANSFER_COUNT,
    MetricType.ETH_CBBTC_UNIQUE_ADDRESSES,
    MetricType.ETH_CBBTC_VOLUME,
)

_TOKEN_METRICS: Mapping[str, tuple[MetricType, ...]] = {
    "USDC": _USDC_METRICS,
    "USDT0": _USDT0_METRICS,
    "WETH": _WETH_METRICS,
    "USDE": _USDE_METRICS,
    "AAVE": _AAVE_METRICS,
    "UNI": _UNI_METRICS,
    "AUSDC": _AUSDC_METRICS,
    "CUSDC": _CUSDC_METRICS,
    "LINK": _LINK_METRICS,
    "STETH": _STETH_METRICS,
    "WSTETH": _WSTETH_METRICS,
    "CBBTC": _CBBTC_METRICS,
}

_TOTAL_SUPPLY_METRICS: frozenset[MetricType] = frozenset(
    {
        MetricType.USDC_TOTAL_SUPPLY,
        MetricType.ETH_WETH_TOTAL_SUPPLY,
        MetricType.ETH_USDE_TOTAL_SUPPLY,
        MetricType.ETH_AAVE_TOTAL_SUPPLY,
        MetricType.ETH_UNI_TOTAL_SUPPLY,
        MetricType.ETH_AUSDC_TOTAL_SUPPLY,
        MetricType.ETH_CUSDC_TOTAL_SUPPLY,
        MetricType.ETH_LINK_TOTAL_SUPPLY,
        MetricType.ETH_STETH_TOTAL_SUPPLY,
        MetricType.ETH_WSTETH_TOTAL_SUPPLY,
        MetricType.ETH_CBBTC_TOTAL_SUPPLY,
    }
)

# Per-EVM-chain JSON-RPC URL resolver. Mirrors the ``_EVM_CHAINS`` dispatch in
# ``metrics/throughput/activities.py`` but without the slot-seconds tuple —
# ``totalSupply()`` is independent of slot timing. Ink and Unichain remain
# single-URL because their settings expose ``ink_url`` / ``unichain_url``
# rather than a multi-provider URL list.
_EVM_SUPPLY_URLS: Mapping[str, Callable[[RPCSettings], list[str]]] = {
    "ETHEREUM": lambda s: s.ethereum_urls,
    "ARBITRUM": lambda s: s.arbitrum_urls,
    "BASE": lambda s: s.base_urls,
    "POLYGON": lambda s: s.polygon_urls,
    "OPTIMISM": lambda s: s.optimism_urls,
    "AVALANCHE_C": lambda s: s.avalanche_c_urls,
    "INK": lambda s: [s.ink_url],
    "UNICHAIN": lambda s: [s.unichain_url],
}


def _ts(dt: datetime) -> str:
    """Format a datetime as a DuneSQL ``TIMESTAMP`` literal (UTC)."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _evm_query(config: _EvmTokenConfig, start: datetime, end: datetime) -> str:
    divisor = 10**config.decimals
    return f"""
SELECT
    COUNT(*)                                                                  AS transfer_count,
    COUNT(DISTINCT "from") + COUNT(DISTINCT "to")                            AS unique_addresses,
    SUM(CASE WHEN "from" = 0x0000000000000000000000000000000000000000
             THEN CAST(amount_raw AS DOUBLE) / {divisor} ELSE 0 END)         AS inflow,
    SUM(CASE WHEN "to"   = 0x0000000000000000000000000000000000000000
             THEN CAST(amount_raw AS DOUBLE) / {divisor} ELSE 0 END)         AS outflow,
    SUM(CASE WHEN "from" != 0x0000000000000000000000000000000000000000
              AND "to"   != 0x0000000000000000000000000000000000000000
             THEN CAST(amount_raw AS DOUBLE) / {divisor} ELSE 0 END)         AS volume
FROM tokens.transfers
WHERE block_time BETWEEN TIMESTAMP '{_ts(start)}' AND TIMESTAMP '{_ts(end)}'
  AND blockchain       = '{config.dune_chain}'
  AND contract_address = {config.contract_address}
"""  # noqa: S608


def _solana_query(config: _SolanaTokenConfig, start: datetime, end: datetime) -> str:
    divisor = 10**config.decimals
    return f"""
SELECT
    COUNT(*)                                                                  AS transfer_count,
    COUNT(DISTINCT from_owner) + COUNT(DISTINCT to_owner)                    AS unique_addresses,
    SUM(CASE WHEN from_owner IS NULL
             THEN amount / {divisor} ELSE 0 END)                             AS inflow,
    SUM(CASE WHEN to_owner   IS NULL
             THEN amount / {divisor} ELSE 0 END)                             AS outflow,
    SUM(CASE WHEN from_owner IS NOT NULL AND to_owner IS NOT NULL
             THEN amount / {divisor} ELSE 0 END)                             AS volume
FROM tokens_solana.transfers
WHERE block_time BETWEEN TIMESTAMP '{_ts(start)}' AND TIMESTAMP '{_ts(end)}'
  AND token_mint_address = '{config.mint_address}'
"""  # noqa: S608


def _row_value(row: Mapping[str, object], key: str, default: float = 0.0) -> float:
    value = row.get(key)
    if value is None:
        return default
    return float(value)  # type: ignore[arg-type]


def _project_row(
    chain: str,
    token: str,
    row: Mapping[str, object] | None,
    total_supply: float,
) -> list[TokenActivityResult]:
    """Project the per-token metric list from the supply value and a flow row.

    ``total_supply`` is sourced on-chain via JSON-RPC. ``row`` is the Dune flow
    row, or ``None`` when the Dune query was unavailable (e.g. an invalid key);
    in that case only the total-supply metric(s) are emitted and the
    Dune-derived flow metrics are skipped, so a Dune outage never blocks supply
    collection.
    """
    flow_available = row is not None
    transfer_count = _row_value(row, "transfer_count") if row is not None else 0.0
    unique_addresses = _row_value(row, "unique_addresses") if row is not None else 0.0
    inflow = _row_value(row, "inflow") if row is not None else 0.0
    outflow = _row_value(row, "outflow") if row is not None else 0.0
    volume = _row_value(row, "volume") if row is not None else 0.0

    metric_values: dict[MetricType, float] = {
        # USDC
        MetricType.USDC_INFLOW: inflow,
        MetricType.USDC_OUTFLOW: outflow,
        MetricType.USDC_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.USDC_TRANSACTION_COUNT: transfer_count,
        MetricType.USDC_TOTAL_SUPPLY: total_supply,
        # USDT0
        MetricType.USDT0_INFLOW: inflow,
        MetricType.USDT0_OUTFLOW: outflow,
        MetricType.USDT0_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.USDT0_TRANSACTION_COUNT: transfer_count,
        MetricType.USDT0_TOTAL_AMOUNT_TRANSFERS: volume,
        # WETH
        MetricType.ETH_WETH_INFLOW: inflow,
        MetricType.ETH_WETH_OUTFLOW: outflow,
        MetricType.ETH_WETH_TOTAL_SUPPLY: total_supply,
        # USDe
        MetricType.ETH_USDE_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_USDE_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_USDE_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_USDE_VOLUME: volume,
        # AAVE
        MetricType.ETH_AAVE_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_AAVE_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_AAVE_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_AAVE_VOLUME: volume,
        # UNI
        MetricType.ETH_UNI_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_UNI_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_UNI_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_UNI_VOLUME: volume,
        # aUSDC (Aave V3)
        MetricType.ETH_AUSDC_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_AUSDC_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_AUSDC_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_AUSDC_VOLUME: volume,
        # cUSDC (Compound V2)
        MetricType.ETH_CUSDC_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_CUSDC_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_CUSDC_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_CUSDC_VOLUME: volume,
        # LINK
        MetricType.ETH_LINK_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_LINK_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_LINK_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_LINK_VOLUME: volume,
        # stETH (Lido rebasing)
        MetricType.ETH_STETH_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_STETH_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_STETH_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_STETH_VOLUME: volume,
        # wstETH (Lido wrapped)
        MetricType.ETH_WSTETH_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_WSTETH_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_WSTETH_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_WSTETH_VOLUME: volume,
        # cbBTC (Coinbase wrapped BTC)
        MetricType.ETH_CBBTC_TOTAL_SUPPLY: total_supply,
        MetricType.ETH_CBBTC_TRANSFER_COUNT: transfer_count,
        MetricType.ETH_CBBTC_UNIQUE_ADDRESSES: unique_addresses,
        MetricType.ETH_CBBTC_VOLUME: volume,
    }

    return [
        TokenActivityResult(
            chain=chain,
            token=token,
            metric_type=metric.value,
            value=metric_values[metric],
        )
        for metric in _TOKEN_METRICS[token]
        if flow_available or metric in _TOTAL_SUPPLY_METRICS
    ]


async def _fetch_total_supply(chain: str, config: _TokenConfig) -> float:
    """Read on-chain ``totalSupply`` via JSON-RPC.

    Replaces the legacy Dune ``mints - burns`` reconstruction, which scanned
    full token transfer history and was the dominant cost driver for the
    metrics workflow. The on-chain call returns the canonical value in a
    single request.
    """
    rpc_settings = get_rpc_settings()
    if isinstance(config, _SolanaTokenConfig):
        return await fetch_solana_total_supply(
            mint_address=config.mint_address,
            decimals=config.decimals,
            urls=rpc_settings.solana_urls,
        )

    urls_resolver = _EVM_SUPPLY_URLS.get(chain)
    if urls_resolver is None:
        msg = f"tokens: no RPC URL resolver configured for chain {chain!r}"
        raise RuntimeError(msg)
    return await fetch_evm_total_supply(
        chain=chain,
        contract_address=config.contract_address,
        decimals=config.decimals,
        urls=urls_resolver(rpc_settings),
    )


@activity.defn
async def fetch_token_activity(
    params: TokenActivityParams,
) -> TokenActivityBatchResult:
    """Fetch all token-activity metrics for a ``(chain, token)`` pair.

    Total supply, when the token emits it, comes from a direct JSON-RPC call
    against the chain — ``eth_call(totalSupply())`` for EVM and
    ``getTokenSupply`` for Solana — and never touches Dune. Window metrics
    (inflow, outflow, transfer count, unique addresses, and volume) come from a
    1-hour Dune SQL query against ``tokens.transfers`` (EVM) or
    ``tokens_solana.transfers`` (Solana).

    Supply and flow are collected independently: if the Dune flow query fails
    (e.g. an invalid key), the activity still persists a supply-only snapshot
    rather than dropping the whole row, so a Dune outage never blocks the
    on-chain supply metric.

    Raises:
        ValueError: if the pair is not registered in :data:`SUPPORTED_PAIRS`.
        RuntimeError: if the on-chain supply call fails for every configured
            RPC URL, or if neither supply nor flow could be collected.
    """
    chain_upper = params.chain.upper()
    token_upper = params.token.upper()
    pair = (chain_upper, token_upper)
    if pair not in SUPPORTED_PAIRS:
        msg = f"tokens: pair {pair!r} not supported"
        raise ValueError(msg)

    config = _TOKEN_CONFIGS[pair]
    metrics = _TOKEN_METRICS[token_upper]
    needs_supply = any(metric in _TOTAL_SUPPLY_METRICS for metric in metrics)
    has_flow_metrics = any(metric not in _TOTAL_SUPPLY_METRICS for metric in metrics)

    # Total supply is read on-chain via JSON-RPC and is independent of Dune.
    # Fetch it first and let a failure propagate so Temporal retries — supply is
    # the metric we most want to keep alive when an upstream is degraded.
    total_supply = 0.0
    if needs_supply:
        total_supply = await _fetch_total_supply(chain_upper, config)

    # Flow metrics (inflow / outflow / volume / transfer count / unique
    # addresses) come from Dune. A Dune failure (e.g. an invalid key) degrades
    # to a supply-only snapshot rather than dropping the whole row.
    rows: list[Mapping[str, object]] | None = None
    if has_flow_metrics:
        end = (datetime.now(UTC) - _DUNE_LAG_BUFFER).replace(second=0, microsecond=0)
        start = end - _DEFAULT_LOOKBACK
        window_sql = (
            _solana_query(config, start, end)
            if isinstance(config, _SolanaTokenConfig)
            else _evm_query(config, start, end)
        )
        try:
            rows = await run_dune_query(window_sql)
        except Exception as exc:  # noqa: BLE001 - degrade to supply-only snapshot
            activity.logger.warning(
                f"tokens: Dune flow query failed for {pair!r}; persisting a "
                f"supply-only snapshot: {exc}"
            )

    flow_row = rows[0] if rows else None
    results = _project_row(chain_upper, token_upper, flow_row, total_supply)
    if not results:
        msg = (
            f"tokens: no metrics collected for {pair!r} "
            "(supply and Dune flow both unavailable)"
        )
        raise RuntimeError(msg)

    return TokenActivityBatchResult(results=results)


@activity.defn
async def store_token_activity(batch: TokenActivityBatchResult) -> None:
    """Persist all metrics from a batch result to ``token_activity``."""
    if not batch.results:
        return
    async with session_factory()() as session:
        session.add_all(
            TokenActivity(
                chain=ChainType(item.chain),
                token=TokenType(item.token),
                metric_type=MetricType(item.metric_type),
                value=Decimal(str(item.value)),
            )
            for item in batch.results
        )
        await session.commit()
