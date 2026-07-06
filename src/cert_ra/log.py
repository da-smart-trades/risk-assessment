# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import sys
from functools import lru_cache
from typing import TYPE_CHECKING

from litestar.serialization import encode_json

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any

    from structlog.types import EventDict, WrappedLogger
    from structlog.typing import Processor


@lru_cache
def is_tty() -> bool:
    """Check if either stdout or stderr is a TTY."""
    return bool(sys.stderr.isatty() or sys.stdout.isatty())


def structlog_json_serializer(value: EventDict, **_: Any) -> bytes:  # noqa: ANN401
    """Serialize log event to JSON bytes."""
    return encode_json(value)


def stdlib_json_serializer(
    value: EventDict,
    **_: Any,  # noqa: ANN401
) -> str:  # pragma: no cover
    """Serialize log event to JSON string."""
    return encode_json(value).decode()


class EventFilter:
    """Remove keys from the log event.

    Add an instance to the processor chain.

    Examples:
        structlog.configure(
            ...,
            processors=[
                ...,
                EventFilter(["color_message"]),
                ...,
            ]
        )
    """

    def __init__(self, filter_keys: Iterable[str]) -> None:
        """Event filter.

        Args:
        filter_keys: Iterable of string keys to be excluded from the log event.
        """
        self.filter_keys = filter_keys

    def __call__(self, _: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        """Receive the log event, and filter keys.

        Args:
            _ ():
            __ ():
            event_dict (): The data to be logged.

        Returns:
            The log event with any key in `self.filter_keys` removed.
        """
        for key in self.filter_keys:
            event_dict.pop(key, None)
        return event_dict


def structlog_processors(as_json: bool) -> list[Processor]:  # noqa: FBT001
    """Set the default processors for structlog.

    Returns:
        An optional list of processors.
    """
    try:
        import structlog
        from structlog.dev import RichTracebackFormatter

        if as_json:
            return [
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.format_exc_info,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.JSONRenderer(serializer=structlog_json_serializer),
            ]
        return [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=RichTracebackFormatter(
                    max_frames=1, show_locals=False, width=80
                ),
            ),
        ]
    except ImportError:
        return []


def stdlib_logger_processors(as_json: bool) -> list[Processor]:  # noqa: FBT001
    """Set the default processors for structlog stdlib.

    Returns:
        An optional list of processors.
    """
    try:
        import structlog
        from structlog.dev import RichTracebackFormatter

        if as_json:
            return [
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.stdlib.add_log_level,
                structlog.stdlib.ExtraAdder(),
                EventFilter(["color_message"]),
                structlog.processors.EventRenamer("message"),
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(serializer=stdlib_json_serializer),
            ]
        return [
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.stdlib.ExtraAdder(),
            EventFilter(["color_message"]),
            EventFilter(["message"]),
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=RichTracebackFormatter(
                    max_frames=1, show_locals=False, width=80
                ),
            ),
        ]
    except ImportError:
        return []
