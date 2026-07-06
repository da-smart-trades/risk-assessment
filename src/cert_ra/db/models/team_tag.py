# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from advanced_alchemy.base import orm_registry
from sqlalchemy import Column, ForeignKey, Table

team_tag: Table = Table(
    "team_tag",
    orm_registry.metadata,
    Column("team_id", ForeignKey("team.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tag.id", ondelete="CASCADE"), primary_key=True),
)
