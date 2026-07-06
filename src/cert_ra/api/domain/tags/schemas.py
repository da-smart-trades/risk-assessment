# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct

__all__ = ("Tag", "TagCreate", "TagUpdate")


class Tag(CamelizedBaseStruct):
    """Tag response schema."""

    id: UUID
    slug: str
    name: str
    description: str | None = None


class TagCreate(CamelizedBaseStruct):
    """Create a new tag."""

    name: str
    description: str | None = None


class TagUpdate(CamelizedBaseStruct, omit_defaults=True):
    """Update a tag."""

    name: str | None | msgspec.UnsetType = msgspec.UNSET
    description: str | None | msgspec.UnsetType = msgspec.UNSET
