# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Pure-function decentralization math helpers.

All inputs are non-negative ``float`` stakes. Zero-stake entries are expected
to have been filtered out by the caller.
"""

from __future__ import annotations

import math

# BFT consensus thresholds. Use exact fractions rather than 0.333 / 0.666 so
# the cumulative check isn't biased low at the boundary.
LIVENESS_THRESHOLD = 1.0 / 3.0
SAFETY_THRESHOLD = 2.0 / 3.0


def nakamoto_coefficient(
    stakes: list[float], threshold: float = LIVENESS_THRESHOLD
) -> int:
    """Minimum validator count whose combined stake exceeds ``threshold`` of total.

    Pass ``LIVENESS_THRESHOLD`` (1/3) for the halting threshold or
    ``SAFETY_THRESHOLD`` (2/3) for finality-violation threshold. Uses strict
    ``>`` to match the academic definition (Srinivasan & Lee).
    """
    if not stakes:
        return 0

    sorted_stakes = sorted(stakes, reverse=True)
    target = sum(sorted_stakes) * threshold

    cumulative = 0.0
    for count, stake in enumerate(sorted_stakes, start=1):
        cumulative += stake
        if cumulative > target:
            return count
    return len(sorted_stakes)


def group_by_entity(
    stakes: list[tuple[str, float]], mapping: dict[str, str]
) -> tuple[list[tuple[str, float]], float]:
    """Collapse ``(validator_id, stake)`` rows by entity using ``mapping``.

    Validators absent from ``mapping`` are kept as their own ungrouped entity
    (treated as a solo operator). Returns ``(grouped_rows, coverage)`` where
    ``coverage`` is the fraction of total stake that mapped to a known entity.
    """
    grouped: dict[str, float] = {}
    mapped_stake = 0.0
    total_stake = 0.0
    for vid, stake in stakes:
        total_stake += stake
        entity = mapping.get(vid)
        if entity is not None:
            grouped[entity] = grouped.get(entity, 0.0) + stake
            mapped_stake += stake
        else:
            # Unmapped validators count as solo entities — keep the raw id so
            # they don't collide and so they contribute one slot each.
            grouped[f"__unmapped__:{vid}"] = stake

    coverage = mapped_stake / total_stake if total_stake > 0 else 0.0
    return list(grouped.items()), coverage


def hhi(stakes: list[float]) -> float:
    """Herfindahl-Hirschman Index (sum of squared stake shares)."""
    total = sum(stakes)
    if total == 0:
        return 0.0
    return sum((s / total) ** 2 for s in stakes)


def renyi_entropy(stakes: list[float], alpha: float) -> float:
    """Renyi entropy of the stake distribution for a given ``alpha``.

    Special-cased for ``alpha`` of 0, 1 (Shannon), and ``+inf`` (min-entropy).
    """
    positive = [s for s in stakes if s > 0]
    total = sum(positive)
    if not positive or total == 0:
        return 0.0

    probs = [s / total for s in positive]

    if alpha == 0:
        return math.log(len(positive))
    if alpha == 1:
        return -sum(p * math.log(p) for p in probs if p > 0)
    if alpha == math.inf:
        return -math.log(max(probs))

    sum_p_alpha = sum(p**alpha for p in probs)
    if sum_p_alpha == 0:
        return 0.0
    return (1 / (1 - alpha)) * math.log(sum_p_alpha)


def shapley_top_values(
    stakes: list[float],
    threshold: float = 0.333,
    top_n: int = 3,
) -> list[float]:
    """Approximate Shapley values for the top ``top_n`` validators.

    Orders validators by stake and credits a pivotal validator (the one that
    pushes cumulative stake past ``threshold``) with ``1.0``; non-pivotal
    validators get a proportional share. Values are normalised to sum to 1.
    """
    if not stakes:
        return []

    sorted_desc = sorted(stakes, reverse=True)
    top = sorted_desc[:top_n]
    total = sum(stakes)
    if total == 0:
        return [0.0] * len(top)

    target = total * threshold
    raw: list[float] = []
    cumulative = 0.0
    for stake in top:
        before = cumulative < target
        after = (cumulative + stake) >= target
        raw.append(1.0 if before and after else stake / total)
        cumulative += stake

    raw_total = sum(raw)
    if raw_total == 0:
        return [0.0] * len(raw)
    return [v / raw_total for v in raw]
