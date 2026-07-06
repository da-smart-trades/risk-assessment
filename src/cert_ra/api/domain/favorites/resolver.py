# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Resolve a list of favorites into dashboard-ready cards.

Auto-metric values are looked up through the alerts ``_metric_sources``
registry; manual PROTOCOL_SCORE / TOKEN_SCORE values are read from the
summary row's ``ManualMetric.value`` (falling back to ``risk_score``).

Per-chain finality favorites additionally render a secondary metric and a
chain-specific explanation tooltip — see ``_FINALITY_DETAIL``. Per-chain
throughput favorites (``TRANSACTIONS_PER_SECOND``) render the gas price as a
chain-specific secondary value — see ``_THROUGHPUT_GAS``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal  # noqa: TC003
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import desc, select

from cert_ra.alerts._value_sources import lookup_metric_source
from cert_ra.api.domain.favorites.schemas import ResolvedFavorite
from cert_ra.api.domain.markets.team_pd import compute_team_market_final_pd
from cert_ra.db.models import (
    Decentralization,
    FinalityEthereum,
    FinalityEvmL2,
    FinalityOpStack,
    FinalityPolygon,
    FinalitySolana,
    ManualMetric,
    MarketConfig,
    MarketScore,
    Throughput,
    TimeToFinality,
)
from cert_ra.types import ChainType, MetricType

_MarketKey = tuple[UUID, int, str]
"""Composite key for one specific market: (market_config_id, chain_id, hex)."""

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.db.models import UserFavoriteMetric

__all__ = ("resolve_favorites",)


_TABLE_MODELS: dict[str, type[Any]] = {
    "throughput": Throughput,
    "time_to_finality": TimeToFinality,
    "finality_ethereum": FinalityEthereum,
    "finality_solana": FinalitySolana,
    "finality_polygon": FinalityPolygon,
    "finality_evm_l2": FinalityEvmL2,
    "finality_op_stack": FinalityOpStack,
    "decentralization": Decentralization,
}


@dataclass(frozen=True)
class _FinalityDetail:
    """Dashboard rendering hints for a per-chain finality favorite.

    The dashboard card shows ``primary`` as the headline value and ``secondary``
    as a complementary one read from the same snapshot row, with ``description``
    surfaced through a hover tooltip.
    """

    primary_column: str
    primary_label: str
    primary_unit: str
    secondary_column: str
    secondary_label: str
    secondary_unit: str
    description: str


_L2_DESCRIPTION = (
    "L2 blocks are 'finalized' when the L1 batch posting reaches L1 Casper "
    "finality (~13 min). The safe → finalized depth (primary) is gated by L1 "
    "finalization, so persistent growth means the L1 finality path is stalled. "
    "Time to hard finality (secondary) expresses the same condition in "
    "seconds — a direct wall-clock measure of how long an L2 'safe' block "
    "waits before it is finalized via the L1 bridge; under healthy operation "
    "it tracks L1 Casper finality."
)
_OP_STACK_DESCRIPTION = (
    "OP-stack chain. Finality is L1-gated through output root finalization on "
    "Ethereum, so the safe → finalized depth (primary) is the direct signal: "
    "persistent growth means L1 finality (and therefore L2 finality) has "
    "stalled. Time to hard finality (secondary) is the seconds-based "
    "companion: the wall-clock delay between L2 safe inclusion and L1 output "
    "root finalization, which under healthy operation tracks L1 Casper "
    "finality (~13 min)."
)


_FINALITY_DETAIL: dict[MetricType, _FinalityDetail] = {
    MetricType.ETH_FINALITY: _FinalityDetail(
        primary_column="justified_finalized_gap",
        primary_label="Justified → finalized gap",
        primary_unit="epochs",
        secondary_column="time_since_finality_advance",
        secondary_label="Since last finality advance",
        secondary_unit="s",
        description=(
            "Under Casper FFG, finalized = justified - 1 in healthy "
            "operation, so the justified → finalized gap (primary) is 1; "
            "≥2 epochs is the on-chain definition of a non-finalizing "
            "state. Seconds since the last finality advance (secondary) "
            "is the wall-clock view of the same condition — healthy is "
            "below ~15 min (a new finalized epoch lands every ~12.8 min). "
            "A value well past that confirms the chain has stopped "
            "finalizing rather than briefly lagging."
        ),
    ),
    MetricType.SOL_FINALITY: _FinalityDetail(
        primary_column="confirmed_finalized_gap",
        primary_label="Confirmed → finalized gap",
        primary_unit="slots",
        secondary_column="processed_confirmed_gap",
        secondary_label="Processed → confirmed gap",
        secondary_unit="slots",
        description=(
            "Solana finalizes when ≥2/3 stake roots a slot. The "
            "confirmed → finalized gap (primary) is normally ~32 slots; "
            "persistent growth means supermajority rooting has stalled. "
            "The processed → confirmed gap (secondary) is the upstream "
            "stage of the same pipeline — a growing value means vote "
            "aggregation is lagging before slots even reach the confirmed "
            "level, often the earliest indicator that supermajority "
            "finalization is about to break."
        ),
    ),
    MetricType.POLYGON_FINALITY: _FinalityDetail(
        primary_column="latest_to_finalized_blocks",
        primary_label="Latest → finalized depth",
        primary_unit="blocks",
        secondary_column="time_since_last_head",
        secondary_label="Since last head",
        secondary_unit="s",
        description=(
            "Polygon PoS finalizes via Heimdall checkpoints submitted to "
            "Ethereum. The latest → finalized depth (primary) grows when "
            "checkpoints aren't being submitted or verified on L1 — the "
            "canonical 'finality is stuck' symptom. Seconds since last "
            "head (secondary) pairs this with a block-production "
            "liveness check: if Bor itself isn't producing new heads, "
            "finalization can't progress either, regardless of checkpoint "
            "health on the Heimdall/L1 side."
        ),
    ),
    MetricType.ARB_FINALITY: _FinalityDetail(
        primary_column="safe_to_finalized_blocks",
        primary_label="Safe → finalized depth",
        primary_unit="blocks",
        secondary_column="time_to_hard_finality",
        secondary_label="Time to hard finality",
        secondary_unit="s",
        description=_L2_DESCRIPTION,
    ),
    MetricType.BASE_FINALITY: _FinalityDetail(
        primary_column="safe_to_finalized_blocks",
        primary_label="Safe → finalized depth",
        primary_unit="blocks",
        secondary_column="time_to_hard_finality",
        secondary_label="Time to hard finality",
        secondary_unit="s",
        description=_L2_DESCRIPTION,
    ),
    MetricType.OPTIMISM_FINALITY: _FinalityDetail(
        primary_column="safe_to_finalized_blocks",
        primary_label="Safe → finalized depth",
        primary_unit="blocks",
        secondary_column="time_to_hard_finality",
        secondary_label="Time to hard finality",
        secondary_unit="s",
        description=_L2_DESCRIPTION,
    ),
    MetricType.INK_FINALITY: _FinalityDetail(
        primary_column="safe_to_finalized_blocks",
        primary_label="Safe → finalized depth",
        primary_unit="blocks",
        secondary_column="time_to_hard_finality",
        secondary_label="Time to hard finality",
        secondary_unit="s",
        description=_OP_STACK_DESCRIPTION,
    ),
    MetricType.UNICHAIN_FINALITY: _FinalityDetail(
        primary_column="safe_to_finalized_blocks",
        primary_label="Safe → finalized depth",
        primary_unit="blocks",
        secondary_column="time_to_hard_finality",
        secondary_label="Time to hard finality",
        secondary_unit="s",
        description=_OP_STACK_DESCRIPTION,
    ),
}


@dataclass(frozen=True)
class _ThroughputGas:
    """How to render a chain's throughput gas-price column as a sub-value.

    ``scale`` divides the stored raw value before formatting: EVM chains
    persist the effective gas price in wei but display it in gwei, so they
    use ``scale=1e9``; chains whose value is already in its display unit use
    the default ``scale=1.0``.
    """

    label: str
    unit: str
    scale: float = 1.0


# Gas-price rendering per chain for the ``TRANSACTIONS_PER_SECOND`` favorite.
# EVM chains store the effective gas price (base + priority fee) in wei and
# are shown in gwei. Solana's Dune ``avg(fee)`` is already in lamports. Canton
# has no gas concept — it reuses the gas-price column for the amulet price
# (USD per Canton Coin), so the sub-value is relabelled accordingly.
_EVM_GAS = _ThroughputGas(label="Gas price", unit="gwei", scale=1e9)
_THROUGHPUT_GAS: dict[ChainType, _ThroughputGas] = {
    ChainType.ETHEREUM: _EVM_GAS,
    ChainType.ARBITRUM: _EVM_GAS,
    ChainType.BASE: _EVM_GAS,
    ChainType.INK: _EVM_GAS,
    ChainType.UNICHAIN: _EVM_GAS,
    ChainType.POLYGON: _EVM_GAS,
    ChainType.AVALANCHE_C: _EVM_GAS,
    ChainType.OPTIMISM: _EVM_GAS,
    ChainType.SOLANA: _ThroughputGas(label="Gas price", unit="lamports"),
    ChainType.CANTON: _ThroughputGas(label="Amulet price", unit="USD / CC"),
}


async def resolve_favorites(
    session: AsyncSession,
    favorites: Iterable[UserFavoriteMetric],
    *,
    team_id: UUID | None = None,
) -> list[ResolvedFavorite]:
    """Materialise favorites into dashboard cards (label + value + link).

    When ``team_id`` is set, market cards show the **team-weighted** PD
    (recomputed against the team's weighting profile), matching the market
    detail page; otherwise they show the stored global ``MarketScore``.

    Manual favorites are batched into a single ``IN`` query; auto favorites are
    resolved one at a time because each may target a different snapshot table.
    The latter is fine in practice — a user typically pins a handful, not
    hundreds.
    """
    favorites = list(favorites)
    manual_ids = [
        f.manual_metric_id for f in favorites if f.manual_metric_id is not None
    ]
    manual_lookup = await _load_manual_metrics(session, manual_ids)

    market_keys: list[_MarketKey] = [
        (f.market_config_id, f.favorite_chain_id, f.favorite_market_id_hex)
        for f in favorites
        if f.market_config_id is not None
        and f.favorite_chain_id is not None
        and f.favorite_market_id_hex is not None
    ]
    protocol_lookup = await _load_protocols(session, [key[0] for key in market_keys])
    score_lookup = await _load_latest_scores_by_market(session, market_keys)

    resolved: list[ResolvedFavorite] = []
    for fav in favorites:
        if fav.manual_metric_id is not None:
            manual = manual_lookup.get(fav.manual_metric_id)
            if manual is None:
                continue  # Row was deleted between query and resolve.
            resolved.append(_resolve_manual(fav.id, manual))
        elif (
            fav.market_config_id is not None
            and fav.favorite_chain_id is not None
            and fav.favorite_market_id_hex is not None
            and fav.favorite_label is not None
        ):
            protocol = protocol_lookup.get(fav.market_config_id)
            if protocol is None:
                continue  # Protocol was deleted between query and resolve.
            key: _MarketKey = (
                fav.market_config_id,
                fav.favorite_chain_id,
                fav.favorite_market_id_hex,
            )
            score = score_lookup.get(key)
            team_pd = (
                await compute_team_market_final_pd(
                    session,
                    protocol=protocol,
                    chain_id=fav.favorite_chain_id,
                    market_id_hex=fav.favorite_market_id_hex,
                    team_id=team_id,
                )
                if team_id is not None
                else None
            )
            resolved.append(
                _resolve_market(
                    favorite_id=fav.id,
                    market_config_id=fav.market_config_id,
                    protocol=protocol,
                    chain_id=fav.favorite_chain_id,
                    market_id_hex=fav.favorite_market_id_hex,
                    label=fav.favorite_label,
                    score=score,
                    team_pd=team_pd,
                )
            )
        elif fav.metric_type is not None:
            resolved.append(
                await _resolve_auto(
                    session, fav.id, fav.metric_type, fav.chain, fav.token
                )
            )
    return resolved


async def _load_manual_metrics(
    session: AsyncSession, ids: list[UUID]
) -> dict[UUID, ManualMetric]:
    if not ids:
        return {}
    stmt = select(ManualMetric).where(ManualMetric.id.in_(ids))
    rows = (await session.execute(stmt)).scalars().all()
    return {row.id: row for row in rows}


async def _load_protocols(
    session: AsyncSession, ids: list[UUID]
) -> dict[UUID, MarketConfig]:
    """Batch-load the protocol rows referenced by market favorites.

    Used by the resolver to detect favorites whose protocol has been
    deleted (they are silently dropped from the card list) and to feed
    :func:`compute_team_market_final_pd`.
    """
    if not ids:
        return {}
    rows = (
        (await session.execute(select(MarketConfig).where(MarketConfig.id.in_(ids))))
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


async def _load_latest_scores_by_market(
    session: AsyncSession, keys: list[_MarketKey]
) -> dict[_MarketKey, MarketScore]:
    """Batch-load the latest MarketScore per ``(protocol, chain, market)``.

    Returns an empty dict for ``keys=[]``. A missing entry means the
    scorer hasn't produced a PD for that market yet — the resolver
    renders the card value as ``—`` rather than failing.
    """
    if not keys:
        return {}
    market_config_ids = {key[0] for key in keys}
    # One query for every score row touching any of the involved
    # protocol rows, narrowed in Python afterwards. The score query
    # uses DISTINCT ON for efficient latest-row selection per
    # ``(market_config_id, chain_id, market_id_hex)``.
    stmt = (
        select(MarketScore)
        .where(MarketScore.market_config_id.in_(market_config_ids))
        .order_by(
            MarketScore.market_config_id,
            MarketScore.chain_id,
            MarketScore.market_id_hex,
            desc(MarketScore.created_at),
        )
        .distinct(
            MarketScore.market_config_id,
            MarketScore.chain_id,
            MarketScore.market_id_hex,
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    want = set(keys)
    return {
        (row.market_config_id, row.chain_id, row.market_id_hex): row
        for row in rows
        if (row.market_config_id, row.chain_id, row.market_id_hex) in want
    }


def _resolve_market(
    *,
    favorite_id: UUID,
    market_config_id: UUID,
    protocol: MarketConfig,
    chain_id: int,
    market_id_hex: str,
    label: str,
    score: MarketScore | None,
    team_pd: Decimal | None = None,
) -> ResolvedFavorite:
    """Render a market favorite as a dashboard card.

    The displayed value is the team-weighted PD (``team_pd``) when one was
    recomputed, otherwise the most recent global ``MarketScore.final_pd``,
    formatted as a percent to two decimal places (matching the market list /
    show pages' ``formatFinalPd``). When neither exists yet (e.g. a
    brand-new market), ``value`` is ``None`` and the card shows the
    placeholder. ``label`` is the cached yarn label captured at favorite
    creation time so the card still renders if the protocol momentarily
    drops the market from yarn output.
    """
    pd_value: Decimal | None = (
        team_pd
        if team_pd is not None
        else (score.final_pd if score is not None else None)
    )
    value: str | None = None if pd_value is None else f"{float(pd_value) * 100:.2f}%"
    href = f"/markets/{protocol.protocol}/{chain_id}/{market_id_hex}/"
    return ResolvedFavorite(
        id=favorite_id,
        label=label,
        value=value,
        href=href,
        market_config_id=market_config_id,
        primary_label="Probability of Default",
        card_kind="market",
    )


def _resolve_manual(favorite_id: UUID, manual: ManualMetric) -> ResolvedFavorite:
    # PROTOCOL_SCORE/SUMMARY rows store the PD in `value` (text); other
    # PROTOCOL_SCORE rows may only carry a numeric `risk_score`. Prefer the
    # freeform value, fall back to the score.
    if manual.value is not None:
        value: str | None = manual.value
    elif manual.risk_score is not None:
        value = str(manual.risk_score)
    else:
        value = None
    if manual.protocol is not None:
        card_kind: str | None = "protocol"
    elif manual.token is not None:
        card_kind = "token"
    else:
        card_kind = None
    return ResolvedFavorite(
        id=favorite_id,
        label=_manual_label(manual),
        value=value,
        href=_manual_href(manual),
        manual_metric_id=manual.id,
        card_kind=card_kind,
    )


# Display names mirror PROTOCOL_LABELS in resources/pages/protocol/show.tsx.
# Kept duplicated rather than refactored into a shared module because the
# set is small and stable. Market favorites moved to a dedicated
# ``market_config_id`` target in Phase 4 — there's no manual-metric market
# path left to look up.
_PROTOCOL_LABELS: dict[str, str] = {
    "AAVE_V3": "Aave v3",
    "MORPHO_V2": "Morpho v2",
    "COMPOUND_V3": "Compound v3",
    "DRIFT_V2": "Drift v2",
}

# Display names mirror TOKEN_LABELS in resources/pages/token/show.tsx. Kept
# duplicated for the same reason as ``_PROTOCOL_LABELS`` above.
_TOKEN_LABELS: dict[str, str] = {
    "WETH": "WETH",
    "USDE": "USDe",
    "AAVE": "Aave (AAVE)",
    "UNI": "Uniswap (UNI)",
    "USDC": "USDC",
    "USDT0": "USDT0",
    "AUSDC": "aUSDC",
    "CUSDC": "cUSDC",
    "CBBTC": "cbBTC",
    "LINK": "LINK",
    "STETH": "stETH",
    "WSTETH": "wstETH",
}


def _manual_label(manual: ManualMetric) -> str:
    # Only PROTOCOL_SCORE / TOKEN_SCORE rows (the "Probability of default"
    # entry) are favoritable as manual metrics; the entity name alone
    # identifies the card unambiguously.
    if manual.protocol is not None:
        return _PROTOCOL_LABELS.get(manual.protocol.value, manual.protocol.value)
    if manual.token is not None:
        return _TOKEN_LABELS.get(manual.token.value, manual.token.value)
    return manual.name


def _manual_href(manual: ManualMetric) -> str:
    if manual.protocol is not None:
        return f"/protocols/{manual.protocol.value}/"
    if manual.token is not None:
        return f"/tokens/{manual.token.value}/"
    return "/manual-metrics?category=PROTOCOL_SCORE"


async def _resolve_auto(
    session: AsyncSession,
    favorite_id: UUID,
    metric_type: MetricType,
    chain: ChainType | None,
    token: object | None,
) -> ResolvedFavorite:
    """Resolve an auto-metric favorite to a card.

    For per-chain finality favorites (``_FINALITY_DETAIL``) the latest snapshot
    row is fetched once and both the primary and secondary columns are pulled
    from it, along with the chain-specific explanation copy.
    """
    detail = _FINALITY_DETAIL.get(metric_type)
    source = lookup_metric_source(metric_type)

    value: str | None = None
    secondary_value: str | None = None
    primary_label: str | None = None
    secondary_label: str | None = None
    description: str | None = None

    if source is not None:
        model = _TABLE_MODELS.get(source.table)
        if model is not None:
            pinned_chain = source.chain_filter or chain
            stmt = select(model).order_by(desc(model.created_at)).limit(1)
            if pinned_chain is not None and hasattr(model, "chain"):
                stmt = (
                    select(model)
                    .where(model.chain == pinned_chain)
                    .order_by(desc(model.created_at))
                    .limit(1)
                )
            row = (await session.execute(stmt)).scalars().first()
            if row is not None:
                if detail is not None:
                    value = _format_with_unit(
                        getattr(row, detail.primary_column, None), detail.primary_unit
                    )
                    secondary_value = _format_with_unit(
                        getattr(row, detail.secondary_column, None),
                        detail.secondary_unit,
                    )
                    primary_label = detail.primary_label
                    secondary_label = detail.secondary_label
                    description = detail.description
                elif metric_type is MetricType.TRANSACTIONS_PER_SECOND:
                    tps = getattr(row, "transactions_per_second", None)
                    value = None if tps is None else _format_value(tps)
                    primary_label = "Transactions per second"
                    gas = _THROUGHPUT_GAS.get(chain) if chain is not None else None
                    if gas is not None:
                        secondary_value = _format_gas(
                            getattr(row, "gas_price", None), gas
                        )
                        secondary_label = gas.label
                else:
                    raw = getattr(row, source.column, None)
                    value = None if raw is None else _format_value(raw)

    return ResolvedFavorite(
        id=favorite_id,
        label=_auto_label(metric_type, chain),
        value=value,
        href=_auto_href(chain),
        metric_type=metric_type,
        chain=chain,
        token=token,  # type: ignore[arg-type]
        primary_label=primary_label,
        secondary_label=secondary_label,
        secondary_value=secondary_value,
        description=description,
        card_kind="token" if token is not None else "chain",
    )


def _format_value(raw: object) -> str:
    if isinstance(raw, float):
        return f"{raw:,.2f}"
    if isinstance(raw, int):
        return f"{raw:,}"
    return str(raw)


def _format_with_unit(raw: object | None, unit: str) -> str | None:
    if raw is None:
        return None
    return f"{_format_value(raw)} {unit}"


def _format_gas(raw: object | None, gas: _ThroughputGas) -> str | None:
    """Format a throughput gas-price column as a chain-specific sub-value.

    Numeric values are divided by ``gas.scale`` (wei → gwei for EVM chains)
    and rendered with extra precision below 1 so small gas prices and the
    sub-dollar Canton amulet price don't collapse to ``0.00``.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        scaled = raw / gas.scale
        decimals = 4 if abs(scaled) < 1 else 2
        return f"{scaled:,.{decimals}f} {gas.unit}"
    return f"{raw} {gas.unit}"


def _chain_display(chain: ChainType) -> str:
    """Pretty chain name (e.g. ``ETHEREUM`` → ``Ethereum``).

    Mirrors ``CHAIN_LABELS`` in ``resources/pages/chain/show.tsx``; title-casing
    the enum value reproduces every entry (``AVALANCHE_C`` → ``Avalanche C``).
    """
    return chain.value.replace("_", " ").title()


def _auto_label(metric_type: MetricType, chain: ChainType | None) -> str:
    if metric_type is MetricType.TRANSACTIONS_PER_SECOND and chain is not None:
        return f"{_chain_display(chain)} - Throughput"
    pretty = metric_type.value.replace("_", " ").title()
    if chain is not None:
        return f"{chain.value} · {pretty}"
    return pretty


def _auto_href(chain: ChainType | None) -> str:
    if chain is not None:
        return f"/chains/{chain.value}/"
    return "/chains/"
