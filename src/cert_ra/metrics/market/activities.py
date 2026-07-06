# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Temporal activities for the automated-market-metrics pipeline.

Four activities:

* :func:`load_enabled_protocols` — workflow's first call each tick.
  Returns the operator-curated set of enabled ``market_config`` rows
  (one per protocol) as :class:`MarketConfigRef`. The workflow uses
  these to decide which protocols to ask yarn about this tick.
* :func:`list_protocol_markets` — runs ``yarn <protocol>`` (no extra
  args) for one protocol, parses the JSON array into a list of
  :class:`MarketTickRef`. The workflow fans collect/score activities
  out across these.
* :func:`collect_market_snapshot` — runs ``yarn <protocol> --output
  json <chain_id> <market_id_hex>`` for one market, parses the JSON
  into :class:`CollectorPayload`, and persists a snapshot with
  ``kind='COLLECT'`` (denormalising chain id / market id hex / label
  onto the row so the UI doesn't need yarn re-runs to label things).
* :func:`score_market_snapshot` — same as ``collect`` but with
  ``--score`` and a downstream :class:`MarketScore` row.

All four are designed to be retried by Temporal: errors surface as
exceptions, the DB writes are idempotent at the row level (one new row
per run, no upsert), and JSON parsing failures are tagged as
non-retryable so a malformed yarn output doesn't burn the retry budget.
"""

from __future__ import annotations

import json
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select
from temporalio import activity

from cert_ra.api.domain.markets.assurance import (
    load_market_anchors,
    load_protocol_assurance,
)
from cert_ra.api.domain.weighting_profiles.resolver import (
    resolve_weighting_profile_entries,
)
from cert_ra.db.models import (
    AutomatedMarketSnapshot,
    MarketConfig,
    MarketScore,
)
from cert_ra.metrics._session import session_factory
from cert_ra.metrics.market.schemas import (
    CollectorPayload,
    MarketConfigRef,
    MarketTickRef,
    ProtocolMarketListing,
    ScorerPayload,
)
from cert_ra.metrics.market.scoring import MarketScoringError, compute_market_pd
from cert_ra.metrics.market.yarn import (
    YarnInvocation,
    run_yarn,
    run_yarn_list,
)
from cert_ra.types import MarketSnapshotKind

__all__ = (
    "MarketSnapshotPayloadError",
    "collect_market_snapshot",
    "list_protocol_markets",
    "load_enabled_protocols",
    "score_market_snapshot",
)


class MarketSnapshotPayloadError(Exception):
    """Raised when the yarn output is not parseable as the expected shape.

    Temporal treats this as non-retryable via the activity workflow's
    retry policy — the LLM run is presumed deterministic enough that
    retrying without a code change would just waste credits.
    """


# ---------------------------------------------------------------------------
# Activity 1 — read the runtime configuration
# ---------------------------------------------------------------------------


@activity.defn
async def load_enabled_protocols() -> list[MarketConfigRef]:
    """Return all ``enabled=true`` market_config rows as ``MarketConfigRef``.

    Sorting by ``protocol`` keeps tick-to-tick fan-out order stable so
    a flaky protocol doesn't push a later one off the end of the
    schedule window.
    """
    async with session_factory()() as session:
        stmt = (
            select(MarketConfig)
            .where(MarketConfig.enabled.is_(True))
            .order_by(MarketConfig.protocol)
        )
        rows = (await session.scalars(stmt)).all()
        return [MarketConfigRef(id=row.id, protocol=row.protocol) for row in rows]


# ---------------------------------------------------------------------------
# Activity 2 — discover the markets for one protocol
# ---------------------------------------------------------------------------


def _parse_listing_output(raw: str, cfg: MarketConfigRef) -> list[MarketTickRef]:
    """Parse the yarn list output. Raises ``MarketSnapshotPayloadError``.

    The yarn CLI prints a JSON array of
    ``{"protocol", "chainId", "marketId", "label"}`` objects. We
    validate each entry through :class:`ProtocolMarketListing` (which
    accepts the camelCase keys verbatim), and remap into the snake_case
    :class:`MarketTickRef` the per-market activities consume.

    Every returned entry is kept. The yarn output's own ``protocol``
    field is informational only — the tool emits a versioned variant
    (e.g. ``aave-v3``) of the CLI subcommand (``aave``), so it won't
    equal ``cfg.protocol``. The fanned-out :class:`MarketTickRef` carries
    ``cfg.protocol`` (the CLI command the per-market activities re-invoke
    yarn with), not the entry's label.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"list_protocol_markets[{cfg.protocol}]: yarn output was not valid JSON"
        raise MarketSnapshotPayloadError(msg) from exc
    if not isinstance(decoded, list):
        msg = (
            f"list_protocol_markets[{cfg.protocol}]: expected a JSON array, "
            f"got {type(decoded).__name__}"
        )
        raise MarketSnapshotPayloadError(msg)
    refs: list[MarketTickRef] = []
    for index, entry in enumerate(decoded):
        try:
            listing = ProtocolMarketListing.model_validate(entry)
        except ValidationError as exc:
            msg = (
                f"list_protocol_markets[{cfg.protocol}]: entry {index} did not "
                f"match expected shape"
            )
            raise MarketSnapshotPayloadError(msg) from exc
        refs.append(
            MarketTickRef(
                market_config_id=cfg.id,
                protocol=cfg.protocol,
                chain_id=listing.chain_id,
                market_id_hex=listing.market_id_hex,
                label=listing.label,
            )
        )
    return refs


@activity.defn
async def list_protocol_markets(cfg: MarketConfigRef) -> list[MarketTickRef]:
    """Run ``yarn <protocol>`` and return its parsed market list.

    Called once per tick per enabled protocol by the workflow, which
    then fans collect/score activities out across the returned refs.
    The yarn output is the source of truth for what markets exist;
    we don't cache it server-side so an operator-side market addition
    surfaces within one tick with no DB write.
    """
    raw = await run_yarn_list(cfg.protocol)
    return _parse_listing_output(raw, cfg)


# ---------------------------------------------------------------------------
# Activity 3 — collector
# ---------------------------------------------------------------------------


def _parse_collector_output(raw: str, ref: MarketTickRef) -> CollectorPayload:
    """Parse the yarn collector output. Raises ``MarketSnapshotPayloadError``."""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = (
            f"collect_market_snapshot[{ref.protocol}/{ref.chain_id}/"
            f"{ref.market_id_hex}]: yarn output was not valid JSON"
        )
        raise MarketSnapshotPayloadError(msg) from exc
    try:
        return CollectorPayload.model_validate(decoded)
    except ValidationError as exc:
        msg = (
            f"collect_market_snapshot[{ref.protocol}/{ref.chain_id}/"
            f"{ref.market_id_hex}]: yarn output did not match expected shape"
        )
        raise MarketSnapshotPayloadError(msg) from exc


@activity.defn
async def collect_market_snapshot(ref: MarketTickRef) -> None:
    """Collect one market's metrics + evidence and persist a snapshot."""
    raw = await run_yarn(
        YarnInvocation(
            protocol=ref.protocol,
            chain_id=ref.chain_id,
            market_id_hex=ref.market_id_hex,
        ),
        mode="collect",
    )
    payload = _parse_collector_output(raw, ref)
    async with session_factory()() as session:
        session.add(
            AutomatedMarketSnapshot(
                market_config_id=ref.market_config_id,
                chain_id=ref.chain_id,
                market_id_hex=ref.market_id_hex,
                label=ref.label,
                kind=MarketSnapshotKind.COLLECT,
                anchors=payload.anchors,
                modifiers=payload.modifiers,
                score=None,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Activity 4 — scorer
# ---------------------------------------------------------------------------


def _parse_scorer_output(raw: str, ref: MarketTickRef) -> ScorerPayload:
    """Parse the yarn scorer output. Raises ``MarketSnapshotPayloadError``."""
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = (
            f"score_market_snapshot[{ref.protocol}/{ref.chain_id}/"
            f"{ref.market_id_hex}]: yarn output was not valid JSON"
        )
        raise MarketSnapshotPayloadError(msg) from exc
    try:
        return ScorerPayload.model_validate(decoded)
    except ValidationError as exc:
        msg = (
            f"score_market_snapshot[{ref.protocol}/{ref.chain_id}/"
            f"{ref.market_id_hex}]: yarn output did not match expected shape"
        )
        raise MarketSnapshotPayloadError(msg) from exc


@activity.defn
async def score_market_snapshot(ref: MarketTickRef) -> None:
    """Score one market and persist a snapshot + PD breakdown.

    Two-transaction shape so a PD-compute failure doesn't lose the
    evidence the operator might want to inspect:

    1. **Transaction A** — write the SCORE snapshot (raw yarn JSON,
       evidence, metrics). Commits on its own.
    2. **Transaction B** — compute PD against the just-committed
       snapshot using the global default weighting profile + shared
       ASSURANCE manual metrics + shared manual ANCHORS metrics
       (protocol-wide or pinned to this market), then write the
       ``MarketScore`` row.
       A :class:`MarketScoringError` (bad pd, negative weight, etc.)
       is logged and swallowed: the snapshot is preserved, just no PD
       lands for this tick.

    Team-scoped weighting profiles and team-scoped ASSURANCE rows are
    deliberately *not* applied here — the persisted ``MarketScore``
    is the global-default view. Team-specific PDs are recomputed at
    read time in the show endpoint.
    """
    raw = await run_yarn(
        YarnInvocation(
            protocol=ref.protocol,
            chain_id=ref.chain_id,
            market_id_hex=ref.market_id_hex,
        ),
        mode="score",
    )
    payload = _parse_scorer_output(raw, ref)
    # The CHECK constraint requires score IS NOT NULL when kind='SCORE'.
    # A missing or empty score block is a validation error — treat the same
    # as a malformed JSON to avoid silently inserting NULL.
    if not payload.score:
        msg = (
            f"score_market_snapshot[{ref.protocol}/{ref.chain_id}/"
            f"{ref.market_id_hex}]: yarn output had no 'score' block"
        )
        raise MarketSnapshotPayloadError(msg)

    # Transaction A — persist the SCORE snapshot.
    async with session_factory()() as session:
        market = await session.get(MarketConfig, ref.market_config_id)
        if market is None:
            # The admin removed the protocol between schedule fire and now;
            # nothing to do this tick.
            return

        snapshot = AutomatedMarketSnapshot(
            market_config_id=ref.market_config_id,
            chain_id=ref.chain_id,
            market_id_hex=ref.market_id_hex,
            label=ref.label,
            kind=MarketSnapshotKind.SCORE,
            anchors=payload.anchors,
            modifiers=payload.modifiers,
            score=payload.score,
        )
        session.add(snapshot)
        await session.commit()
        await session.refresh(snapshot)
        snapshot_id = snapshot.id

    # Transaction B — compute PD against the now-committed snapshot
    # and persist the MarketScore row. Failures here log + swallow so
    # the evidence in transaction A stays on disk.
    async with session_factory()() as session:
        market = await session.get(MarketConfig, ref.market_config_id)
        if market is None:
            return
        profile_entries = await resolve_weighting_profile_entries(
            session,
            protocol=ref.protocol,
            market_config_id=ref.market_config_id,
            chain_id=ref.chain_id,
            market_id_hex=ref.market_id_hex,
            team_id=None,
        )
        assurance_metrics = await load_protocol_assurance(session, market, team_id=None)
        manual_anchors = await load_market_anchors(
            session,
            market,
            chain_id=ref.chain_id,
            market_id_hex=ref.market_id_hex,
            team_id=None,
        )
        try:
            breakdown = compute_market_pd(
                payload.score, profile_entries, assurance_metrics, manual_anchors
            )
        except MarketScoringError as exc:
            activity.logger.warning(
                "score_market_snapshot[%s/%s/%s]: PD compute failed (%s); "
                "snapshot %s retained, no MarketScore row written",
                ref.protocol,
                ref.chain_id,
                ref.market_id_hex,
                exc,
                snapshot_id,
            )
            return

        session.add(
            MarketScore(
                market_config_id=ref.market_config_id,
                chain_id=ref.chain_id,
                market_id_hex=ref.market_id_hex,
                label=ref.label,
                source_amk_snapshot_id=snapshot_id,
                final_pd=Decimal(repr(breakdown.final_pd)),
                anchors_term=Decimal(repr(breakdown.anchors_term)),
                control_term=Decimal(repr(breakdown.control_term)),
                assurance_term=Decimal(repr(breakdown.assurance_term)),
                breakdown=breakdown.breakdown,
            )
        )
        await session.commit()
