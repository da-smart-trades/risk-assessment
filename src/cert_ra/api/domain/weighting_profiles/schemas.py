# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Weighting profile request / response schemas + Inertia page props."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from decimal import Decimal
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import (  # noqa: TC001
    WeightingProfileEntryCategory,
    WeightingProfileScope,
)

__all__ = (
    "AdminWeightingProfileCreatePage",
    "AdminWeightingProfileEditPage",
    "AdminWeightingProfileListPage",
    "AvailableSubCategoriesPage",
    "TeamScopeOption",
    "WeightingProfile",
    "WeightingProfileCreate",
    "WeightingProfileEntry",
    "WeightingProfileEntryCreate",
    "WeightingProfileUpdate",
)


# Convenience: 1.0 as a Decimal so msgspec serializes it consistently.
_DEFAULT_WEIGHT = Decimal("1.0")


class WeightingProfileEntry(CamelizedBaseStruct):
    """One ``(category, sub_category, weight)`` override."""

    id: UUID | None
    category: WeightingProfileEntryCategory
    sub_category: str
    weight: Decimal


class WeightingProfileEntryCreate(CamelizedBaseStruct):
    """Create / replace payload for one entry inside a profile."""

    category: WeightingProfileEntryCategory
    sub_category: str
    weight: Decimal = _DEFAULT_WEIGHT


class WeightingProfile(CamelizedBaseStruct):
    """Read-shape for one weighting_profile row + its entries."""

    id: UUID
    team_id: UUID | None
    team_slug: str | None
    team_name: str | None
    name: str
    scope: WeightingProfileScope
    target_protocol: str | None
    target_market_config_id: UUID | None
    target_chain_id: int | None
    target_market_id_hex: str | None
    target_market_label: str | None
    entries: list[WeightingProfileEntry]
    can_edit: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None


class WeightingProfileCreate(CamelizedBaseStruct):
    """Create payload.

    ``team_id`` is derived server-side: superusers can target the global
    default (``team_id=None``) by setting ``is_global=True``; team
    admins/editors otherwise get their team's id stamped from the
    session.

    For ``scope=PROTOCOL``: ``target_protocol`` is required and the
    market columns must be unset. For ``scope=MARKET``:
    ``target_market_config_id`` (the protocol row) plus
    ``target_chain_id`` + ``target_market_id_hex`` + ``target_label``
    (the specific market within the protocol) are all required.
    """

    name: str
    scope: WeightingProfileScope
    target_protocol: str | None = None
    target_market_config_id: UUID | None = None
    target_chain_id: int | None = None
    target_market_id_hex: str | None = None
    target_label: str | None = None
    is_global: bool = False
    entries: list[WeightingProfileEntryCreate] = msgspec.field(default_factory=list)


class WeightingProfileUpdate(CamelizedBaseStruct, omit_defaults=True):
    """Partial update.

    ``name``, ``target_*`` (within the same scope), and ``entries`` may
    change. ``scope`` itself is immutable — a profile changing scope is
    a different profile, so the workflow is "delete + recreate".
    Likewise ``team_id`` is immutable after creation.

    Passing ``entries`` replaces the full set atomically (orphan entries
    are deleted via ``cascade='all, delete-orphan'`` on the
    relationship).
    """

    name: str | msgspec.UnsetType = msgspec.UNSET
    target_protocol: str | None | msgspec.UnsetType = msgspec.UNSET
    target_market_config_id: UUID | None | msgspec.UnsetType = msgspec.UNSET
    target_chain_id: int | None | msgspec.UnsetType = msgspec.UNSET
    target_market_id_hex: str | None | msgspec.UnsetType = msgspec.UNSET
    target_label: str | None | msgspec.UnsetType = msgspec.UNSET
    entries: list[WeightingProfileEntryCreate] | msgspec.UnsetType = msgspec.UNSET


class TeamScopeOption(CamelizedBaseStruct):
    """One option in the team-scope picker on the admin form.

    ``team_id=None`` represents the global default; the synthetic option
    is present only when the current user is a superuser. Mirrors the
    pattern used by manual-metrics admin.
    """

    team_id: UUID | None
    team_slug: str
    team_name: str
    is_global: bool
    can_edit: bool


class AdminWeightingProfileListPage(CamelizedBaseStruct):
    """Inertia page props for ``GET /admin/weighting-profiles/``."""

    profiles: list[WeightingProfile]
    total: int
    scopes: list[TeamScopeOption]
    is_operator_editor: bool


class AdminWeightingProfileCreatePage(CamelizedBaseStruct):
    """Inertia page props for ``GET /admin/weighting-profiles/create``.

    ``scopes`` enumerates the team buckets the current user is allowed
    to create profiles in. ``markets`` is the catalogue of currently
    enabled market_config rows so the form can populate its target
    dropdown.
    """

    scopes: list[TeamScopeOption]
    markets: list[dict]
    is_operator_editor: bool


class AdminWeightingProfileEditPage(CamelizedBaseStruct):
    """Inertia page props for ``GET /admin/weighting-profiles/{id}``."""

    profile: WeightingProfile
    scopes: list[TeamScopeOption]
    markets: list[dict]
    is_operator_editor: bool


class AvailableSubCategoriesPage(CamelizedBaseStruct):
    """Response for the cascading-dropdown helper endpoint.

    Returned by ``GET /api/weighting-profiles/available-sub-categories``.
    The list is sourced dynamically from the most recent
    automated_market_snapshot of ``kind='SCORE'`` (for ``anchor`` and
    ``control`` categories) or from distinct ``ManualMetric.sub_category``
    on protocol-scoped ASSURANCE rows (for ``assurance``).
    """

    sub_categories: list[str]
