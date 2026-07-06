#!/usr/bin/env python3
"""Insert dummy finality snapshots for development / demo purposes.

Usage:
    uv run python scripts/seed_finality.py
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from cert_ra.db.models.finality import (
    FinalityEthereum,
    FinalityEvmL2,
    FinalityOpStack,
    FinalityPolygon,
    FinalitySolana,
)
from cert_ra.settings.db import get_db_settings
from cert_ra.types import ChainType

# 50 snapshots, one every ~12 hours → ~25 days of history
N = 50
INTERVAL_HOURS = 12


def noise(base: float, pct: float = 0.08) -> float:
    """Uniform ±pct% noise around base."""
    return base * (1 + random.uniform(-pct, pct))  # noqa: S311


def wave(i: int, amplitude: float, base: float, period: int = N) -> float:
    """Slow sinusoidal variation around base."""
    return base + amplitude * math.sin(2 * math.pi * i / period)


async def main() -> None:
    """Add test data for finality snapshots."""
    random.seed(42)
    settings = get_db_settings()
    engine = create_async_engine(settings.url, echo=False)
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    now = datetime.now(UTC)
    rows: list[object] = []

    for i in range(N):
        t = now - timedelta(hours=(N - 1 - i) * INTERVAL_HOURS)

        # ── Ethereum ─────────────────────────────────────────────────────────
        rows.append(
            FinalityEthereum(
                created_at=t,
                head_height=20_000_000 + i * 300,
                finalized_height=20_000_000 + i * 300 - 64,
                safe_height=20_000_000 + i * 300 - 32,
                justified_epoch=625_000 + i * 10,
                finalized_epoch=624_999 + i * 10,
                justified_finalized_gap=random.randint(1, 3),  # noqa: S311
                time_since_finality_advance=noise(5.0),
                head_to_finalized_time=int(wave(i, amplitude=80, base=768)),
            )
        )

        # ── Arbitrum ──────────────────────────────────────────────────────────
        rows.append(
            FinalityEvmL2(
                created_at=t,
                chain=ChainType.ARBITRUM,
                latest_height=250_000_000 + i * 40_000,
                safe_height=250_000_000 + i * 40_000 - 10,
                finalized_height=250_000_000 + i * 40_000 - 20,
                latest_to_safe_blocks=random.randint(8, 14),  # noqa: S311
                safe_to_finalized_blocks=random.randint(8, 14),  # noqa: S311
                time_since_last_head=noise(0.5),
                height_correlation=int(noise(20, pct=0.15)),
                time_to_hard_finality=int(wave(i, amplitude=180, base=1200)),
            )
        )

        # ── Base ──────────────────────────────────────────────────────────────
        rows.append(
            FinalityEvmL2(
                created_at=t,
                chain=ChainType.BASE,
                latest_height=30_000_000 + i * 30_000,
                safe_height=30_000_000 + i * 30_000 - 100,
                finalized_height=30_000_000 + i * 30_000 - 200,
                latest_to_safe_blocks=random.randint(88, 112),  # noqa: S311
                safe_to_finalized_blocks=random.randint(88, 112),  # noqa: S311
                time_since_last_head=noise(0.8),
                height_correlation=None,
                time_to_hard_finality=None,
            )
        )

        # ── Ink ───────────────────────────────────────────────────────────────
        rows.append(
            FinalityOpStack(
                created_at=t,
                chain=ChainType.INK,
                unsafe_height=5_000_000 + i * 25_000,
                safe_height=5_000_000 + i * 25_000 - 100,
                finalized_height=5_000_000 + i * 25_000 - 200,
                unsafe_to_safe_blocks=random.randint(88, 115),  # noqa: S311
                safe_to_finalized_blocks=random.randint(88, 115),  # noqa: S311
                time_since_last_unsafe=noise(1.0),
                height_correlation=int(noise(200, pct=0.10)),
                time_to_hard_finality=int(wave(i, amplitude=350, base=3600)),
            )
        )

        # ── Unichain ──────────────────────────────────────────────────────────
        rows.append(
            FinalityOpStack(
                created_at=t,
                chain=ChainType.UNICHAIN,
                unsafe_height=3_000_000 + i * 20_000,
                safe_height=3_000_000 + i * 20_000 - 100,
                finalized_height=3_000_000 + i * 20_000 - 200,
                unsafe_to_safe_blocks=random.randint(88, 115),  # noqa: S311
                safe_to_finalized_blocks=random.randint(88, 115),  # noqa: S311
                time_since_last_unsafe=noise(0.5),
                height_correlation=int(noise(200, pct=0.10)),
                time_to_hard_finality=int(
                    wave(i, amplitude=280, base=3600, period=N // 2)
                ),
            )
        )

        # ── Polygon ───────────────────────────────────────────────────────────
        rows.append(
            FinalityPolygon(
                created_at=t,
                latest_height=60_000_000 + i * 20_000,
                finalized_height=60_000_000 + i * 20_000 - 100,
                latest_to_finalized_blocks=int(wave(i, amplitude=15, base=100)),
                time_since_last_head=noise(1.0),
            )
        )

        # ── Solana ────────────────────────────────────────────────────────────
        rows.append(
            FinalitySolana(
                created_at=t,
                processed_slot=300_000_000 + i * 500_000,
                confirmed_slot=300_000_000 + i * 500_000 - 10,
                finalized_slot=300_000_000 + i * 500_000 - 50,
                confirmed_finalized_gap=int(wave(i, amplitude=8, base=40)),
                processed_confirmed_gap=random.randint(8, 14),  # noqa: S311
            )
        )

    async with session_factory() as session:
        session.add_all(rows)
        await session.commit()

    await engine.dispose()
    print(f"Seeded {len(rows)} rows ({N} time points * 7 chains).")  # noqa: T201


asyncio.run(main())
