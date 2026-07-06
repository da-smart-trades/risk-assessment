# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Pydantic models for the market collector/scorer Temporal pipeline.

Kept deliberately small. The activity layer parses raw yarn JSON into
``CollectorPayload`` / ``ScorerPayload`` and discards anything outside
the spec'd top-level keys so we don't accidentally persist whatever the
LLM decided to emit. Shape changes from the yarn side should land here
first; database writes go through these models.
"""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field


class MarketConfigRef(BaseModel):
    """Identity of one operator-configured protocol, sent workflow → activity.

    Carries the ``market_config`` row's UUID + protocol slug. The
    workflow uses it as the FK for snapshot/score writes and as the
    yarn argv's protocol token. Per-market identifiers (chain id,
    market id hex, label) are *not* here — they're discovered at tick
    time by :func:`list_protocol_markets` and packaged into a separate
    :class:`MarketTickRef` for the per-market collect/score fan-out.
    """

    id: UUID
    protocol: str


class MarketTickRef(BaseModel):
    """Identity of one market being collected/scored on a single tick.

    Built by :func:`list_protocol_markets` from one entry of the yarn
    list output (``{protocol, chainId, marketId, label}``) plus the
    parent ``market_config_id``. Passed to ``collect_market_snapshot``
    and ``score_market_snapshot``; the activity persists every field
    onto the snapshot/score row so downstream UI can render the human
    label without re-running yarn.
    """

    market_config_id: UUID
    protocol: str
    chain_id: int
    market_id_hex: str
    label: str


class ProtocolMarketListing(BaseModel):
    """One entry of the JSON array printed by ``yarn <protocol>``.

    Field names mirror the yarn CLI output exactly (``chainId``,
    ``marketId``) so :func:`model_validate` works on the raw decoded
    JSON; the activity layer remaps these into snake_case before
    persisting.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    protocol: str
    chain_id: int = Field(alias="chainId")
    market_id_hex: str = Field(alias="marketId")
    label: str


class CollectorPayload(BaseModel):
    """Validated shape of the JSON produced by ``yarn <protocol> ...``.

    The collector emits two top-level dicts — ``anchors`` and
    ``modifiers`` — each a category → metric-dict tree (e.g.
    ``anchors.marketSolvency.totalSupplied``). The yarn binary may
    include arbitrary additional top-level keys; we keep these two
    only. Both default to empty dicts so a yarn run that produces only
    one half does not crash the activity.
    """

    model_config = ConfigDict(extra="ignore")

    anchors: dict = Field(default_factory=dict)
    modifiers: dict = Field(default_factory=dict)


class ScorerPayload(BaseModel):
    """Validated shape of the JSON produced by ``yarn <protocol> --score ...``.

    The scorer typically emits a top-level ``score`` dict with ``anchors``
    and ``controlModifiers`` sub-trees, plus optionally the same
    ``anchors`` / ``modifiers`` metric blocks the collector returns. We
    accept all three so the scorer run can stand in for a recent collect
    when the collector tick hasn't run yet for a freshly-added market.
    """

    model_config = ConfigDict(extra="ignore")

    anchors: dict = Field(default_factory=dict)
    modifiers: dict = Field(default_factory=dict)
    score: dict = Field(default_factory=dict)
