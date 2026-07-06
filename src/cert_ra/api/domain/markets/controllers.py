# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Public market list + show pages.

Replaces the legacy ``MarketsController`` in ``web/controllers.py`` that
served the four-market ``MarketType`` enum. The new URL shape is
``/markets/{protocol}/{chain_id}/{market_id_hex}/``; markets are
discovered dynamically by the collector workflow running
``yarn <protocol>`` per tick, so the list page derives its entries
from the most recent ``AutomatedMarketSnapshot`` per
``(protocol, chain_id, market_id_hex)`` instead of an admin-curated
table.

Show page is assembly-heavy — it gathers six pieces of state in one
request:

* the latest collector snapshot for evidence + raw metrics,
* the latest scorer snapshot for the PD breakdown,
* the most recent N market_score rows for the trend chart,
* protocol-level ASSURANCE manual metrics (the manual half of the
  scoring methodology),
* the dashboards the viewer owns + whether each pins this market,
* a "is_favorited" flag derived from the dashboards above.

Each piece is queried independently so a missing piece (e.g. brand-new
market with no SCORE row yet) degrades to ``None`` / ``[]`` rather
than 404. A 404 is raised when *no* snapshot exists for the natural
key — we have nothing to render and no way to know whether the market
actually exists upstream until the next collector tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003  (runtime use in Litestar handler signatures)

from litestar import Controller, Request, get
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from sqlalchemy import desc, select

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.markets.assurance import (
    load_market_anchors,
    load_protocol_assurance,
)
from cert_ra.api.domain.markets.schemas import (
    AnchorScoreRow,
    AppliedWeightingProfile,
    AssuranceItem,
    ControlScoreRow,
    DashboardOption,
    MarketAlertOption,
    MarketAlertOptionsResponse,
    MarketAlertSubCategories,
    MarketListItem,
    MarketListPage,
    MarketScoring,
    MarketShowPage,
    PdBreakdown,
    ScoreTrendPoint,
)
from cert_ra.api.domain.weighting_profiles.resolver import resolve_weighting_profile
from cert_ra.api.lib.team_context import current_team_id_from_session
from cert_ra.db.models import (
    AutomatedMarketSnapshot,
    Dashboard,
    ManualMetric,
    MarketConfig,
    MarketScore,
    User,
    UserFavoriteMetric,
    WeightingProfile,
)
from cert_ra.metrics._session import session_factory
from cert_ra.metrics.market.scoring import MarketScoringError, compute_market_pd
from cert_ra.types import MarketSnapshotKind, ProtocolType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ("MarketAlertApiController", "MarketController")

# Window over which we union sub_categories for the alert dialog typeahead.
# Hourly scoring cadence ⇒ 24 SCORE snapshots cover the most recent day; the
# LLM occasionally drops a sub_category from individual ticks, so a day-wide
# union is more useful than a single-row sample.
_SUB_CATEGORY_LOOKBACK_SNAPSHOTS = 24

# Cap the trend response so the wire payload stays bounded — one week
# at hourly cadence = 168 points. Show page renders all of them.
_TREND_LIMIT = 168


@dataclass(frozen=True, slots=True)
class _MarketIdentity:
    """Identity of one market as derived from the snapshot table."""

    market_config_id: UUID
    protocol: str
    chain_id: int
    market_id_hex: str
    label: str
    enabled: bool
    # The ``ProtocolType`` this market's protocol maps to for manual
    # metrics (ANCHORS / ASSURANCE), or ``None`` when unmapped.
    assurance_protocol: ProtocolType | None = None


class MarketController(Controller):
    """Market list + show pages backed by the dynamic snapshot stream."""

    tags = ["Markets"]  # noqa: RUF012
    path = "/markets"
    guards = [requires_active_user]  # noqa: RUF012
    include_in_schema = False

    @get(component="market/list", path="/", name="markets.list")
    async def list_markets(self) -> MarketListPage:
        """List every discovered market plus a one-line PD summary per card.

        Returns:
            One ``MarketListItem`` per ``(protocol, chain_id, market_id_hex)``
            currently surfaced in the snapshot stream of an enabled
            protocol, with the latest ``MarketScore.final_pd``
            denormalised onto the item (``None`` when no score exists
            yet). Sorted by ``(protocol, label)`` so the list is
            alphabetical within each protocol.
        """
        async with session_factory()() as session:
            identities = await _load_discovered_markets(session)
            score_lookup = await _latest_scores_by_market(
                session,
                [(m.market_config_id, m.chain_id, m.market_id_hex) for m in identities],
            )
            items = []
            for m in identities:
                row = score_lookup.get(
                    (m.market_config_id, m.chain_id, m.market_id_hex)
                )
                items.append(
                    MarketListItem(
                        id=m.market_config_id,
                        protocol=m.protocol,
                        chain_id=m.chain_id,
                        market_id_hex=m.market_id_hex,
                        label=m.label,
                        enabled=m.enabled,
                        latest_pd=row.final_pd if row is not None else None,
                        latest_pd_at=row.created_at if row is not None else None,
                    )
                )
        return MarketListPage(markets=items)

    @get(
        component="market/show",
        path="/{protocol:str}/{chain_id:int}/{market_id_hex:str}/",
        name="markets.show",
    )
    async def show_market(
        self,
        request: Request,
        current_user: User,
        protocol: Annotated[str, Parameter(title="Protocol")],
        chain_id: Annotated[int, Parameter(title="Chain id")],
        market_id_hex: Annotated[str, Parameter(title="Market id (hex)")],
    ) -> MarketShowPage:
        """Render the full market detail view, scoped to the viewer's team.

        The persisted ``MarketScore`` was computed by the scorer activity
        against the global default profile + shared (team_id IS NULL)
        ASSURANCE rows. When a viewer has a current team selected, we
        recompute the PD card here against:

        * the team's resolved weighting profile (precedence: team+market
          → team+protocol → global+market → global+protocol),
        * the union of shared + team-scoped ASSURANCE manual metrics
          for the protocol.

        If recomputation fails (bad SCORE payload, etc.) we fall back to
        the stored ``MarketScore`` so the page still renders.

        The trend chart keeps the stored series — recomputing 168
        historical points per page load was deferred for performance.
        The PD card carries the team's current view; the trend shows
        how the global-default view has moved.

        Returns:
            The assembled page props. Raises :class:`NotFoundException`
            when no snapshot exists for the natural key (no protocol
            row, or no collector tick has happened for the market yet).
        """
        viewer_team_id = _current_team_id(request)
        async with session_factory()() as session:
            market_row = await _get_protocol_row(session, protocol=protocol)
            if market_row is None:
                msg = f"Protocol {protocol} is not configured"
                raise NotFoundException(msg)
            latest_collect = await _latest_snapshot(
                session,
                market_config_id=market_row.id,
                chain_id=chain_id,
                market_id_hex=market_id_hex,
                kind=MarketSnapshotKind.COLLECT,
            )
            latest_score_snapshot = await _latest_snapshot(
                session,
                market_config_id=market_row.id,
                chain_id=chain_id,
                market_id_hex=market_id_hex,
                kind=MarketSnapshotKind.SCORE,
            )
            if latest_collect is None and latest_score_snapshot is None:
                msg = f"Market {protocol}/{chain_id}/{market_id_hex} not found"
                raise NotFoundException(msg)

            # The most recent snapshot (collector by default, scorer as
            # fallback) is the source of truth for the human label.
            display_source = latest_collect or latest_score_snapshot
            label = display_source.label if display_source is not None else ""

            latest_score = await _latest_market_score(
                session,
                market_config_id=market_row.id,
                chain_id=chain_id,
                market_id_hex=market_id_hex,
            )
            trend = await _score_trend(
                session,
                market_config_id=market_row.id,
                chain_id=chain_id,
                market_id_hex=market_id_hex,
                limit=_TREND_LIMIT,
            )
            assurance = await load_protocol_assurance(
                session, market_row, viewer_team_id
            )
            manual_anchors = await load_market_anchors(
                session,
                market_row,
                chain_id=chain_id,
                market_id_hex=market_id_hex,
                team_id=viewer_team_id,
            )
            dashboards = await _load_dashboard_options(
                session,
                owner_id=current_user.id,
                market_config_id=market_row.id,
                chain_id=chain_id,
                market_id_hex=market_id_hex,
            )
            # Star lights up when at least one of the viewer's
            # dashboards already pins this market.
            is_favorited = any(d.contains_market for d in dashboards)

            pd, applied_profile = await _resolve_pd_card(
                session,
                market=market_row,
                chain_id=chain_id,
                market_id_hex=market_id_hex,
                viewer_team_id=viewer_team_id,
                latest_score=latest_score,
                latest_score_snapshot=latest_score_snapshot,
                assurance_metrics=assurance,
                manual_anchors=manual_anchors,
            )

            return MarketShowPage(
                market=MarketListItem(
                    id=market_row.id,
                    protocol=market_row.protocol,
                    chain_id=chain_id,
                    market_id_hex=market_id_hex,
                    label=label,
                    enabled=market_row.enabled,
                    latest_pd=(pd.final_pd if pd is not None else None),
                    latest_pd_at=(pd.computed_at if pd is not None else None),
                ),
                pd=pd,
                trend=trend,
                anchors=latest_collect.anchors if latest_collect is not None else {},
                modifiers=(
                    latest_collect.modifiers if latest_collect is not None else {}
                ),
                metrics_captured_at=(
                    latest_collect.created_at if latest_collect is not None else None
                ),
                scoring=_scoring_with_manual_anchors(
                    latest_score_snapshot.score
                    if latest_score_snapshot is not None
                    else None,
                    manual_anchors,
                ),
                assurance_metrics=[
                    AssuranceItem(
                        id=m.id,
                        name=m.name,
                        sub_category=m.sub_category,
                        value=m.value,
                        risk_score=m.risk_score,
                        notes=m.notes,
                    )
                    for m in assurance
                ],
                applied_profile=applied_profile,
                is_favorited=is_favorited,
                dashboards=dashboards,
            )


class MarketAlertApiController(Controller):
    """JSON API endpoints used by the alert create dialog.

    Kept separate from the Inertia ``MarketController`` so OpenAPI emits a
    proper schema for these endpoints (the page controller is
    ``include_in_schema=False`` because its props are Inertia-driven).
    """

    path = "/api/markets"
    tags = ["Markets"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012

    @get(
        operation_id="ListMarketAlertOptions",
        name="markets:alert_options",
        summary="List discovered markets available as alert targets",
        path="/alert-options",
    )
    async def list_alert_options(self) -> MarketAlertOptionsResponse:
        """Return every discovered market the dialog can target.

        Mirrors the discovery the Inertia list page does — one entry per
        ``(protocol, chain_id, market_id_hex)`` currently visible in the
        snapshot stream of an enabled protocol.
        """
        async with session_factory()() as session:
            identities = await _load_discovered_markets(session)
        return MarketAlertOptionsResponse(
            items=[
                MarketAlertOption(
                    market_config_id=identity.market_config_id,
                    protocol=identity.protocol,
                    chain_id=identity.chain_id,
                    market_id_hex=identity.market_id_hex,
                    label=identity.label,
                    assurance_protocol=(
                        identity.assurance_protocol.value
                        if identity.assurance_protocol is not None
                        else None
                    ),
                )
                for identity in identities
            ],
        )

    @get(
        operation_id="ListMarketAlertSubCategories",
        name="markets:alert_sub_categories",
        summary="Distinct anchor / control sub_categories observed for one market",
        path="/{market_config_id:uuid}/{chain_id:int}/{market_id_hex:str}/alert-sub-categories",
    )
    async def list_sub_categories(
        self,
        market_config_id: Annotated[UUID, Parameter(title="Market config id")],
        chain_id: Annotated[int, Parameter(title="Chain id")],
        market_id_hex: Annotated[str, Parameter(title="Market id (hex)")],
    ) -> MarketAlertSubCategories:
        """Union the anchor + control sub_category keys from recent SCORE snapshots.

        Uses the most recent N SCORE snapshots so the typeahead reflects the
        latest yarn output even if individual snapshots dropped a key.
        """
        async with session_factory()() as session:
            stmt = (
                select(AutomatedMarketSnapshot)
                .where(
                    AutomatedMarketSnapshot.market_config_id == market_config_id,
                    AutomatedMarketSnapshot.chain_id == chain_id,
                    AutomatedMarketSnapshot.market_id_hex == market_id_hex,
                    AutomatedMarketSnapshot.kind == MarketSnapshotKind.SCORE,
                )
                .order_by(desc(AutomatedMarketSnapshot.created_at))
                .limit(_SUB_CATEGORY_LOOKBACK_SNAPSHOTS)
            )
            rows = list((await session.scalars(stmt)).all())
        anchors: set[str] = set()
        controls: set[str] = set()
        for row in rows:
            score = row.score
            if not isinstance(score, dict):
                continue
            anchor_block = score.get("anchors")
            if isinstance(anchor_block, dict):
                anchors.update(k for k in anchor_block if isinstance(k, str))
            control_block = score.get("controls")
            if control_block is None:
                control_block = score.get("controlModifiers")
            if isinstance(control_block, dict):
                controls.update(k for k in control_block if isinstance(k, str))
        return MarketAlertSubCategories(
            anchors=sorted(anchors),
            control_modifiers=sorted(controls),
        )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _coerce_float(value: object) -> float | None:
    """Best-effort numeric coercion for a raw scorer field; ``None`` if not numeric."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_rationale(value: object) -> list[str]:
    """Keep only the string entries of a rationale array; ``[]`` otherwise."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _coerce_conclusion(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _build_scoring(score: object) -> MarketScoring | None:
    """Project a raw ``score`` dict into the page's scoring tables.

    Reads ``score.anchors`` and ``score.controls`` (the scorer's current
    key; older snapshots used ``controlModifiers``, still accepted). Each
    sub-category becomes one row. Returns ``None`` when there is nothing
    to show so the frontend can hide the tables.
    """
    if not isinstance(score, dict) or not score:
        return None
    anchors_raw = score.get("anchors")
    controls_raw = score.get("controls")
    if controls_raw is None:
        controls_raw = score.get("controlModifiers")
    anchors: list[AnchorScoreRow] = []
    if isinstance(anchors_raw, dict):
        for sub_category, payload in anchors_raw.items():
            if not isinstance(payload, dict):
                continue
            anchors.append(
                AnchorScoreRow(
                    sub_category=sub_category,
                    score=_coerce_float(payload.get("score")),
                    pd=_coerce_float(payload.get("pd")),
                    conclusion=_coerce_conclusion(payload.get("conclusion")),
                    rationale=_coerce_rationale(payload.get("rationale")),
                )
            )
    controls: list[ControlScoreRow] = []
    if isinstance(controls_raw, dict):
        for sub_category, payload in controls_raw.items():
            if not isinstance(payload, dict):
                continue
            controls.append(
                ControlScoreRow(
                    sub_category=sub_category,
                    multiplier=_coerce_float(payload.get("multiplier")),
                    conclusion=_coerce_conclusion(payload.get("conclusion")),
                    rationale=_coerce_rationale(payload.get("rationale")),
                )
            )
    if not anchors and not controls:
        return None
    return MarketScoring(anchors=anchors, controls=controls)


def _manual_anchor_row(metric: ManualMetric) -> AnchorScoreRow:
    """Project a manual ANCHORS metric into a scoring-table row.

    ``value`` carries the probability (already validated to ``[0, 1)`` at
    write time); ``desc`` / ``notes`` surface as rationale. ``source`` is
    ``"manual"`` so the UI can badge it apart from scorer anchors.
    """
    return AnchorScoreRow(
        sub_category=metric.sub_category or metric.name,
        score=None,
        pd=_coerce_float(metric.value),
        conclusion=None,
        rationale=[t for t in (metric.desc, metric.notes) if t],
        source="manual",
    )


def _scoring_with_manual_anchors(
    score: object, manual_anchors: list[ManualMetric]
) -> MarketScoring | None:
    """Build the scoring tables and append manual anchor rows.

    Manual anchors are shown even when the scorer hasn't run yet (no SCORE
    snapshot): rows with a blank ``value`` are skipped (they contribute
    nothing to the PD either). Returns ``None`` only when there is nothing
    at all to show.
    """
    base = _build_scoring(score)
    manual_rows = [
        _manual_anchor_row(m) for m in manual_anchors if m.value not in (None, "")
    ]
    if not manual_rows:
        return base
    if base is None:
        return MarketScoring(anchors=manual_rows, controls=[])
    return MarketScoring(anchors=[*base.anchors, *manual_rows], controls=base.controls)


async def _load_discovered_markets(session: AsyncSession) -> list[_MarketIdentity]:
    """Derive the per-market identity list from snapshot history.

    A market is "discovered" iff at least one ``AutomatedMarketSnapshot``
    row exists for it under an enabled protocol — that's the only way
    to know the workers have ever produced data for it.

    ``DISTINCT ON (market_config_id, chain_id, market_id_hex)`` picks
    the most recent snapshot per natural key so the cached label
    reflects the latest yarn output. Filtering to enabled protocols
    happens by joining ``market_config``.
    """
    stmt = (
        select(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
            AutomatedMarketSnapshot.label,
            MarketConfig.protocol,
            MarketConfig.enabled,
            MarketConfig.assurance_protocol,
        )
        .join(MarketConfig, MarketConfig.id == AutomatedMarketSnapshot.market_config_id)
        .where(MarketConfig.enabled.is_(True))
        .order_by(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
            desc(AutomatedMarketSnapshot.created_at),
        )
        .distinct(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
        )
    )
    rows = (await session.execute(stmt)).all()
    identities = [
        _MarketIdentity(
            market_config_id=row.market_config_id,
            chain_id=row.chain_id,
            market_id_hex=row.market_id_hex,
            label=row.label,
            protocol=row.protocol,
            enabled=row.enabled,
            assurance_protocol=row.assurance_protocol,
        )
        for row in rows
    ]
    identities.sort(key=lambda m: (m.protocol, m.label))
    return identities


async def _get_protocol_row(
    session: AsyncSession, *, protocol: str
) -> MarketConfig | None:
    stmt = select(MarketConfig).where(MarketConfig.protocol == protocol.lower())
    return (await session.scalars(stmt)).first()


async def _latest_snapshot(
    session: AsyncSession,
    *,
    market_config_id: UUID,
    chain_id: int,
    market_id_hex: str,
    kind: MarketSnapshotKind,
) -> AutomatedMarketSnapshot | None:
    stmt = (
        select(AutomatedMarketSnapshot)
        .where(
            AutomatedMarketSnapshot.market_config_id == market_config_id,
            AutomatedMarketSnapshot.chain_id == chain_id,
            AutomatedMarketSnapshot.market_id_hex == market_id_hex,
            AutomatedMarketSnapshot.kind == kind,
        )
        .order_by(desc(AutomatedMarketSnapshot.created_at))
        .limit(1)
    )
    return (await session.scalars(stmt)).first()


async def _latest_market_score(
    session: AsyncSession,
    *,
    market_config_id: UUID,
    chain_id: int,
    market_id_hex: str,
) -> MarketScore | None:
    stmt = (
        select(MarketScore)
        .where(
            MarketScore.market_config_id == market_config_id,
            MarketScore.chain_id == chain_id,
            MarketScore.market_id_hex == market_id_hex,
        )
        .order_by(desc(MarketScore.created_at))
        .limit(1)
    )
    return (await session.scalars(stmt)).first()


async def _latest_scores_by_market(
    session: AsyncSession, keys: list[tuple[UUID, int, str]]
) -> dict[tuple[UUID, int, str], MarketScore]:
    """Latest MarketScore per ``(market_config_id, chain_id, market_id_hex)``."""
    if not keys:
        return {}
    market_config_ids = {key[0] for key in keys}
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
    rows = (await session.scalars(stmt)).all()
    want = set(keys)
    return {
        (row.market_config_id, row.chain_id, row.market_id_hex): row
        for row in rows
        if (row.market_config_id, row.chain_id, row.market_id_hex) in want
    }


async def _score_trend(
    session: AsyncSession,
    *,
    market_config_id: UUID,
    chain_id: int,
    market_id_hex: str,
    limit: int,
) -> list[ScoreTrendPoint]:
    """Return up to ``limit`` recent score points, oldest first."""
    stmt = (
        select(MarketScore)
        .where(
            MarketScore.market_config_id == market_config_id,
            MarketScore.chain_id == chain_id,
            MarketScore.market_id_hex == market_id_hex,
        )
        .order_by(desc(MarketScore.created_at))
        .limit(limit)
    )
    rows = list((await session.scalars(stmt)).all())
    rows.reverse()  # frontend expects oldest → newest
    return [
        ScoreTrendPoint(
            captured_at=r.created_at,
            final_pd=r.final_pd,
            anchors_term=r.anchors_term,
            control_term=r.control_term,
            assurance_term=r.assurance_term,
        )
        for r in rows
    ]


def _current_team_id(request: Request) -> UUID | None:
    """Read the viewer's effective team from the request session.

    The team is the switcher selection or, when none was made, the
    user's default team (set by ``current_user_from_session``).
    ``None`` means "no team" — recomputation falls through to the
    global default profile + shared-only ASSURANCE.
    """
    return current_team_id_from_session(request.session)


async def _resolve_pd_card(
    session: AsyncSession,
    *,
    market: MarketConfig,
    chain_id: int,
    market_id_hex: str,
    viewer_team_id: UUID | None,
    latest_score: MarketScore | None,
    latest_score_snapshot: AutomatedMarketSnapshot | None,
    assurance_metrics: list[ManualMetric],
    manual_anchors: list[ManualMetric],
) -> tuple[PdBreakdown | None, AppliedWeightingProfile | None]:
    """Return the PD card + the weighting profile that shaped it.

    Order of preference:

    1. **Team-aware recomputation** — runs when the viewer has a team
       selected AND we have access to the source SCORE snapshot's
       ``score`` block. Resolves the team's weighting profile,
       combines team + shared ASSURANCE, and re-runs
       :func:`compute_market_pd`. The resulting card carries the
       viewer's locally-tuned PD, and the applied profile is the one
       resolved for ``viewer_team_id``.
    2. **Stored MarketScore** — used when the viewer has no current
       team, or when recomputation fails (e.g. the source snapshot
       was purged or compute raises). The card is the same numbers
       the scorer activity wrote (global default profile), so the
       applied profile is re-resolved with ``team_id=None`` to name
       the global default in effect for the same precedence inputs.
    3. **(None, None)** — no PD has ever been computed for this market.

    The applied profile is ``None`` whenever no profile matched —
    every weight defaulted to 1.0.
    """
    if (
        viewer_team_id is not None
        and latest_score_snapshot is not None
        and isinstance(latest_score_snapshot.score, dict)
        and latest_score_snapshot.score
    ):
        profile = await resolve_weighting_profile(
            session,
            protocol=market.protocol,
            market_config_id=market.id,
            chain_id=chain_id,
            market_id_hex=market_id_hex,
            team_id=viewer_team_id,
        )
        try:
            breakdown = compute_market_pd(
                latest_score_snapshot.score,
                list(profile.entries) if profile is not None else [],
                assurance_metrics,
                manual_anchors,
            )
        except MarketScoringError:
            breakdown = None
        if breakdown is not None:
            return (
                PdBreakdown(
                    final_pd=Decimal(repr(breakdown.final_pd)),
                    anchors_term=Decimal(repr(breakdown.anchors_term)),
                    control_term=Decimal(repr(breakdown.control_term)),
                    assurance_term=Decimal(repr(breakdown.assurance_term)),
                    breakdown=breakdown.breakdown,
                    computed_at=datetime.now(UTC),
                ),
                _to_applied_profile(profile),
            )

    if latest_score is None:
        return None, None
    global_profile = await resolve_weighting_profile(
        session,
        protocol=market.protocol,
        market_config_id=market.id,
        chain_id=chain_id,
        market_id_hex=market_id_hex,
        team_id=None,
    )
    return (
        PdBreakdown(
            final_pd=latest_score.final_pd,
            anchors_term=latest_score.anchors_term,
            control_term=latest_score.control_term,
            assurance_term=latest_score.assurance_term,
            breakdown=latest_score.breakdown or {},
            computed_at=latest_score.created_at,
        ),
        _to_applied_profile(global_profile),
    )


def _to_applied_profile(
    profile: WeightingProfile | None,
) -> AppliedWeightingProfile | None:
    """Project a resolved profile into its page shape, or ``None``."""
    if profile is None:
        return None
    return AppliedWeightingProfile(
        id=profile.id,
        name=profile.name,
        scope=profile.scope.value,
        is_global=profile.team_id is None,
        override_count=len(profile.entries),
        team_name=profile.team.name if profile.team is not None else None,
        target_label=profile.target_label,
        target_protocol=profile.target_protocol,
    )


async def _load_dashboard_options(
    session: AsyncSession,
    *,
    owner_id: UUID,
    market_config_id: UUID,
    chain_id: int,
    market_id_hex: str,
) -> list[DashboardOption]:
    """Return all dashboards owned by ``owner_id``, flagging market membership.

    The show page surfaces these so the FavoriteButton can route the
    POST/DELETE to the right ``/dashboards/{id}/favorites/market``
    endpoint without an extra round-trip. A LEFT JOIN against
    ``UserFavoriteMetric`` (filtered by
    ``(market_config_id, favorite_chain_id, favorite_market_id_hex)``)
    keeps the cost to a single query per page load.

    Sort order: default first, then alphabetical by name — so the
    default appears first in any picker that follows insertion order.
    """
    fav_subq = (
        select(UserFavoriteMetric.id)
        .where(
            UserFavoriteMetric.dashboard_id == Dashboard.id,
            UserFavoriteMetric.market_config_id == market_config_id,
            UserFavoriteMetric.favorite_chain_id == chain_id,
            UserFavoriteMetric.favorite_market_id_hex == market_id_hex,
        )
        .correlate(Dashboard)
        .limit(1)
        .scalar_subquery()
    )
    stmt = (
        select(Dashboard, fav_subq.label("favorite_id"))
        .where(Dashboard.owner_id == owner_id)
        .order_by(desc(Dashboard.is_default), Dashboard.name)
    )
    rows = (await session.execute(stmt)).all()
    return [
        DashboardOption(
            id=dashboard.id,
            name=dashboard.name,
            is_default=dashboard.is_default,
            contains_market=favorite_id is not None,
            favorite_id=favorite_id,
        )
        for dashboard, favorite_id in rows
    ]
