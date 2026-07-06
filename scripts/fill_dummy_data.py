#!/usr/bin/env python3
"""Insert fake but plausible snapshots for every automatic metric model.

Usage:
    uv run python scripts/fill_dummy_data.py

The script is idempotent: each ``automatic`` metric table is truncated first
so re-running the script never produces duplicate rows. Only the rows
seeded here are touched — manual metrics, users, teams, alerts, etc. are
preserved.

Tables populated:
    * finality_ethereum / _evm_l2 / _op_stack / _polygon / _solana
    * throughput
    * time_to_finality
    * decentralization
    * tvl
    * token_activity
    * governance_event
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cert_ra.db.models.decentralization import Decentralization
from cert_ra.db.models.finality import (
    FinalityEthereum,
    FinalityEvmL2,
    FinalityOpStack,
    FinalityPolygon,
    FinalitySolana,
)
from cert_ra.db.models.governance import GovernanceEvent
from cert_ra.db.models.throughput import Throughput
from cert_ra.db.models.time_to_finality import TimeToFinality
from cert_ra.db.models.token_activity import TokenActivity
from cert_ra.db.models.tvl import TVL
from cert_ra.settings.db import get_db_settings
from cert_ra.types import ChainType, MetricType, TokenType

# ---------------------------------------------------------------------------
# Tuning knobs
# ---------------------------------------------------------------------------

N = 50  # number of historical snapshots per (chain, metric) pair
INTERVAL_HOURS = 12

_THROUGHPUT_CHAINS: tuple[ChainType, ...] = (
    ChainType.ARBITRUM,
    ChainType.SOLANA,
    ChainType.INK,
    ChainType.UNICHAIN,
    ChainType.POLYGON,
    ChainType.AVALANCHE_C,
    ChainType.OPTIMISM,
    ChainType.BASE,
)

_TIME_TO_FINALITY_CHAINS: tuple[ChainType, ...] = (
    ChainType.ETHEREUM,
    ChainType.BASE,
    ChainType.INK,
    ChainType.SOLANA,
    ChainType.UNICHAIN,
)

_DECENTRALIZATION_CHAINS: tuple[ChainType, ...] = (
    ChainType.ETHEREUM,
    ChainType.POLYGON,
    ChainType.SOLANA,
    ChainType.AVALANCHE_C,
)

_TVL_CHAINS: tuple[ChainType, ...] = (
    ChainType.ETHEREUM,
    ChainType.ARBITRUM,
    ChainType.BASE,
    ChainType.INK,
    ChainType.UNICHAIN,
    ChainType.POLYGON,
    ChainType.AVALANCHE_C,
    ChainType.OPTIMISM,
    ChainType.SOLANA,
)

# (chain, token, metric_types) — mirrors the worker.py token-activity matrix.
_TOKEN_PAIRS: tuple[tuple[ChainType, TokenType, tuple[MetricType, ...]], ...] = (
    *[
        (
            chain,
            TokenType.USDC,
            (
                MetricType.USDC_INFLOW,
                MetricType.USDC_OUTFLOW,
                MetricType.USDC_UNIQUE_ADDRESSES,
                MetricType.USDC_TRANSACTION_COUNT,
                MetricType.USDC_TOTAL_SUPPLY,
            ),
        )
        for chain in (
            ChainType.ETHEREUM,
            ChainType.ARBITRUM,
            ChainType.BASE,
            ChainType.INK,
            ChainType.UNICHAIN,
            ChainType.POLYGON,
            ChainType.AVALANCHE_C,
            ChainType.OPTIMISM,
            ChainType.SOLANA,
        )
    ],
    *[
        (
            chain,
            TokenType.USDT0,
            (
                MetricType.USDT0_INFLOW,
                MetricType.USDT0_OUTFLOW,
                MetricType.USDT0_UNIQUE_ADDRESSES,
                MetricType.USDT0_TRANSACTION_COUNT,
                MetricType.USDT0_TOTAL_AMOUNT_TRANSFERS,
            ),
        )
        for chain in (
            ChainType.ETHEREUM,
            ChainType.INK,
            ChainType.UNICHAIN,
            ChainType.OPTIMISM,
            ChainType.POLYGON,
        )
    ],
    (
        ChainType.ETHEREUM,
        TokenType.WETH,
        (
            MetricType.ETH_WETH_INFLOW,
            MetricType.ETH_WETH_OUTFLOW,
            MetricType.ETH_WETH_TOTAL_SUPPLY,
        ),
    ),
    (
        ChainType.ETHEREUM,
        TokenType.USDE,
        (
            MetricType.ETH_USDE_TOTAL_SUPPLY,
            MetricType.ETH_USDE_TRANSFER_COUNT,
            MetricType.ETH_USDE_UNIQUE_ADDRESSES,
            MetricType.ETH_USDE_VOLUME,
        ),
    ),
    (
        ChainType.ETHEREUM,
        TokenType.AAVE,
        (
            MetricType.ETH_AAVE_TOTAL_SUPPLY,
            MetricType.ETH_AAVE_TRANSFER_COUNT,
            MetricType.ETH_AAVE_UNIQUE_ADDRESSES,
            MetricType.ETH_AAVE_VOLUME,
        ),
    ),
    (
        ChainType.ETHEREUM,
        TokenType.UNI,
        (
            MetricType.ETH_UNI_TOTAL_SUPPLY,
            MetricType.ETH_UNI_TRANSFER_COUNT,
            MetricType.ETH_UNI_UNIQUE_ADDRESSES,
            MetricType.ETH_UNI_VOLUME,
        ),
    ),
)

_GOVERNANCE_EVENTS: tuple[tuple[ChainType, str], ...] = (
    (ChainType.ETHEREUM, "proposals"),
    (ChainType.ARBITRUM, "proposals"),
    (ChainType.ARBITRUM, "execution"),
    (ChainType.ARBITRUM, "emergency"),
    (ChainType.BASE, "execution"),
    (ChainType.SOLANA, "proposals"),
)


# Tables to truncate before seeding. ``CASCADE`` is unnecessary because none of
# these tables have FK dependants.
_TRUNCATE_TABLES: tuple[str, ...] = (
    "finality_ethereum",
    "finality_evm_l2",
    "finality_op_stack",
    "finality_polygon",
    "finality_solana",
    "throughput",
    "time_to_finality",
    "decentralization",
    "tvl",
    "token_activity",
    "governance_event",
)


def _noise(base: float, pct: float = 0.08) -> float:
    """Uniform ±pct% noise around ``base``."""
    return base * (1 + random.uniform(-pct, pct))  # noqa: S311


def _wave(i: int, amplitude: float, base: float, period: int = N) -> float:
    """Slow sinusoidal variation around ``base``."""
    return base + amplitude * math.sin(2 * math.pi * i / period)


def _finality_rows(now: datetime) -> list[object]:
    rows: list[object] = []
    for i in range(N):
        t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)
        rows.append(
            FinalityEthereum(
                created_at=t,
                head_height=20_000_000 + i * 300,
                finalized_height=20_000_000 + i * 300 - 64,
                safe_height=20_000_000 + i * 300 - 32,
                justified_epoch=625_000 + i * 10,
                finalized_epoch=624_999 + i * 10,
                justified_finalized_gap=random.randint(1, 3),  # noqa: S311
                time_since_finality_advance=_noise(5.0),
                head_to_finalized_time=int(_wave(i, amplitude=80, base=768)),
            )
        )
        rows.append(
            FinalityEvmL2(
                created_at=t,
                chain=ChainType.ARBITRUM,
                latest_height=250_000_000 + i * 40_000,
                safe_height=250_000_000 + i * 40_000 - 10,
                finalized_height=250_000_000 + i * 40_000 - 20,
                latest_to_safe_blocks=random.randint(8, 14),  # noqa: S311
                safe_to_finalized_blocks=random.randint(8, 14),  # noqa: S311
                time_since_last_head=_noise(0.5),
                height_correlation=int(_noise(20, pct=0.15)),
                time_to_hard_finality=int(_wave(i, amplitude=180, base=1200)),
            )
        )
        rows.append(
            FinalityEvmL2(
                created_at=t,
                chain=ChainType.BASE,
                latest_height=30_000_000 + i * 30_000,
                safe_height=30_000_000 + i * 30_000 - 100,
                finalized_height=30_000_000 + i * 30_000 - 200,
                latest_to_safe_blocks=random.randint(88, 112),  # noqa: S311
                safe_to_finalized_blocks=random.randint(88, 112),  # noqa: S311
                time_since_last_head=_noise(0.8),
                height_correlation=None,
                time_to_hard_finality=None,
            )
        )
        rows.append(
            FinalityOpStack(
                created_at=t,
                chain=ChainType.INK,
                unsafe_height=5_000_000 + i * 25_000,
                safe_height=5_000_000 + i * 25_000 - 100,
                finalized_height=5_000_000 + i * 25_000 - 200,
                unsafe_to_safe_blocks=random.randint(88, 115),  # noqa: S311
                safe_to_finalized_blocks=random.randint(88, 115),  # noqa: S311
                time_since_last_unsafe=_noise(1.0),
                height_correlation=int(_noise(200, pct=0.10)),
                time_to_hard_finality=int(_wave(i, amplitude=350, base=3600)),
            )
        )
        rows.append(
            FinalityOpStack(
                created_at=t,
                chain=ChainType.UNICHAIN,
                unsafe_height=3_000_000 + i * 20_000,
                safe_height=3_000_000 + i * 20_000 - 100,
                finalized_height=3_000_000 + i * 20_000 - 200,
                unsafe_to_safe_blocks=random.randint(88, 115),  # noqa: S311
                safe_to_finalized_blocks=random.randint(88, 115),  # noqa: S311
                time_since_last_unsafe=_noise(0.5),
                height_correlation=int(_noise(200, pct=0.10)),
                time_to_hard_finality=int(
                    _wave(i, amplitude=280, base=3600, period=N // 2)
                ),
            )
        )
        rows.append(
            FinalityPolygon(
                created_at=t,
                latest_height=60_000_000 + i * 20_000,
                finalized_height=60_000_000 + i * 20_000 - 100,
                latest_to_finalized_blocks=int(_wave(i, amplitude=15, base=100)),
                time_since_last_head=_noise(1.0),
            )
        )
        rows.append(
            FinalitySolana(
                created_at=t,
                processed_slot=300_000_000 + i * 500_000,
                confirmed_slot=300_000_000 + i * 500_000 - 10,
                finalized_slot=300_000_000 + i * 500_000 - 50,
                confirmed_finalized_gap=int(_wave(i, amplitude=8, base=40)),
                processed_confirmed_gap=random.randint(8, 14),  # noqa: S311
            )
        )
    return rows


def _throughput_rows(now: datetime) -> list[object]:
    rows: list[object] = []
    for chain in _THROUGHPUT_CHAINS:
        for i in range(N):
            t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)
            rows.append(
                Throughput(
                    created_at=t,
                    chain=chain,
                    gas_price=_noise(20.0, pct=0.30),
                    transactions_per_second=_noise(50.0, pct=0.40),
                    blocks_per_second=_noise(0.5, pct=0.20),
                )
            )
    return rows


def _time_to_finality_rows(now: datetime) -> list[object]:
    rows: list[object] = []
    for chain in _TIME_TO_FINALITY_CHAINS:
        base = {
            ChainType.ETHEREUM: 12.0,
            ChainType.BASE: 2.0,
            ChainType.INK: 1.0,
            ChainType.SOLANA: 0.4,
            ChainType.UNICHAIN: 0.25,
        }[chain]
        for i in range(N):
            t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)
            rows.append(
                TimeToFinality(
                    created_at=t,
                    chain=chain,
                    soft_finality_seconds=_noise(base, pct=0.15),
                )
            )
    return rows


def _decentralization_rows(now: datetime) -> list[object]:
    rows: list[object] = []
    for chain in _DECENTRALIZATION_CHAINS:
        nodes_base = {
            ChainType.ETHEREUM: 850_000,
            ChainType.POLYGON: 100,
            ChainType.SOLANA: 1_500,
            ChainType.AVALANCHE_C: 1_300,
        }[chain]
        stake_base = {
            ChainType.ETHEREUM: 30_000_000.0,
            ChainType.POLYGON: 4_000_000_000.0,
            ChainType.SOLANA: 400_000_000.0,
            ChainType.AVALANCHE_C: 300_000_000.0,
        }[chain]
        for i in range(N):
            t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)
            rows.append(
                Decentralization(
                    created_at=t,
                    chain=chain,
                    total_amount_of_stakes=_noise(stake_base, pct=0.05),
                    number_of_nodes=int(_noise(nodes_base, pct=0.05)),
                    nakamoto_liveness_coefficient=random.randint(2, 8),  # noqa: S311
                    nakamoto_safety_coefficient=random.randint(4, 14),  # noqa: S311
                    hhi=_noise(0.05, pct=0.30),
                    shapley_top_value=_noise(0.20, pct=0.10),
                    shapley_second_value=_noise(0.15, pct=0.10),
                    shapley_third_value=_noise(0.10, pct=0.10),
                    renyi_entropy_alpha_0=_noise(8.0, pct=0.05),
                    renyi_entropy_alpha_1=_noise(7.5, pct=0.05),
                    renyi_entropy_alpha_2=_noise(7.0, pct=0.05),
                    renyi_entropy_alpha_inf=_noise(2.5, pct=0.10),
                )
            )
    return rows


def _tvl_rows(now: datetime) -> list[object]:
    rows: list[object] = []
    for chain in _TVL_CHAINS:
        base = {
            ChainType.ETHEREUM: 70_000_000_000.0,
            ChainType.ARBITRUM: 12_000_000_000.0,
            ChainType.BASE: 8_000_000_000.0,
            ChainType.INK: 200_000_000.0,
            ChainType.UNICHAIN: 600_000_000.0,
            ChainType.POLYGON: 1_500_000_000.0,
            ChainType.AVALANCHE_C: 1_200_000_000.0,
            ChainType.OPTIMISM: 1_000_000_000.0,
            ChainType.SOLANA: 9_500_000_000.0,
        }[chain]
        for i in range(N):
            t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)
            rows.append(
                TVL(
                    created_at=t,
                    chain=chain,
                    value=Decimal(str(round(_noise(base, pct=0.05), 2))),
                )
            )
    return rows


def _token_activity_rows(now: datetime) -> list[object]:
    rows: list[object] = []
    for chain, token, metric_types in _TOKEN_PAIRS:
        for metric in metric_types:
            base = (
                100_000_000.0
                if "TOTAL_SUPPLY" in metric.value or "TVL" in metric.value
                else 5_000.0
                if "UNIQUE" in metric.value
                else 50_000.0
                if "COUNT" in metric.value
                else 1_500_000.0
            )
            for i in range(N):
                t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)
                rows.append(
                    TokenActivity(
                        created_at=t,
                        chain=chain,
                        token=token,
                        metric_type=metric,
                        value=Decimal(str(round(_noise(base, pct=0.20), 2))),
                    )
                )
    return rows


def _governance_rows(now: datetime) -> list[object]:
    rows: list[object] = []
    for chain, event_type in _GOVERNANCE_EVENTS:
        for i in range(N):
            t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)
            rows.append(
                GovernanceEvent(
                    created_at=t,
                    chain=chain,
                    event_type=event_type,
                    count=random.randint(0, 5),  # noqa: S311
                )
            )
    return rows


async def _truncate(session: AsyncSession) -> None:
    """Truncate every automatic-metric table so re-runs stay idempotent."""
    table_list = ", ".join(_TRUNCATE_TABLES)
    await session.execute(text(f"TRUNCATE TABLE {table_list} RESTART IDENTITY"))
    await session.commit()


async def main() -> None:
    """Insert dummy rows for every automatic metric table."""
    random.seed(42)
    settings = get_db_settings()
    engine = create_async_engine(settings.url, echo=False)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    now = datetime.now(UTC)

    rows: list[object] = []
    rows.extend(_finality_rows(now))
    rows.extend(_throughput_rows(now))
    rows.extend(_time_to_finality_rows(now))
    rows.extend(_decentralization_rows(now))
    rows.extend(_tvl_rows(now))
    rows.extend(_token_activity_rows(now))
    rows.extend(_governance_rows(now))

    async with session_factory() as session:
        await _truncate(session)
        session.add_all(rows)
        await session.commit()

    await engine.dispose()
    print(f"Seeded {len(rows)} dummy rows across all automatic metric tables.")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
