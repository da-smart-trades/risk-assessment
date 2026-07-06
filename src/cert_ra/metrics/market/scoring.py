# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Pure-function Probability-of-Default calculator for markets.

The methodology is fixed by the product owner; only the weights are
user-configurable. Given the latest scorer JSON and the applicable
weighting profile entries, :func:`compute_market_pd` returns a
:class:`PdBreakdown` with the final PD and the three intermediate
terms used to build it.

Methodology (per the PRD):

* **Anchors term** — "at least one anchor fires" under independent
  failures:
  ``anchors = 1 − ∏(1 − pd_i × w_i)`` over the anchor metric keys.
  Operator-entered manual ANCHORS metrics fold into the same product:
  each contributes its ``value`` as a ``pd`` in ``[0, 1)`` at a fixed
  weight of ``1.0`` (manual anchors are deliberately *not* weightable).
  A manual key that collides with a scorer anchor key is namespaced
  (``manual:<key>``) so both contribute independently.
* **Control modifiers term** — multiplicative aggregate of control
  metrics, clamped to ``[0.75, 1.25]``:
  ``control = clamp(∏(multiplier_i × w_i))``.
* **Assurance term** — multiplicative aggregate of operator-entered
  manual ASSURANCE values, also clamped to ``[0.75, 1.25]``:
  ``assurance = clamp(∏(value_i × w_i))``.
* **Final PD** — the product of the three terms.

**Empty-input rule:** if any of the three category inputs has zero
entries, that term forces to ``1.0`` so missing signal doesn't zero out
the other factors. Applied uniformly across all three categories.

**Weight resolution:** weights live in the normalised
``weighting_profile_entry`` table as ``(category, sub_category,
weight)`` triples. Categories in the profile are singular (``anchor``,
``control``, ``assurance``); this module maps the scorer JSON's plural
keys (``anchors``, ``controls``) to those singular forms. Any
``(category, sub_category)`` not present in the entries dict defaults
to weight ``1.0``.

The calculator is intentionally a pure function — no DB access, no
``datetime.now()`` calls in the math path. The caller injects all
required state (the scorer payload, the resolved profile entries, the
ASSURANCE manual metrics).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from cert_ra.types import WeightingProfileEntryCategory

if TYPE_CHECKING:
    from cert_ra.db.models import ManualMetric, WeightingProfileEntry

__all__ = (
    "AnchorContribution",
    "AssuranceContribution",
    "ControlContribution",
    "MarketScoringError",
    "PdBreakdown",
    "compute_market_pd",
)

# Clamp bounds for control modifiers + assurance, per the methodology.
_CLAMP_LOW = 0.75
_CLAMP_HIGH = 1.25

# Maps the scorer JSON's keys to the singular WeightingProfileEntry
# category enum value (and to the lookup key used inside this module).
_ANCHOR_CATEGORY = WeightingProfileEntryCategory.ANCHOR.value
_CONTROL_CATEGORY = WeightingProfileEntryCategory.CONTROL.value
_ASSURANCE_CATEGORY = WeightingProfileEntryCategory.ASSURANCE.value


class MarketScoringError(ValueError):
    """Raised when an input violates the calculator's contract.

    Examples that trigger this:
    * a per-metric ``pd`` value ≥ 1.0 (probabilities must be in ``[0, 1)``)
    * a per-metric ``pd`` or ``multiplier`` value that is not numeric
    * a negative weight (rejected at profile-write time, but defended
      here for paranoia)
    """


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnchorContribution:
    """One anchor metric's contribution to the anchors term.

    ``score`` is the LLM's per-anchor numeric judgment (separate from
    ``pd``, which is the derived probability of default). It carries
    no role in the PD math — the calculator preserves it solely so the
    UI can render the per-anchor ``name · score · pd`` row the spec
    calls for. ``rationale`` is the free-text justification, surfaced
    alongside the collected metrics.

    ``source`` distinguishes a scorer-derived anchor (``"scorer"``) from
    an operator-entered manual ANCHORS metric (``"manual"``) so the UI
    can badge the manual ones.
    """

    sub_category: str
    pd: float
    weight: float
    score: float | None = None
    rationale: str | None = None
    source: str = "scorer"


@dataclass(frozen=True, slots=True)
class ControlContribution:
    """One control modifier's contribution to the control term.

    ``rationale`` carries the LLM's per-control free-text justification
    for surfacing in the UI; it has no role in the PD math.
    """

    sub_category: str
    multiplier: float
    weight: float
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class AssuranceContribution:
    """One assurance manual-metric's contribution to the assurance term."""

    sub_category: str
    multiplier: float
    weight: float


@dataclass(frozen=True, slots=True)
class PdBreakdown:
    """Result of one PD calculation, suitable for persistence + display.

    The three term fields are the post-clamp / post-formula values that
    multiply to ``final_pd``. ``breakdown`` carries the per-metric
    contributions so the UI can explain how each input shaped the
    result. The dataclass is frozen so callers can safely cache it
    without worrying about accidental mutation.
    """

    final_pd: float
    anchors_term: float
    control_term: float
    assurance_term: float
    breakdown: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_weights_lookup(
    entries: Iterable[WeightingProfileEntry],
) -> dict[tuple[str, str], float]:
    """Flatten profile entries into ``(category, sub_category) -> weight``.

    Negative weights would have been rejected at write time by the
    service layer + DB CHECK, but defend defensively. Non-numeric
    weights are stored as ``Decimal`` and converted to ``float`` here.
    """
    out: dict[tuple[str, str], float] = {}
    for entry in entries:
        weight = float(entry.weight)
        if weight < 0:
            msg = (
                f"weighting_profile_entry {entry.id} has negative weight "
                f"{weight}; service layer should have rejected it"
            )
            raise MarketScoringError(msg)
        out[(entry.category.value, entry.sub_category)] = weight
    return out


def _weight(
    weights: Mapping[tuple[str, str], float], category: str, sub_category: str
) -> float:
    """Look up a weight; default ``1.0`` when the combination is unmapped."""
    return weights.get((category, sub_category), 1.0)


def _coerce_number(raw: object, *, where: str) -> float:
    """Convert a JSON-decoded value to ``float``. Raises on non-numerics."""
    if isinstance(raw, bool):
        # ``bool`` is a subclass of ``int`` in Python — guard so an
        # accidental ``"pd": true`` doesn't silently become 1.0.
        msg = f"{where}: expected a number, got boolean"
        raise MarketScoringError(msg)
    if isinstance(raw, (int, float, Decimal)):
        return float(raw)
    msg = f"{where}: expected a number, got {type(raw).__name__}"
    raise MarketScoringError(msg)


def _coerce_optional_number(raw: object) -> float | None:
    """Best-effort numeric coercion for fields like the per-anchor ``score``.

    Unlike :func:`_coerce_number`, missing or non-numeric values are
    treated as "absent" rather than fatal — ``score`` is a display-only
    hint from the LLM, not load-bearing math. Returning ``None`` lets
    the UI omit the column for entries the model didn't fill in.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        return float(raw)
    return None


def _coerce_optional_text(raw: object) -> str | None:
    """Best-effort text coercion for the per-entry ``rationale`` field."""
    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        return stripped or None
    return None


def _clamp(value: float) -> float:
    """Clamp to ``[0.75, 1.25]``. Used for control + assurance terms."""
    return max(_CLAMP_LOW, min(_CLAMP_HIGH, value))


# ---------------------------------------------------------------------------
# Per-category term computations
# ---------------------------------------------------------------------------


def _anchors_term(
    anchors_raw: Mapping[str, object],
    weights: Mapping[tuple[str, str], float],
    manual_anchors: Sequence[ManualMetric] = (),
) -> tuple[float, list[AnchorContribution]]:
    """Combine anchor metrics into ``1 − ∏(1 − pd × w)`` plus per-anchor contributions.

    Two sources fold into the same product:

    * **Scorer anchors** (``anchors_raw``) — weighted by the resolved
      profile (default ``1.0``).
    * **Manual ANCHORS metrics** (``manual_anchors``) — each row's
      ``value`` is its ``pd``, always at weight ``1.0`` (manual anchors
      are deliberately not weightable). A manual key that collides with
      a scorer key (or another manual key) is namespaced ``manual:<key>``
      so both contribute independently. Rows with a blank ``value`` are
      skipped (neutral until the operator fills one in).

    Empty input on *both* sources forces the term to ``1.0`` (neutral).
    A per-anchor ``pd`` must be in ``[0, 1)`` — values ≥ 1 don't make
    sense as a probability and break the formula (the inner
    ``1 − pd × w`` could go negative, which then propagates a sign flip
    into the final product).
    """
    if not anchors_raw and not manual_anchors:
        return 1.0, []
    product = 1.0
    contribs: list[AnchorContribution] = []
    seen: set[str] = set()
    for sub_category, payload in anchors_raw.items():
        if not isinstance(payload, Mapping):
            msg = f"score.anchors[{sub_category}]: expected an object"
            raise MarketScoringError(msg)
        pd = _coerce_number(
            payload.get("pd"), where=f"score.anchors[{sub_category}].pd"
        )
        if not 0.0 <= pd < 1.0:
            msg = (
                f"score.anchors[{sub_category}].pd = {pd}: per-anchor pd "
                f"must be in [0, 1)"
            )
            raise MarketScoringError(msg)
        weight = _weight(weights, _ANCHOR_CATEGORY, sub_category)
        product *= 1.0 - pd * weight
        seen.add(sub_category)
        contribs.append(
            AnchorContribution(
                sub_category=sub_category,
                pd=pd,
                weight=weight,
                score=_coerce_optional_number(payload.get("score")),
                rationale=_coerce_optional_text(payload.get("rationale")),
                source="scorer",
            )
        )
    for metric in manual_anchors:
        raw_value = metric.value
        if raw_value is None or raw_value == "":
            # No probability entered yet — the row is neutral until it
            # gets a value, mirroring the assurance term's behaviour.
            continue
        try:
            pd = float(raw_value)
        except (TypeError, ValueError) as exc:
            msg = (
                f"manual_metric {metric.id} value {raw_value!r} is not a "
                f"valid float probability"
            )
            raise MarketScoringError(msg) from exc
        if not 0.0 <= pd < 1.0:
            msg = (
                f"manual_metric {metric.id} pd = {pd}: a manual anchor "
                f"probability must be in [0, 1)"
            )
            raise MarketScoringError(msg)
        base = (metric.sub_category or metric.name or "manual").strip() or "manual"
        key = base if base not in seen else f"manual:{base}"
        suffix = 2
        while key in seen:
            key = f"manual:{base}:{suffix}"
            suffix += 1
        seen.add(key)
        # Manual anchors are never weighted — fixed weight of 1.0.
        product *= 1.0 - pd
        contribs.append(
            AnchorContribution(
                sub_category=key,
                pd=pd,
                weight=1.0,
                score=None,
                rationale=_coerce_optional_text(metric.notes),
                source="manual",
            )
        )
    if not contribs:
        # Scorer block was empty and every manual row had a blank value.
        return 1.0, []
    return 1.0 - product, contribs


def _control_term(
    controls_raw: Mapping[str, object],
    weights: Mapping[tuple[str, str], float],
) -> tuple[float, list[ControlContribution]]:
    """Combine control modifiers into ``clamp(∏(multiplier × w))`` plus contributions.

    Empty input forces the term to ``1.0``. Multipliers must be
    non-negative — a negative multiplier would let a single control
    flip the sign of the entire term, which is meaningless given the
    clamp bounds.
    """
    if not controls_raw:
        return 1.0, []
    product = 1.0
    contribs: list[ControlContribution] = []
    for sub_category, payload in controls_raw.items():
        if not isinstance(payload, Mapping):
            msg = f"score.controls[{sub_category}]: expected an object"
            raise MarketScoringError(msg)
        multiplier = _coerce_number(
            payload.get("multiplier"),
            where=f"score.controls[{sub_category}].multiplier",
        )
        if multiplier < 0.0:
            msg = (
                f"score.controls[{sub_category}].multiplier = "
                f"{multiplier}: must be non-negative"
            )
            raise MarketScoringError(msg)
        weight = _weight(weights, _CONTROL_CATEGORY, sub_category)
        product *= multiplier * weight
        contribs.append(
            ControlContribution(
                sub_category=sub_category,
                multiplier=multiplier,
                weight=weight,
                rationale=_coerce_optional_text(payload.get("rationale")),
            )
        )
    return _clamp(product), contribs


def _assurance_term(
    assurance_metrics: Sequence[ManualMetric],
    weights: Mapping[tuple[str, str], float],
) -> tuple[float, list[AssuranceContribution]]:
    """Combine manual ASSURANCE rows into ``clamp(∏(value × w))`` + contributions.

    Each manual metric's ``value`` column carries the multiplier as a
    text-encoded float in ``[0.75, 1.25]``. The assurance *dimension*
    (Audits, Testing, …) is the row's ``name`` — that's the key a
    weighting profile targets, so a per-dimension weight multiplies just
    that dimension's contribution (default ``1.0`` when unmapped). Note
    the ``sub_category`` column on these rows holds ``Evidence`` /
    ``Multiplier`` (which of the paired rows this is), not the dimension,
    so it is *not* used as the weight key. Rows with no parseable
    multiplier are flagged at the source (the admin form / service
    layer) — here we just defend against bad values.
    """
    if not assurance_metrics:
        return 1.0, []
    product = 1.0
    contribs: list[AssuranceContribution] = []
    for metric in assurance_metrics:
        raw_value = metric.value
        if raw_value is None or raw_value == "":
            # Skip rows where the operator hasn't filled in a multiplier;
            # the row is effectively neutral until it gets a value.
            continue
        try:
            multiplier = float(raw_value)
        except (TypeError, ValueError) as exc:
            msg = (
                f"manual_metric {metric.id} value {raw_value!r} is not a "
                f"valid float multiplier"
            )
            raise MarketScoringError(msg) from exc
        if multiplier < 0.0:
            msg = (
                f"manual_metric {metric.id} multiplier = {multiplier}: must "
                f"be non-negative"
            )
            raise MarketScoringError(msg)
        # The dimension (and the weight key) is the row's ``name``; the
        # ``sub_category`` column only distinguishes the Evidence/Multiplier
        # rows that make up one dimension.
        dimension = metric.name or ""
        weight = _weight(weights, _ASSURANCE_CATEGORY, dimension)
        product *= multiplier * weight
        contribs.append(
            AssuranceContribution(
                sub_category=dimension,
                multiplier=multiplier,
                weight=weight,
            )
        )
    if not contribs:
        # Every row was empty — treat the same as no rows.
        return 1.0, []
    return _clamp(product), contribs


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def compute_market_pd(
    score: Mapping[str, object] | None,
    profile_entries: Iterable[WeightingProfileEntry],
    assurance_metrics: Sequence[ManualMetric],
    manual_anchors: Sequence[ManualMetric] = (),
) -> PdBreakdown:
    """Compute the final PD and breakdown for one market at one point in time.

    Args:
        score: The ``score`` block from the latest scorer snapshot. The
            calculator reads ``score.anchors`` and ``score.controls``
            (the legacy ``score.controlModifiers`` key is still accepted).
            A ``None`` or empty mapping means "no automated signal yet" —
            both terms force to neutral.
        profile_entries: The applicable ``WeightingProfileEntry`` rows
            (already resolved by precedence in the caller). Pass an empty
            iterable when no profile applies; every weight defaults to 1.0.
        assurance_metrics: Protocol-level ``ManualMetric`` rows in the
            ASSURANCE category. Pass an empty list to skip the assurance
            term.
        manual_anchors: Protocol- or market-scoped ``ManualMetric`` rows
            in the ANCHORS category, already filtered to the relevant
            market by the caller. Each contributes its ``value`` as a
            ``pd`` to the anchors term at a fixed weight of ``1.0``. Pass
            an empty list to fold in no manual anchors.

    Returns:
        :class:`PdBreakdown` carrying the final PD, the three intermediate
        terms, and a per-metric breakdown dict for the UI.

    Raises:
        MarketScoringError: For any input that violates the calculator's
            contract — non-numeric metric values, pd values outside
            ``[0, 1)``, negative multipliers, etc.
    """
    weights = _build_weights_lookup(profile_entries)
    score_map: Mapping[str, object] = score or {}
    anchors_raw_obj = score_map.get("anchors") or {}
    # The scorer renamed ``controlModifiers`` -> ``controls``; accept the
    # legacy key so PD recompute still works against older snapshots.
    controls_raw_obj = score_map.get("controls")
    if controls_raw_obj is None:
        controls_raw_obj = score_map.get("controlModifiers")
    controls_raw_obj = controls_raw_obj or {}
    if not isinstance(anchors_raw_obj, Mapping):
        msg = "score.anchors must be an object"
        raise MarketScoringError(msg)
    if not isinstance(controls_raw_obj, Mapping):
        msg = "score.controls must be an object"
        raise MarketScoringError(msg)
    anchors_term, anchor_contribs = _anchors_term(
        anchors_raw_obj, weights, manual_anchors
    )
    control_term, control_contribs = _control_term(controls_raw_obj, weights)
    assurance_term, assurance_contribs = _assurance_term(assurance_metrics, weights)
    final_pd = anchors_term * control_term * assurance_term
    return PdBreakdown(
        final_pd=final_pd,
        anchors_term=anchors_term,
        control_term=control_term,
        assurance_term=assurance_term,
        breakdown={
            "anchors": [
                {
                    "subCategory": c.sub_category,
                    "pd": c.pd,
                    "weight": c.weight,
                    "score": c.score,
                    "rationale": c.rationale,
                    "source": c.source,
                }
                for c in anchor_contribs
            ],
            "controlModifiers": [
                {
                    "subCategory": c.sub_category,
                    "multiplier": c.multiplier,
                    "weight": c.weight,
                    "rationale": c.rationale,
                }
                for c in control_contribs
            ],
            "assurance": [
                {
                    "subCategory": c.sub_category,
                    "multiplier": c.multiplier,
                    "weight": c.weight,
                }
                for c in assurance_contribs
            ],
        },
    )
