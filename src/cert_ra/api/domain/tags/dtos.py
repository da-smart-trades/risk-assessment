# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from advanced_alchemy.extensions.litestar.dto import SQLAlchemyDTO
from app.lib import dto  # pyright: ignore[reportMissingImports]

from cert_ra.db.models import Tag

__all__ = ["TagCreateDTO", "TagDTO", "TagUpdateDTO"]


class TagDTO(SQLAlchemyDTO[Tag]):
    config = dto.config(
        max_nested_depth=0, exclude={"created_at", "updated_at", "teams"}
    )


class TagCreateDTO(SQLAlchemyDTO[Tag]):
    config = dto.config(
        max_nested_depth=0, exclude={"id", "created_at", "updated_at", "teams"}
    )


class TagUpdateDTO(SQLAlchemyDTO[Tag]):
    config = dto.config(
        max_nested_depth=0,
        exclude={"id", "created_at", "updated_at", "teams"},
        partial=True,
    )
