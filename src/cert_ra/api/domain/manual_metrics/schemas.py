# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import (  # noqa: TC001
    ChainType,
    MetricCategory,
    ProtocolType,
    TokenType,
)

__all__ = (
    "ManualMetric",
    "ManualMetricCreate",
    "ManualMetricGroup",
    "ManualMetricListPage",
    "ManualMetricPublish",
    "ManualMetricUpdate",
    "TeamOption",
)


SHARED_SLUG = "shared"
"""Reserved slug for the synthetic "Shared (platform-wide)" scope option."""


class TeamOption(CamelizedBaseStruct):
    """A scope option for the manual-metrics team selector.

    Used to populate the team filter on the read page and the team selector
    in the create form. The synthetic shared option uses ``team_id=None`` and
    ``team_slug=SHARED_SLUG``.
    """

    team_id: UUID | None
    team_slug: str
    team_name: str
    is_shared: bool
    can_edit: bool


class ManualMetric(CamelizedBaseStruct):
    """Manual metric response schema."""

    id: UUID
    name: str
    desc: str
    category: MetricCategory
    risk_score: int | None
    is_published: bool = False
    entity_type: str = ""  # one of "chain" / "token" / "protocol"
    chain: ChainType | None = None
    token: TokenType | None = None
    protocol: ProtocolType | None = None
    sub_category: str | None = None
    value: str | None = None
    notes: str | None = None
    deleted: bool = False
    # Optional pin to one discovered market (ANCHORS metrics only). Both
    # null ⇒ the row applies to every market of its protocol.
    market_chain_id: int | None = None
    market_id_hex: str | None = None
    team_id: UUID | None = None
    team_slug: str | None = None
    team_name: str | None = None
    can_edit: bool = False
    can_publish: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None


class ManualMetricCreate(CamelizedBaseStruct):
    """Create payload — scope and audit fields are derived server-side.

    Scope is derived from ``current_user``: operator editors always create
    shared metrics; non-operator team editors create metrics for their
    current team (or their only team if unambiguous). The new row always
    lands in draft state — call the publish endpoint to make it visible.

    ``created_by`` / ``updated_by`` are injected from ``current_user``.
    """

    name: str
    desc: str
    category: MetricCategory
    chain: ChainType | None = None
    token: TokenType | None = None
    protocol: ProtocolType | None = None
    sub_category: str | None = None
    value: str | None = None
    risk_score: int | None = None
    notes: str | None = None
    # Optional pin to one discovered market. Valid only for a
    # protocol-scoped ANCHORS metric; both must be set together. Leaving
    # them null makes the metric apply to every market of its protocol.
    market_chain_id: int | None = None
    market_id_hex: str | None = None


class ManualMetricPublish(CamelizedBaseStruct):
    """Toggle the published state of a manual metric."""

    is_published: bool


class ManualMetricUpdate(CamelizedBaseStruct, omit_defaults=True):
    """Partial update — every field optional; ``updated_by`` is injected.

    The following are intentionally NOT fields, as they are immutable after
    creation: ``team_id`` (scope), ``chain`` / ``token`` / ``protocol``
    (entity), ``category`` (constrained by entity-type), and
    ``is_published`` (use the publish endpoint).
    """

    name: str | msgspec.UnsetType = msgspec.UNSET
    desc: str | msgspec.UnsetType = msgspec.UNSET
    sub_category: str | None | msgspec.UnsetType = msgspec.UNSET
    value: str | None | msgspec.UnsetType = msgspec.UNSET
    risk_score: int | None | msgspec.UnsetType = msgspec.UNSET
    notes: str | None | msgspec.UnsetType = msgspec.UNSET


class ManualMetricGroup(CamelizedBaseStruct):
    """A category (or category/sub-category) bucket for the read view."""

    category: MetricCategory
    sub_category: str | None
    items: list[ManualMetric]


class ManualMetricListPage(CamelizedBaseStruct):
    """Inertia page props for the read view (grouped) and the operator list."""

    groups: list[ManualMetricGroup]
    items: list[ManualMetric]
    total: int
    is_operator_editor: bool
    teams: list[TeamOption] = msgspec.field(default_factory=list)
    selected_team_slug: str | None = None
    selected_category: MetricCategory | None = None
