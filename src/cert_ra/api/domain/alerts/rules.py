# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Typed configurations for the polymorphic ``alert.rule_config`` JSONB column.

Each rule kind has a corresponding msgspec struct registered in
``_RULE_VALIDATORS``. The discriminator lives in ``alert.rule_kind`` (a separate
column) rather than embedded in the JSONB, so we look up the validator by kind
and round-trip through ``parse_rule_config`` / ``dump_rule_config`` helpers.

Adding a new rule kind requires:

1. Adding the variant to ``cert_ra.types.AlertRuleKind``.
2. Defining a new ``CamelizedBaseStruct`` here.
3. Registering it in ``_RULE_VALIDATORS``.

No database migration is needed — the JSONB column accepts the new shape as
long as the validator passes.
"""

from __future__ import annotations

from typing import Any, Literal, cast

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import AlertRuleKind

__all__ = (
    "RULE_VALIDATORS",
    "Direction",
    "RateOfChangeRuleConfig",
    "RuleConfig",
    "StddevDeviationRuleConfig",
    "ThresholdOperator",
    "ThresholdRuleConfig",
    "dump_rule_config",
    "parse_rule_config",
)


ThresholdOperator = str
"""Type alias for the threshold operator. Constrained to ``_OPERATORS`` at runtime."""

Direction = Literal["above", "below", "both"]
"""Shared direction selector for RoC + stddev rules.

``above``: fires only when the value moves up past the threshold (rise / spike).
``below``: fires only on downward moves (drop). ``both``: fires regardless of
direction (magnitude-only comparison). The value's interpretation depends on
the rule kind — see :class:`RateOfChangeRuleConfig` and
:class:`StddevDeviationRuleConfig`.
"""

_OPERATORS: frozenset[str] = frozenset({">", ">=", "<", "<=", "==", "!="})
_DIRECTIONS: frozenset[str] = frozenset({"above", "below", "both"})


class ThresholdRuleConfig(CamelizedBaseStruct, tag="THRESHOLD"):
    """Fire when the metric value crosses ``value`` according to ``operator``.

    ``window_seconds = 0`` means evaluate the latest snapshot only. Non-zero
    values are reserved for future "sustained over a window" semantics; the
    MVP evaluator treats any positive value as the latest snapshot and logs a
    warning.

    The ``tag`` mirrors ``AlertRuleKind.THRESHOLD`` so msgspec can serialise
    and deserialise this struct as a member of the ``RuleConfig`` union. The
    tag also ends up in the JSONB column (alongside ``alert.rule_kind``); the
    duplicated information is harmless and keeps round-trip serialisation
    automatic.
    """

    operator: ThresholdOperator
    value: float
    window_seconds: int = 0

    def __post_init__(self) -> None:
        """Validate operator and window_seconds invariants."""
        if self.operator not in _OPERATORS:
            msg = (
                f"Invalid threshold operator {self.operator!r}; "
                f"must be one of {sorted(_OPERATORS)}."
            )
            raise ValueError(msg)
        if self.window_seconds < 0:
            msg = "window_seconds must be non-negative."
            raise ValueError(msg)


class RateOfChangeRuleConfig(CamelizedBaseStruct, tag="RATE_OF_CHANGE"):
    """Fire when the metric percent-change over ``window_seconds`` exceeds ``delta_pct``.

    ``delta_pct`` is a magnitude (must be ≥ 0). ``direction`` selects which
    crossing fires:

    * ``above``: ``pct_change > delta_pct`` (rise / spike).
    * ``below``: ``pct_change < -delta_pct`` (drop).
    * ``both``: ``|pct_change| > delta_pct`` (either direction).

    ``window_seconds`` must be strictly positive — the evaluator picks the
    historical sample closest to ``now - window_seconds`` and computes
    ``(current - past) / |past| x 100``. A zero baseline raises an evaluator
    ERROR (no meaningful percentage exists).

    See ``ThresholdRuleConfig`` for the rationale behind the ``tag`` value.
    """

    delta_pct: float
    window_seconds: int
    direction: Direction = "both"

    def __post_init__(self) -> None:
        """Validate magnitude, window, and direction."""
        if self.window_seconds <= 0:
            msg = "window_seconds must be positive for a rate-of-change rule."
            raise ValueError(msg)
        if self.delta_pct < 0:
            msg = (
                f"delta_pct must be a non-negative magnitude (got {self.delta_pct}); "
                f"use direction='below' to fire on drops."
            )
            raise ValueError(msg)
        if self.direction not in _DIRECTIONS:
            msg = (
                f"Invalid direction {self.direction!r}; "
                f"must be one of {sorted(_DIRECTIONS)}."
            )
            raise ValueError(msg)


class StddevDeviationRuleConfig(CamelizedBaseStruct, tag="STDDEV_DEVIATION"):
    """Fire when the latest value is more than ``multiplier x stddev`` from the historical mean.

    The evaluator loads samples observed within ``lookback_seconds`` of the
    current snapshot, computes the population mean and standard deviation, and
    compares the latest value to the band ``mean +/- multiplier x stddev``:

    * ``above``: ``value > mean + multiplier x stddev``.
    * ``below``: ``value < mean - multiplier x stddev``.
    * ``both``: ``|value - mean| > multiplier x stddev``.

    A short series (fewer than ``MIN_STDDEV_SAMPLES`` samples) yields an ERROR
    history row rather than silently passing. A flat series (``stddev = 0``)
    yields ``not triggered`` — there is no meaningful deviation to compare to.
    """

    multiplier: float
    lookback_seconds: int
    direction: Direction = "both"

    def __post_init__(self) -> None:
        """Validate multiplier, lookback, and direction."""
        if self.multiplier <= 0:
            msg = f"multiplier must be > 0 (got {self.multiplier})."
            raise ValueError(msg)
        if self.lookback_seconds <= 0:
            msg = "lookback_seconds must be positive for a stddev-deviation rule."
            raise ValueError(msg)
        if self.direction not in _DIRECTIONS:
            msg = (
                f"Invalid direction {self.direction!r}; "
                f"must be one of {sorted(_DIRECTIONS)}."
            )
            raise ValueError(msg)


RuleConfig = ThresholdRuleConfig | RateOfChangeRuleConfig | StddevDeviationRuleConfig
"""Discriminated union of all currently-implemented rule configurations.

Used as the response-schema type for ``Alert.rule_config`` so OpenAPI emits a
discriminated union and the generated TypeScript exposes a typed value rather
than ``Record<string, unknown>``.
"""


RULE_VALIDATORS: dict[AlertRuleKind, type[CamelizedBaseStruct]] = {
    AlertRuleKind.THRESHOLD: ThresholdRuleConfig,
    AlertRuleKind.RATE_OF_CHANGE: RateOfChangeRuleConfig,
    AlertRuleKind.STDDEV_DEVIATION: StddevDeviationRuleConfig,
    # AlertRuleKind.COMPOSITE intentionally omitted — reserved for v2.
}


def parse_rule_config(kind: AlertRuleKind, raw: dict[str, Any]) -> RuleConfig:
    """JSONB ``dict`` → typed Struct.

    Used on the read path (services / activities / response builders).

    Args:
        kind: Discriminator value from ``alert.rule_kind``.
        raw: Raw dict pulled from the JSONB column.

    Returns:
        A concrete struct of the variant matching ``kind``.

    Raises:
        ValueError: If ``kind`` has no registered validator.
        msgspec.ValidationError: If ``raw`` does not match the expected shape.
    """
    schema = RULE_VALIDATORS.get(kind)
    if schema is None:
        msg = f"No validator registered for AlertRuleKind.{kind.name}."
        raise ValueError(msg)
    return cast("RuleConfig", msgspec.convert(raw, type=schema))


def dump_rule_config(
    kind: AlertRuleKind,
    config: RuleConfig | dict[str, Any],
) -> dict[str, Any]:
    """Typed Struct (or raw dict) → validated JSONB ``dict``.

    Used on the write path (services). Always validates before persisting.

    Args:
        kind: Discriminator value to validate against.
        config: Either a typed struct or a raw dict to be coerced.

    Returns:
        A dict suitable for JSONB persistence (camelCase per
        ``CamelizedBaseStruct``).

    Raises:
        ValueError: If ``kind`` has no registered validator.
        msgspec.ValidationError: If ``config`` does not match the expected shape.
    """
    schema = RULE_VALIDATORS.get(kind)
    if schema is None:
        msg = f"No validator registered for AlertRuleKind.{kind.name}."
        raise ValueError(msg)
    typed = (
        config if isinstance(config, schema) else msgspec.convert(config, type=schema)
    )
    return cast("dict[str, Any]", msgspec.to_builtins(typed))
