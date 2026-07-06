# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Typed configuration for the ``alert_history.context`` JSONB column.

Unlike ``rule_config`` and ``integration_config``, the history context has a
single uniform shape — no discriminator. The struct documents what the
evaluator must record on every event so downstream consumers (audit logs,
debugging tools) get stable typing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct

__all__ = (
    "AlertHistoryContext",
    "dump_history_context",
    "parse_history_context",
)


class AlertHistoryContext(CamelizedBaseStruct):
    """Provenance attached to every ``alert_history`` row.

    Captured by the evaluator activity at write time. Sufficient to reconstruct
    *which snapshot drove this event* for post-incident analysis without
    joining against the source metric tables (which roll over).
    """

    snapshot_id: UUID | None = None
    """ID of the metric snapshot row used to evaluate the rule. Nullable for
    ``ERROR`` rows that were emitted because no fresh snapshot was available."""

    snapshot_table: str | None = None
    """Source metric table name (e.g. ``finality_ethereum``). Nullable for
    ``ERROR`` rows."""

    evaluator_version: str = "1"
    """Version stamp of the evaluator code that produced this row. Bump when
    rule semantics change so older rows can be interpreted correctly."""

    notes: str | None = None
    """Free-form context, e.g. error message details for ``ERROR`` rows."""


def parse_history_context(raw: dict[str, Any]) -> AlertHistoryContext:
    """JSONB ``dict`` → typed Struct (read path).

    Raises:
        msgspec.ValidationError: If ``raw`` does not match the expected shape.
    """
    return msgspec.convert(raw, type=AlertHistoryContext)


def dump_history_context(
    context: AlertHistoryContext | dict[str, Any],
) -> dict[str, Any]:
    """Typed Struct (or raw dict) → validated JSONB ``dict`` (write path).

    Raises:
        msgspec.ValidationError: If ``context`` does not match the expected shape.
    """
    typed = (
        context
        if isinstance(context, AlertHistoryContext)
        else msgspec.convert(context, type=AlertHistoryContext)
    )
    return msgspec.to_builtins(typed)  # type: ignore[no-any-return]
