# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator


def tz_now() -> datetime:
    """Returns the current time in UTC."""
    return datetime.now(UTC)


def datetime_with_tz(dt: datetime) -> datetime:
    """Ensures that a datetime object is timezone-aware in UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def type_wraps[T, **P](
    cls: Callable[P, T],  # noqa: ARG001
    /,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """A no-op decorator that preserves the type signature of the wrapped function."""

    def inner(func: Callable[P, T]) -> Callable[P, T]:
        return func

    return inner


def hex_str_to_int(value: str | int) -> int:
    """Convert a hexadecimal string or integer to an integer."""
    if isinstance(value, int):
        return value

    return int(value, 16)


HexInt = Annotated[int, BeforeValidator(hex_str_to_int)]


PACKAGE_ROOT = Path(__file__).parent
