# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Inertia page props + helper shapes for the public market list / show."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from decimal import Decimal  # noqa: TC003
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct

__all__ = (
    "AnchorScoreRow",
    "AppliedWeightingProfile",
    "AssuranceItem",
    "ControlScoreRow",
    "DashboardOption",
    "MarketListItem",
    "MarketListPage",
    "MarketScoring",
    "MarketShowPage",
    "PdBreakdown",
    "ScoreTrendPoint",
)


class DashboardOption(CamelizedBaseStruct):
    """A dashboard the viewer owns, surfaced on the favorite picker.

    The show page renders one entry per dashboard so the star button
    can route the favorite to the user's preferred target. ``is_default``
    drives the initial selection. ``favorite_id`` is the existing
    ``UserFavoriteMetric.id`` when ``contains_market`` is true — the
    UI uses it for the DELETE round-trip without needing a fan-out
    GET against ``/dashboards/{id}/favorites`` first.
    """

    id: UUID
    name: str
    is_default: bool
    contains_market: bool
    favorite_id: UUID | None = None


class MarketListItem(CamelizedBaseStruct):
    """One row in the public markets list."""

    id: UUID
    protocol: str
    chain_id: int
    market_id_hex: str
    label: str
    enabled: bool
    # Optional summary metrics rendered on the list card.
    latest_pd: Decimal | None = None
    latest_pd_at: datetime | None = None


class MarketListPage(CamelizedBaseStruct):
    """Page props for ``GET /markets/``."""

    markets: list[MarketListItem]


class AppliedWeightingProfile(CamelizedBaseStruct):
    """The weighting profile that shaped the PD shown on this page.

    Resolved on the same precedence path as the displayed PD: the
    viewer's team profile when they have a team selected (and recompute
    succeeded), otherwise the global default the scorer used. ``None`` on
    the page when no profile matched — every weight defaulted to 1.0.

    ``override_count`` is the number of ``(category, sub_category)``
    weight overrides in the profile; the per-row weights themselves ride
    along in :attr:`PdBreakdown.breakdown` so the UI can badge each
    affected sub-category.
    """

    id: UUID
    name: str
    scope: str
    is_global: bool
    override_count: int
    team_name: str | None = None
    target_label: str | None = None
    target_protocol: str | None = None


class PdBreakdown(CamelizedBaseStruct):
    """Per-term breakdown of the latest PD computation.

    Mirrors :class:`cert_ra.metrics.market.scoring.PdBreakdown` but
    flattened for the wire so the frontend can render the explainer
    table without recomputing.
    """

    final_pd: Decimal
    anchors_term: Decimal
    control_term: Decimal
    assurance_term: Decimal
    breakdown: dict
    computed_at: datetime


class AnchorScoreRow(CamelizedBaseStruct):
    """One anchor sub-category's judgment surfaced on the market page.

    Scorer rows are read straight off ``score.anchors[sub_category]`` in
    the latest SCORE snapshot — the qualitative LLM output, independent of
    team weighting. Manual rows (``source="manual"``) are operator-entered
    ANCHORS metrics folded into the same anchors term; the UI badges them.
    """

    sub_category: str
    score: float | None
    pd: float | None
    conclusion: str | None
    rationale: list[str]
    source: str = "scorer"


class ControlScoreRow(CamelizedBaseStruct):
    """One control sub-category's raw scorer judgment.

    Read off ``score.controls[sub_category]`` (the scorer's renamed
    ``controlModifiers`` block) in the latest SCORE snapshot.
    """

    sub_category: str
    multiplier: float | None
    conclusion: str | None
    rationale: list[str]


class MarketScoring(CamelizedBaseStruct):
    """The latest SCORE snapshot's per-sub-category scorer output.

    Drives the scoring tables on the PD card. ``None`` on the page when
    no SCORE snapshot exists yet.
    """

    anchors: list[AnchorScoreRow]
    controls: list[ControlScoreRow]


class AssuranceItem(CamelizedBaseStruct):
    """One manual ASSURANCE row surfaced on the market show page."""

    id: UUID
    name: str
    sub_category: str | None
    value: str | None
    risk_score: int | None
    notes: str | None


class ScoreTrendPoint(CamelizedBaseStruct):
    """One point on the score trend chart."""

    captured_at: datetime
    final_pd: Decimal
    anchors_term: Decimal
    control_term: Decimal
    assurance_term: Decimal


class MarketAlertSubCategories(CamelizedBaseStruct):
    """Distinct anchor / control sub_categories observed in recent SCORE snapshots.

    Drives the sub_category typeahead in the alert create dialog. ``anchors``
    and ``control_modifiers`` are independent — the dialog picks the right
    list based on the selected target kind.
    """

    anchors: list[str]
    control_modifiers: list[str]


class MarketAlertOption(CamelizedBaseStruct):
    """One discovered market exposed to the alert create dialog.

    Carries enough identity to populate the ``MARKET_*`` target_config
    variants directly from the chosen entry — no extra round trip to look up
    the ``market_config_id`` later.
    """

    market_config_id: UUID
    protocol: str
    chain_id: int
    market_id_hex: str
    label: str
    # The ``ProtocolType`` (e.g. ``AAVE_V3``) this market maps to for
    # manual metrics, so the manual-metric form can filter the market-pin
    # selector to markets of the chosen protocol. ``None`` when unmapped.
    assurance_protocol: str | None = None


class MarketAlertOptionsResponse(CamelizedBaseStruct):
    """Wrapper for the discovered-markets list endpoint."""

    items: list[MarketAlertOption]


class MarketShowPage(CamelizedBaseStruct):
    """Page props for ``GET /markets/{protocol}/{chain_id}/{market_id_hex}/``.

    Sections (Inertia renders them in order):

    * ``market`` — basic identity + label.
    * ``pd`` — current PD card. ``None`` until the first scorer run
      completes for this market.
    * ``trend`` — hourly score points, sorted oldest → newest, up to
      168 points (a week). Frontend hides the chart when ``len < 2``.
    * ``anchors`` / ``modifiers`` — the two top-level metric trees from
      the most recent COLLECT snapshot. Either may be empty if the
      collector hasn't ticked yet.
    * ``scoring`` — the latest SCORE snapshot's per-sub-category scorer
      output (score/pd/multiplier/conclusion/rationale). ``None`` until
      the first scorer run completes.
    * ``assurance_metrics`` — protocol-level manual ASSURANCE rows.
    * ``applied_profile`` — the weighting profile that shaped ``pd``,
      or ``None`` when the default weight of 1.0 applied everywhere.
    * ``is_favorited`` — pre-computed so the star can render without
      a follow-up round-trip.
    """

    market: MarketListItem
    pd: PdBreakdown | None
    trend: list[ScoreTrendPoint]
    anchors: dict
    modifiers: dict
    metrics_captured_at: datetime | None
    scoring: MarketScoring | None
    assurance_metrics: list[AssuranceItem]
    applied_profile: AppliedWeightingProfile | None = None
    is_favorited: bool = False
    # Per-dashboard targets for the star button. Pre-resolved on the
    # server so the frontend doesn't fan out an extra request to
    # /api/dashboards/ just to discover the user's options. The first
    # entry whose ``is_default`` is true is the default click target.
    dashboards: list[DashboardOption] = msgspec.field(default_factory=list)
