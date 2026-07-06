# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Market config request / response schemas + Inertia page props."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import ProtocolType

__all__ = (
    "AdminMarketConfigCreatePage",
    "AdminMarketConfigEditPage",
    "AdminMarketConfigListPage",
    "MarketConfig",
    "MarketConfigCreate",
    "MarketConfigUpdate",
)


class MarketConfig(CamelizedBaseStruct):
    """Read-shape for one operator-registered protocol row.

    Per-market identifiers (chain id, market id hex, label) are not
    here — those are runtime-discovered by ``yarn <protocol>`` and
    persisted onto the snapshot/score/favorite rows that reference
    this protocol.
    """

    id: UUID
    protocol: str
    enabled: bool
    assurance_protocol: ProtocolType | None = None
    """The ``ProtocolType`` whose ASSURANCE manual metrics apply to this
    protocol, or ``None`` when it has none."""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None


class MarketConfigCreate(CamelizedBaseStruct):
    """Create payload.

    ``protocol`` is lowercased server-side, so the admin may type any
    case in the form. ``enabled`` defaults to ``True`` — the
    collector/scorer workflow starts ticking the protocol immediately
    unless explicitly disabled. ``assurance_protocol`` maps the yarn slug
    to a ``ProtocolType`` for ASSURANCE lookups; leave it ``None`` when the
    protocol has no ASSURANCE manual metrics.
    """

    protocol: str
    enabled: bool = True
    assurance_protocol: ProtocolType | None = None


class MarketConfigUpdate(CamelizedBaseStruct, omit_defaults=True):
    """Partial update.

    ``enabled`` and ``assurance_protocol`` may change after creation.
    ``protocol`` is the natural key and is immutable — switching protocols
    is "disable + add new" rather than rewrite-in-place.
    """

    enabled: bool | msgspec.UnsetType = msgspec.UNSET
    assurance_protocol: ProtocolType | None | msgspec.UnsetType = msgspec.UNSET


class AdminMarketConfigListPage(CamelizedBaseStruct):
    """Inertia page props for ``GET /admin/market-config/``."""

    markets: list[MarketConfig]
    total: int


class AdminMarketConfigCreatePage(CamelizedBaseStruct):
    """Inertia page props for ``GET /admin/market-config/create``."""

    protocol_options: list[str] = msgspec.field(default_factory=list)
    """``ProtocolType`` values offered for the assurance-mapping dropdown."""


class AdminMarketConfigEditPage(CamelizedBaseStruct):
    """Inertia page props for ``GET /admin/market-config/{id}``."""

    market: MarketConfig
    protocol_options: list[str] = msgspec.field(default_factory=list)
