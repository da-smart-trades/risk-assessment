# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Typed configurations for the polymorphic ``alert.target_config`` JSONB column.

The discriminator lives in ``alert.target_kind`` (a separate column) and the
shapes are validated by the structs registered in ``TARGET_VALIDATORS``. The
same pattern as ``rules.py`` and ``integrations.py``.

Adding a new target kind requires:

1. Adding the variant to ``cert_ra.types.AlertTargetKind``.
2. Defining a new ``CamelizedBaseStruct`` here.
3. Registering it in ``TARGET_VALIDATORS``.
4. Registering a corresponding ``ValueSource`` in
   ``cert_ra.alerts._value_sources``.

No database migration is needed — the JSONB column accepts the new shape as
long as the validator passes.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID  # noqa: TC003 — runtime type for msgspec

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import AlertTargetKind, ChainType, MetricType, TokenType

__all__ = (
    "TARGET_VALIDATORS",
    "MarketAnchorTargetConfig",
    "MarketControlTargetConfig",
    "MarketPdTargetConfig",
    "MetricTargetConfig",
    "TargetConfig",
    "dump_target_config",
    "parse_target_config",
)


class MetricTargetConfig(CamelizedBaseStruct, tag="METRIC"):
    """Blockchain-metric target — the original ``MetricType`` + chain + token shape.

    ``chain`` and ``token`` are optional selectors that match the historical
    ``(chain, token)`` columns on the alert row; together with ``metric_type``
    they identify a row in the value-source registry that points at one of the
    snapshot tables (``throughput``, ``finality_*``, ``decentralization``, …).
    """

    metric_type: MetricType
    chain: ChainType | None = None
    token: TokenType | None = None


class MarketPdTargetConfig(CamelizedBaseStruct, tag="MARKET_PD"):
    """Whole-market PD target — ``market_score.final_pd`` for one specific market.

    Identifies the market by the full ``(market_config_id, chain_id, market_id_hex)``
    triple; the evaluator reads the latest row matching all three and
    compares ``final_pd``.
    """

    market_config_id: UUID
    chain_id: int
    market_id_hex: str


class MarketAnchorTargetConfig(CamelizedBaseStruct, tag="MARKET_ANCHOR"):
    """Per-anchor target — one entry of ``automated_market_snapshot.score['anchors']``.

    The value extracted at evaluation time is
    ``snapshot.score['anchors'][sub_category]['pd']``. If the latest SCORE
    snapshot for the market lacks ``sub_category``, the evaluator emits an
    ERROR row rather than treating it as zero.
    """

    market_config_id: UUID
    chain_id: int
    market_id_hex: str
    sub_category: str


class MarketControlTargetConfig(CamelizedBaseStruct, tag="MARKET_CONTROL"):
    """Per-control target — one entry of ``automated_market_snapshot.score['controlModifiers']``.

    Mirrors :class:`MarketAnchorTargetConfig` but extracts
    ``snapshot.score['controlModifiers'][sub_category]['multiplier']``.
    """

    market_config_id: UUID
    chain_id: int
    market_id_hex: str
    sub_category: str


TargetConfig = (
    MetricTargetConfig
    | MarketPdTargetConfig
    | MarketAnchorTargetConfig
    | MarketControlTargetConfig
)
"""Discriminated union of all currently-implemented target configurations.

Exposed via ``Alert.target_config`` so OpenAPI emits a discriminated union and
the generated TypeScript surfaces one of the typed variants instead of an
opaque dict.
"""


TARGET_VALIDATORS: dict[AlertTargetKind, type[CamelizedBaseStruct]] = {
    AlertTargetKind.METRIC: MetricTargetConfig,
    AlertTargetKind.MARKET_PD: MarketPdTargetConfig,
    AlertTargetKind.MARKET_ANCHOR: MarketAnchorTargetConfig,
    AlertTargetKind.MARKET_CONTROL: MarketControlTargetConfig,
}


def parse_target_config(kind: AlertTargetKind, raw: dict[str, Any]) -> TargetConfig:
    """JSONB ``dict`` → typed Struct (read path).

    Args:
        kind: Discriminator value from ``alert.target_kind``.
        raw: Raw dict pulled from the JSONB column.

    Returns:
        A concrete struct of the variant matching ``kind``.

    Raises:
        ValueError: If ``kind`` has no registered validator.
        msgspec.ValidationError: If ``raw`` does not match the expected shape.
    """
    schema = TARGET_VALIDATORS.get(kind)
    if schema is None:
        msg = f"No validator registered for AlertTargetKind.{kind.name}."
        raise ValueError(msg)
    return cast("TargetConfig", msgspec.convert(raw, type=schema))


def dump_target_config(
    kind: AlertTargetKind,
    config: TargetConfig | dict[str, Any],
) -> dict[str, Any]:
    """Typed Struct (or raw dict) → validated JSONB ``dict`` (write path).

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
    schema = TARGET_VALIDATORS.get(kind)
    if schema is None:
        msg = f"No validator registered for AlertTargetKind.{kind.name}."
        raise ValueError(msg)
    typed = (
        config if isinstance(config, schema) else msgspec.convert(config, type=schema)
    )
    return cast("dict[str, Any]", msgspec.to_builtins(typed))
