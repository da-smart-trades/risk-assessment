# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    schema_dump,
)

from cert_ra.db.models import ManualMetric
from cert_ra.types import MetricCategory

if TYPE_CHECKING:
    from uuid import UUID

    from advanced_alchemy.service import ModelDictT

__all__ = (
    "ALLOWED_CATEGORIES_BY_ENTITY",
    "ENTITY_COLUMNS",
    "MARKET_PIN_COLUMNS",
    "RESERVED_CATEGORIES",
    "ManualMetricService",
    "validate_anchor_probability",
)


ENTITY_COLUMNS: tuple[str, str, str] = ("chain", "token", "protocol")
"""Columns that name the target entity of a manual metric (exactly one set).

The ``market`` column was removed in Phase 7 of the automated-market-
metrics rollout — markets are now dynamic via the ``market_config``
table and the automated PD pipeline, not manual entries.
"""


ALLOWED_CATEGORIES_BY_ENTITY: dict[str, frozenset[MetricCategory]] = {
    "chain": frozenset({MetricCategory.GOVERNANCE}),
    "token": frozenset(
        {
            MetricCategory.ANCHORS,
            MetricCategory.CONTROL,
            MetricCategory.ASSURANCE,
            MetricCategory.TOKEN_RISK,
        }
    ),
    "protocol": frozenset(
        {
            MetricCategory.ANCHORS,
            MetricCategory.CONTROL,
            MetricCategory.ASSURANCE,
            MetricCategory.PROTOCOL_SCORE,
        }
    ),
}
"""Per-entity allowed categories. Mirrors the DB ``ck_manual_metric_entity_category`` check."""


RESERVED_CATEGORIES: frozenset[MetricCategory] = frozenset(
    {MetricCategory.PROTOCOL_SCORE, MetricCategory.TOKEN_RISK}
)
"""Categories that only operator editors may assign at create time."""


def _is_anchors(category: object) -> bool:
    """True if ``category`` is (or names) the ANCHORS category.

    Accepts both a ``MetricCategory`` and its string value, since payloads
    may carry either form.
    """
    if isinstance(category, MetricCategory):
        return category == MetricCategory.ANCHORS
    return category == MetricCategory.ANCHORS.value


def _entity_type_of(data: dict) -> str | None:
    """Return the entity-type name set in ``data``, or None if zero/multiple."""
    present = [c for c in ENTITY_COLUMNS if data.get(c) is not None]
    if len(present) != 1:
        return None
    return present[0]


def _validate_entity_and_category(data: dict) -> None:
    """Reject payloads that violate the (entity, category) invariants.

    Mirrors the DB ``ck_manual_metric_entity_category`` constraint so the
    error surfaces as a 4xx with a clear message instead of an
    IntegrityError. Reserved-category authorization is enforced separately
    by the controller (it requires an operator-editor user); this check
    only enforces that the chosen category is *structurally* valid for the
    chosen entity type.

    Raises:
        ValueError: If zero or multiple entity columns are set, or the
            category is not allowed for that entity type.
    """
    entity_type = _entity_type_of(data)
    if entity_type is None:
        msg = "Exactly one of chain / token / protocol must be set on a manual metric."
        raise ValueError(msg)
    category = data.get("category")
    if category is None:
        msg = "category is required."
        raise ValueError(msg)
    # Coerce to MetricCategory for set membership (caller may pass the str).
    try:
        category_enum = MetricCategory(category)
    except ValueError as e:
        msg = f"Unknown category '{category}'."
        raise ValueError(msg) from e
    allowed = ALLOWED_CATEGORIES_BY_ENTITY[entity_type]
    if category_enum not in allowed:
        allowed_names = sorted(c.value for c in allowed)
        msg = (
            f"Category '{category_enum.value}' is not valid for "
            f"entity type '{entity_type}'. Allowed: {', '.join(allowed_names)}."
        )
        raise ValueError(msg)


MARKET_PIN_COLUMNS: tuple[str, str] = ("market_chain_id", "market_id_hex")
"""Columns that pin an ANCHORS metric to one discovered market."""


def _validate_market_pin(data: dict) -> None:
    """Reject market pins that violate ``ck_manual_metric_market_pin``.

    Both pin columns must be set together, and only a protocol-scoped
    ANCHORS metric may carry a pin (markets belong to a protocol and only
    anchors feed a market's anchors term). An unpinned metric (both null)
    applies to every market of its protocol and is always allowed.

    Raises:
        ValueError: On a half-set pin, a pin on a non-protocol metric, or
            a pin on a non-ANCHORS category.
    """
    chain_id = data.get("market_chain_id")
    hex_id = data.get("market_id_hex")
    if chain_id is None and hex_id is None:
        return
    if (chain_id is None) != (hex_id is None):
        msg = "market_chain_id and market_id_hex must be set together."
        raise ValueError(msg)
    if data.get("protocol") is None:
        msg = "A market pin is only valid on a protocol-scoped metric."
        raise ValueError(msg)
    category = data.get("category")
    if category is not None and not _is_anchors(category):
        msg = "Only ANCHORS metrics can be pinned to a specific market."
        raise ValueError(msg)


def validate_anchor_probability(category: object, value: object) -> None:
    """Ensure an ANCHORS metric's ``value`` is a probability in ``[0, 1)``.

    ANCHORS metrics fold their ``value`` into the market anchors term as a
    per-anchor ``pd``; the calculator requires ``pd ∈ [0, 1)``. A blank
    value is allowed (the row is neutral until the operator fills it in),
    matching the assurance term. Non-ANCHORS categories are unaffected —
    their ``value`` keeps its own semantics (e.g. the assurance multiplier).

    Raises:
        ValueError: If an ANCHORS value is non-numeric or outside ``[0, 1)``.
    """
    if category is None or not _is_anchors(category):
        return
    if value is None or value == "":
        return
    if not isinstance(value, (str, int, float)):
        # ValueError (not TypeError) on purpose: the controller catches
        # ValueError to surface a clean 400 for every bad-value case.
        msg = (
            f"An ANCHORS metric's value must be a probability in [0, 1); got {value!r}."
        )
        raise ValueError(msg)  # noqa: TRY004
    try:
        pd = float(value)
    except (TypeError, ValueError) as exc:
        msg = (
            f"An ANCHORS metric's value must be a probability in [0, 1); got {value!r}."
        )
        raise ValueError(msg) from exc
    if not 0.0 <= pd < 1.0:
        msg = f"An ANCHORS metric's value must be a probability in [0, 1); got {pd}."
        raise ValueError(msg)


class ManualMetricService(SQLAlchemyAsyncRepositoryService[ManualMetric]):
    """CRUD service for operator-curated manual metrics."""

    class Repo(SQLAlchemyAsyncRepository[ManualMetric]):
        """ManualMetric SQLAlchemy Repository."""

        model_type = ManualMetric

    repository_type = Repo
    match_fields = ["name"]  # noqa: RUF012

    async def to_model_on_create(
        self, data: ModelDictT[ManualMetric]
    ) -> ModelDictT[ManualMetric]:
        """Validate audit fields, scope, and the (entity, category) pair.

        The controller derives ``team_id`` from ``current_user`` (operator
        editors → ``None`` / shared; team editors → their current team) and
        injects it before calling ``create``.

        Args:
            data: Raw payload (dict or schema) for the new metric.

        Returns:
            Validated payload with audit-stamp fields and ``team_id`` present.

        Raises:
            ValueError: If required fields are missing, exactly-one-entity
                is violated, or the (entity, category) pair is invalid.
        """
        data = schema_dump(data)
        if "created_by" not in data or "updated_by" not in data:
            msg = "created_by and updated_by must be set by the controller."
            raise ValueError(msg)
        if "team_id" not in data:
            msg = (
                "team_id must be set by the controller — pass None for shared "
                "scope or a UUID for a team-owned metric."
            )
            raise ValueError(msg)
        _validate_entity_and_category(data)
        _validate_market_pin(data)
        validate_anchor_probability(data.get("category"), data.get("value"))
        return data

    async def to_model_on_update(
        self, data: ModelDictT[ManualMetric]
    ) -> ModelDictT[ManualMetric]:
        """Validate the update payload.

        ``team_id`` is immutable after creation — reassigning a metric between
        scopes would break visibility / favoritability invariants.
        Entity columns (``chain``/``token``/``protocol``) are likewise
        immutable. ``is_published`` is mutated only via the dedicated
        ``set_published`` path.

        Args:
            data: Raw payload (dict or schema) for the update.

        Returns:
            Validated payload with ``updated_by`` present.

        Raises:
            ValueError: If ``updated_by`` is missing or the payload tries to
                mutate ``team_id`` / entity columns / ``is_published``.
        """
        data = schema_dump(data)
        if "updated_by" not in data:
            msg = "updated_by must be set by the controller."
            raise ValueError(msg)
        if "team_id" in data:
            msg = "team_id is immutable after creation."
            raise ValueError(msg)
        if any(col in data for col in ENTITY_COLUMNS):
            msg = "Entity columns (chain/token/protocol) are immutable after creation."
            raise ValueError(msg)
        if any(col in data for col in MARKET_PIN_COLUMNS):
            msg = "The market pin is immutable after creation."
            raise ValueError(msg)
        if "is_published" in data:
            msg = "Use the publish endpoint to change is_published."
            raise ValueError(msg)
        return data

    async def set_published(
        self,
        item_id: UUID,
        *,
        is_published: bool,
        updated_by: UUID,
    ) -> ManualMetric:
        """Toggle a metric's published state.

        Bypasses the normal update path (which forbids mutating
        ``is_published``). This is the only legitimate writer of the field
        after row creation.

        Args:
            item_id: Target metric ID.
            is_published: New published state.
            updated_by: User performing the change (audit field).

        Returns:
            The updated metric.
        """
        updated: ManualMetric = await self.repository.update(
            ManualMetric(id=item_id, is_published=is_published, updated_by=updated_by)
        )
        return updated
