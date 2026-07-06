# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Favorite-metric request/response schemas."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import (  # noqa: TC001
    ChainType,
    MetricType,
    TokenType,
)

__all__ = (
    "Favorite",
    "FavoriteAutoCreate",
    "FavoriteManualCreate",
    "FavoriteMarketCreate",
    "ResolvedFavorite",
)


class Favorite(CamelizedBaseStruct):
    """A single favorite, returned by the list/create endpoints.

    Exactly one of (``metric_type``, ``manual_metric_id``,
    ``market_config_id``) is set, matching the XOR enforced at the
    database layer. When ``market_config_id`` is set, the cached
    ``favorite_chain_id`` / ``favorite_market_id_hex`` / ``favorite_label``
    pin the specific market within the protocol.
    """

    id: UUID
    created_at: datetime
    dashboard_id: UUID
    metric_type: MetricType | None = None
    chain: ChainType | None = None
    token: TokenType | None = None
    manual_metric_id: UUID | None = None
    market_config_id: UUID | None = None
    favorite_chain_id: int | None = None
    favorite_market_id_hex: str | None = None
    favorite_label: str | None = None


class FavoriteAutoCreate(CamelizedBaseStruct):
    """Favorite an auto-collected metric series, addressed by tuple."""

    metric_type: MetricType
    chain: ChainType | None = None
    token: TokenType | None = None


class FavoriteManualCreate(CamelizedBaseStruct):
    """Favorite a single ``ManualMetric`` row.

    The referenced row must have ``category == PROTOCOL_SCORE`` — the service
    layer rejects the request otherwise.
    """

    manual_metric_id: UUID


class FavoriteMarketCreate(CamelizedBaseStruct):
    """Favorite a specific market under a registered protocol.

    ``market_config_id`` identifies the protocol row;
    ``favorite_chain_id`` + ``favorite_market_id_hex`` pin the specific
    market within it; ``favorite_label`` caches the yarn-output label
    so the dashboard card can render it without re-running yarn. The
    card surfaces the latest ``MarketScore.final_pd`` as the value.
    The service layer rejects disabled protocols.
    """

    market_config_id: UUID
    favorite_chain_id: int
    favorite_market_id_hex: str
    favorite_label: str


class ResolvedFavorite(CamelizedBaseStruct):
    """A favorite with a rendered label + latest value, for the dashboard card.

    ``value`` is ``None`` when no source is registered for the metric (the
    dashboard shows ``—``). For PROTOCOL_SCORE manual metrics, ``value`` is the
    formatted ``risk_score``.

    For per-chain finality favorites, ``primary_label``/``secondary_label`` and
    ``secondary_value`` are populated so the card can render the headline metric
    alongside a complementary one; ``description`` carries the chain-specific
    explanation shown in the card's hover tooltip.
    """

    id: UUID
    label: str
    value: str | None
    href: str
    metric_type: MetricType | None = None
    chain: ChainType | None = None
    token: TokenType | None = None
    manual_metric_id: UUID | None = None
    market_config_id: UUID | None = None
    primary_label: str | None = None
    secondary_label: str | None = None
    secondary_value: str | None = None
    description: str | None = None
    # Card classification for the dashboard icon: "chain" | "protocol" |
    # "market" | "token". Always set by the resolver.
    card_kind: str | None = None
