# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Value-source registry: per-``AlertTargetKind`` adapters that read latest + history.

The evaluator pipeline is target-agnostic. It hands each rule's ``target_kind``
+ ``target_config`` to this module, which dispatches to one of the registered
:class:`ValueSource` implementations and returns a uniform :class:`MetricSnapshot`
(latest) or :class:`HistoricalSeries` (range).

Adding a new ``AlertTargetKind`` means:

1. Add a new ``CamelizedBaseStruct`` to ``cert_ra.api.domain.alerts.targets``.
2. Implement a :class:`ValueSource` here.
3. Register it in ``_SOURCES``.

That's the only place outside the model layer that needs to understand the new
shape — workflows, activities, and the evaluator do not change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import desc, select

from cert_ra.alerts.schemas import HistoricalSeries, MetricSnapshot
from cert_ra.api.domain.alerts.targets import (
    MarketAnchorTargetConfig,
    MarketControlTargetConfig,
    MarketPdTargetConfig,
    MetricTargetConfig,
    TargetConfig,
)
from cert_ra.types import (
    AlertTargetKind,
    ChainType,
    MarketSnapshotKind,
    MetricType,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = (
    "MarketAnchorValueSource",
    "MarketControlValueSource",
    "MarketPdValueSource",
    "MetricSource",
    "MetricValueSource",
    "ValueSource",
    "lookup_metric_source",
    "lookup_value_source",
)


# ---------------------------------------------------------------------------
# Metric-source mapping (used by MetricValueSource)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricSource:
    """Where to read the latest value for a given ``MetricType``.

    ``chain_filter`` is the ``ChainType`` to filter by, when the source table
    is multi-chain (e.g. ``finality_evm_l2`` rows differ by ``chain``). For
    single-chain tables (``finality_ethereum``) it is ``None``.
    """

    table: str
    column: str
    chain_filter: ChainType | None = None


_METRIC_REGISTRY: dict[MetricType, MetricSource] = {
    # Throughput — multi-chain
    MetricType.GAS_PRICE: MetricSource("throughput", "gas_price"),
    MetricType.TRANSACTIONS_PER_SECOND: MetricSource(
        "throughput", "transactions_per_second"
    ),
    MetricType.BLOCKS_PER_SECOND: MetricSource("throughput", "blocks_per_second"),
    # Soft time-to-finality — multi-chain
    MetricType.TIME_TO_FINALITY_SOFT: MetricSource(
        "time_to_finality", "soft_finality_seconds"
    ),
    # Per-chain finality
    MetricType.ETH_FINALITY: MetricSource(
        "finality_ethereum", "time_since_finality_advance"
    ),
    MetricType.SOL_FINALITY: MetricSource("finality_solana", "confirmed_finalized_gap"),
    MetricType.POLYGON_FINALITY: MetricSource(
        "finality_polygon", "time_since_last_head"
    ),
    MetricType.ARB_FINALITY: MetricSource(
        "finality_evm_l2", "time_since_last_head", ChainType.ARBITRUM
    ),
    MetricType.BASE_FINALITY: MetricSource(
        "finality_evm_l2", "time_since_last_head", ChainType.BASE
    ),
    MetricType.OPTIMISM_FINALITY: MetricSource(
        "finality_evm_l2", "time_since_last_head", ChainType.OPTIMISM
    ),
    MetricType.INK_FINALITY: MetricSource(
        "finality_op_stack", "time_since_last_unsafe", ChainType.INK
    ),
    MetricType.UNICHAIN_FINALITY: MetricSource(
        "finality_op_stack", "time_since_last_unsafe", ChainType.UNICHAIN
    ),
    # Decentralization
    MetricType.NAKAMOTO_LIVENESS_COEFFICIENT: MetricSource(
        "decentralization", "nakamoto_liveness_coefficient"
    ),
    MetricType.NAKAMOTO_SAFETY_COEFFICIENT: MetricSource(
        "decentralization", "nakamoto_safety_coefficient"
    ),
    MetricType.HHI: MetricSource("decentralization", "hhi"),
    MetricType.NUMBER_OF_NODES: MetricSource("decentralization", "number_of_nodes"),
    MetricType.TOTAL_AMOUNT_OF_STAKES: MetricSource(
        "decentralization", "total_amount_of_stakes"
    ),
}


_METRIC_TABLE_MODELS: dict[str, Any] = {}
"""Lazy cache of ``table name -> SQLAlchemy model``.

Populated on first use to avoid pulling every finality / decentralization model
at import time, which would create import-order cycles with ``cert_ra.db.models``
during worker startup.
"""


def _metric_table_models() -> dict[str, Any]:
    if _METRIC_TABLE_MODELS:
        return _METRIC_TABLE_MODELS
    # Import inside the function so the alerts package can be imported before
    # all model modules have settled (worker startup ordering).
    from cert_ra.db.models import (
        Decentralization,
        FinalityEthereum,
        FinalityEvmL2,
        FinalityOpStack,
        FinalityPolygon,
        FinalitySolana,
        Throughput,
        TimeToFinality,
    )

    _METRIC_TABLE_MODELS.update(
        {
            "throughput": Throughput,
            "time_to_finality": TimeToFinality,
            "finality_ethereum": FinalityEthereum,
            "finality_solana": FinalitySolana,
            "finality_polygon": FinalityPolygon,
            "finality_evm_l2": FinalityEvmL2,
            "finality_op_stack": FinalityOpStack,
            "decentralization": Decentralization,
        }
    )
    return _METRIC_TABLE_MODELS


# ---------------------------------------------------------------------------
# ValueSource protocol + implementations
# ---------------------------------------------------------------------------


class ValueSource(Protocol):
    """One adapter per :class:`AlertTargetKind`.

    Implementations open no sessions of their own — callers (the alerts
    activities) provide a session so the per-tick query budget is shared and
    explicit. Returning ``None`` from ``load_latest`` signals "no value to
    evaluate" and the evaluator emits an ERROR history row.
    """

    async def load_latest(
        self,
        session: AsyncSession,
        config: TargetConfig,
    ) -> MetricSnapshot | None:
        """Return the most recently committed value, or ``None`` if no data exists."""
        ...

    async def load_series(
        self,
        session: AsyncSession,
        config: TargetConfig,
        lookback_seconds: int,
    ) -> HistoricalSeries:
        """Return all samples observed within the lookback window (oldest → newest).

        An empty series is valid and the evaluator handles it as an error case
        (rate-of-change ⇒ no baseline; stddev ⇒ insufficient history).
        """
        ...


# ---------------------------------------------------------------------------
# Metric value source
# ---------------------------------------------------------------------------


class MetricValueSource:
    """Reads from the blockchain-metric snapshot tables via ``_METRIC_REGISTRY``."""

    async def load_latest(
        self,
        session: AsyncSession,
        config: TargetConfig,
    ) -> MetricSnapshot | None:
        assert isinstance(config, MetricTargetConfig)
        source = _METRIC_REGISTRY.get(MetricType(config.metric_type))
        if source is None:
            return None
        model = _metric_table_models().get(source.table)
        if model is None:
            return None
        stmt = select(model)
        chain = source.chain_filter or (
            ChainType(config.chain) if config.chain is not None else None
        )
        if chain is not None and hasattr(model, "chain"):
            stmt = stmt.where(model.chain == chain)
        stmt = stmt.order_by(desc(model.created_at)).limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return MetricSnapshot(
            value=float(getattr(row, source.column)),
            observed_at=row.created_at,
            snapshot_id=row.id,
            snapshot_table=source.table,
        )

    async def load_series(
        self,
        session: AsyncSession,
        config: TargetConfig,
        lookback_seconds: int,
    ) -> HistoricalSeries:
        assert isinstance(config, MetricTargetConfig)
        source = _METRIC_REGISTRY.get(MetricType(config.metric_type))
        if source is None:
            return HistoricalSeries(samples=[])
        model = _metric_table_models().get(source.table)
        if model is None:
            return HistoricalSeries(samples=[])
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=lookback_seconds)
        stmt = select(model).where(model.created_at >= cutoff)
        chain = source.chain_filter or (
            ChainType(config.chain) if config.chain is not None else None
        )
        if chain is not None and hasattr(model, "chain"):
            stmt = stmt.where(model.chain == chain)
        stmt = stmt.order_by(model.created_at)
        rows = (await session.execute(stmt)).scalars().all()
        samples = [(row.created_at, float(getattr(row, source.column))) for row in rows]
        return HistoricalSeries(samples=samples)


# ---------------------------------------------------------------------------
# Market value sources
# ---------------------------------------------------------------------------


class MarketPdValueSource:
    """Reads ``market_score.final_pd`` filtered by the market triple."""

    async def load_latest(
        self,
        session: AsyncSession,
        config: TargetConfig,
    ) -> MetricSnapshot | None:
        from cert_ra.db.models import MarketScore

        assert isinstance(config, MarketPdTargetConfig)
        stmt = (
            select(MarketScore)
            .where(
                MarketScore.market_config_id == config.market_config_id,
                MarketScore.chain_id == config.chain_id,
                MarketScore.market_id_hex == config.market_id_hex,
            )
            .order_by(desc(MarketScore.created_at))
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return MetricSnapshot(
            value=float(row.final_pd),
            observed_at=row.created_at,
            snapshot_id=row.id,
            snapshot_table="market_score",
        )

    async def load_series(
        self,
        session: AsyncSession,
        config: TargetConfig,
        lookback_seconds: int,
    ) -> HistoricalSeries:
        from cert_ra.db.models import MarketScore

        assert isinstance(config, MarketPdTargetConfig)
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=lookback_seconds)
        stmt = (
            select(MarketScore)
            .where(
                MarketScore.market_config_id == config.market_config_id,
                MarketScore.chain_id == config.chain_id,
                MarketScore.market_id_hex == config.market_id_hex,
                MarketScore.created_at >= cutoff,
            )
            .order_by(MarketScore.created_at)
        )
        rows = (await session.execute(stmt)).scalars().all()
        samples = [(row.created_at, float(row.final_pd)) for row in rows]
        return HistoricalSeries(samples=samples)


class _MarketScoreJsonValueSource:
    """Shared helper for the per-anchor / per-control value sources.

    Subclasses set ``_block_key`` (e.g. ``"anchors"`` or ``"controlModifiers"``)
    and ``_value_key`` (``"pd"`` / ``"multiplier"``). ``load_latest`` and
    ``load_series`` walk SCORE-kind ``automated_market_snapshot`` rows filtered
    by the market triple and pull the targeted scalar out of the JSONB blob.
    """

    _block_key: str = ""
    _value_key: str = ""

    async def load_latest(
        self,
        session: AsyncSession,
        config: TargetConfig,
    ) -> MetricSnapshot | None:
        from cert_ra.db.models import AutomatedMarketSnapshot

        assert isinstance(
            config, (MarketAnchorTargetConfig, MarketControlTargetConfig)
        )
        stmt = (
            select(AutomatedMarketSnapshot)
            .where(
                AutomatedMarketSnapshot.market_config_id == config.market_config_id,
                AutomatedMarketSnapshot.chain_id == config.chain_id,
                AutomatedMarketSnapshot.market_id_hex == config.market_id_hex,
                AutomatedMarketSnapshot.kind == MarketSnapshotKind.SCORE,
            )
            .order_by(desc(AutomatedMarketSnapshot.created_at))
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        value = self._extract(row.score, config.sub_category)
        if value is None:
            return None
        return MetricSnapshot(
            value=value,
            observed_at=row.created_at,
            snapshot_id=row.id,
            snapshot_table="automated_market_snapshot",
        )

    async def load_series(
        self,
        session: AsyncSession,
        config: TargetConfig,
        lookback_seconds: int,
    ) -> HistoricalSeries:
        from cert_ra.db.models import AutomatedMarketSnapshot

        assert isinstance(
            config, (MarketAnchorTargetConfig, MarketControlTargetConfig)
        )
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=lookback_seconds)
        stmt = (
            select(AutomatedMarketSnapshot)
            .where(
                AutomatedMarketSnapshot.market_config_id == config.market_config_id,
                AutomatedMarketSnapshot.chain_id == config.chain_id,
                AutomatedMarketSnapshot.market_id_hex == config.market_id_hex,
                AutomatedMarketSnapshot.kind == MarketSnapshotKind.SCORE,
                AutomatedMarketSnapshot.created_at >= cutoff,
            )
            .order_by(AutomatedMarketSnapshot.created_at)
        )
        rows = (await session.execute(stmt)).scalars().all()
        samples: list[tuple[datetime, float]] = []
        for row in rows:
            value = self._extract(row.score, config.sub_category)
            if value is None:
                # Snapshot exists but doesn't carry this sub_category; skip
                # rather than synthesize a zero — the LLM scorer can drop a
                # key when it has nothing to report.
                continue
            samples.append((row.created_at, value))
        return HistoricalSeries(samples=samples)

    def _extract(self, score: dict | None, sub_category: str) -> float | None:
        """Pull the targeted numeric out of the scorer JSONB.

        Returns ``None`` when any expected key is missing or the value is
        non-numeric — the caller treats this as "no observation" for this
        snapshot.
        """
        if not score:
            return None
        block = score.get(self._block_key)
        if not isinstance(block, dict):
            return None
        entry = block.get(sub_category)
        if not isinstance(entry, dict):
            return None
        raw = entry.get(self._value_key)
        if isinstance(raw, bool):
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        return None


class MarketAnchorValueSource(_MarketScoreJsonValueSource):
    """Reads ``score['anchors'][sub_category]['pd']``."""

    _block_key = "anchors"
    _value_key = "pd"


class MarketControlValueSource(_MarketScoreJsonValueSource):
    """Reads ``score['controlModifiers'][sub_category]['multiplier']``."""

    _block_key = "controlModifiers"
    _value_key = "multiplier"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


_SOURCES: dict[AlertTargetKind, ValueSource] = {
    AlertTargetKind.METRIC: MetricValueSource(),
    AlertTargetKind.MARKET_PD: MarketPdValueSource(),
    AlertTargetKind.MARKET_ANCHOR: MarketAnchorValueSource(),
    AlertTargetKind.MARKET_CONTROL: MarketControlValueSource(),
}


def lookup_metric_source(metric_type: MetricType) -> MetricSource | None:
    """Return the registry entry for a ``MetricType``, or ``None`` if unsupported.

    Kept as a top-level helper so callers outside the alerts package — most
    notably the favorites resolver — can introspect the source mapping without
    needing to know about the broader ``ValueSource`` registry.
    """
    return _METRIC_REGISTRY.get(metric_type)


def lookup_value_source(kind: AlertTargetKind) -> ValueSource:
    """Return the registered value source for ``kind``.

    Raises:
        KeyError: When the kind has no registered source. Should never happen
            in production — every enum value has an entry — but defensive in
            case a new variant is added without a matching adapter.
    """
    return _SOURCES[kind]
