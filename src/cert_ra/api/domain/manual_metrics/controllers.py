# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Manual metric controllers — JSON API and Inertia page controllers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from advanced_alchemy.extensions.litestar.providers import (
    FilterConfig,
    create_service_dependencies,
)
from advanced_alchemy.filters import CollectionFilter, LimitOffset
from advanced_alchemy.service import OffsetPagination
from litestar import Controller, Request, delete, get, patch, post
from litestar.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from litestar.params import Dependency, Parameter
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import and_, or_, select

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.manual_metrics.schemas import (
    SHARED_SLUG,
    ManualMetric,
    ManualMetricCreate,
    ManualMetricGroup,
    ManualMetricListPage,
    ManualMetricPublish,
    ManualMetricUpdate,
    TeamOption,
)
from cert_ra.api.domain.manual_metrics.services import (
    ALLOWED_CATEGORIES_BY_ENTITY,
    RESERVED_CATEGORIES,
    ManualMetricService,
    validate_anchor_probability,
)
from cert_ra.api.lib.team_context import current_team_id_from_session
from cert_ra.db.models import (
    AutomatedMarketSnapshot as AutomatedMarketSnapshotModel,
    ManualMetric as ManualMetricModel,
    MarketConfig as MarketConfigModel,
    Team as TeamModel,
    TeamRoles,
    User,
)
from cert_ra.types import ChainType, MetricCategory, ProtocolType, TokenType

if TYPE_CHECKING:
    from collections.abc import Sequence

    from advanced_alchemy.filters import FilterTypes
    from sqlalchemy import ColumnElement

__all__ = ("ManualMetricApiController", "ManualMetricPageController")


_BASE_FILTERS: FilterConfig = {
    "id_filter": UUID,
    "created_at": True,
    "updated_at": True,
    "sort_field": "created_at",
    "sort_order": "desc",
    "pagination_type": "limit_offset",
    "pagination_size": 50,
}


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


def _user_team_ids(user: User) -> list[UUID]:
    """Team IDs the user belongs to (from the eagerly-loaded relationship)."""
    return [m.team_id for m in user.teams]


def _is_team_editor(user: User, team_id: UUID) -> bool:
    """True if ``user`` has ADMIN/EDITOR/owner role on ``team_id``."""
    if user.is_superuser:
        return True
    return any(
        m.team_id == team_id
        and (m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner)
        for m in user.teams
    )


def _is_operator_editor(user: User) -> bool:
    """True if ``user`` is editor/admin/owner of the operator team."""
    if user.is_superuser:
        return True
    return any(
        m.team.is_operator
        and (m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner)
        for m in user.teams
    )


def _is_any_team_editor(user: User) -> bool:
    """True if ``user`` can edit any team's content (any scope)."""
    if user.is_superuser:
        return True
    return any(
        m.is_owner or m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) for m in user.teams
    )


def _can_edit(metric: ManualMetricModel, user: User) -> bool:
    """True if ``user`` can mutate ``metric`` (per-row, post-fetch)."""
    if metric.team_id is None:
        return _is_operator_editor(user)
    return _is_team_editor(user, metric.team_id)


def _is_visible(metric: ManualMetricModel, user: User) -> bool:
    """True if ``metric`` is visible to ``user`` under scope + draft rules.

    Editors of the metric's scope see drafts; everyone else sees only
    published metrics. Soft-deleted rows are never visible to anyone
    (including superusers) — they are retired data.
    """
    if metric.deleted:
        return False
    if user.is_superuser:
        return True
    if _can_edit(metric, user):
        return True
    if not metric.is_published:
        return False
    if metric.team_id is None:
        return True
    return metric.team_id in _user_team_ids(user)


def _assert_can_write_manual_metric(user: User, metric: ManualMetricModel) -> None:
    """Raise ``PermissionDeniedException`` if ``user`` can't mutate ``metric``."""
    if metric.team_id is None:
        allowed = _is_operator_editor(user)
        detail = (
            "Operator team editor access is required to mutate shared manual metrics."
        )
    else:
        allowed = _is_team_editor(user, metric.team_id)
        detail = "Team editor access is required to mutate this manual metric."
    if not allowed:
        raise PermissionDeniedException(detail=detail)


def _editable_team_ids(user: User) -> list[UUID]:
    """Team IDs where ``user`` has ADMIN/EDITOR/owner role (excludes operator)."""
    return [
        m.team_id
        for m in user.teams
        if (m.is_owner or m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR))
        and not m.team.is_operator
    ]


def _derive_scope_for_create(request: Request, user: User) -> UUID | None:
    """Pick ``team_id`` for a new metric created by ``user``.

    Scope is server-determined; clients never pass ``team_id`` on create.

    - Operator editors always create shared metrics (``team_id=None``).
      Operator membership wins even if the user is also in other teams.
    - Non-operator team editors create metrics for their current team:
      * If they belong to exactly one editable team, that team is used.
      * Otherwise, the session's ``currentTeam.team_id`` is used
        (set by team page visits / the team switcher).

    Raises:
        PermissionDeniedException: If the user can't create in any scope.
        ValidationException: If a non-operator multi-team user has no
            current team selected.

    Returns:
        ``None`` for shared scope, or a team UUID.
    """
    if _is_operator_editor(user):
        return None
    editable = _editable_team_ids(user)
    if not editable:
        raise PermissionDeniedException(
            detail="You do not have permission to create manual metrics."
        )
    if len(editable) == 1:
        return editable[0]
    current_id = current_team_id_from_session(request.session)
    if current_id is None or current_id not in editable:
        raise ValidationException(
            detail=(
                "Switch to the team you want to publish under before creating "
                "a manual metric."
            )
        )
    return current_id


def _entity_type_from_payload(data: ManualMetricCreate) -> str:
    """Determine which entity type the create payload targets.

    Raises:
        ValidationException: if zero or multiple entity columns are set.
    """
    present = [
        name
        for name in ("chain", "token", "protocol")
        if getattr(data, name) is not None
    ]
    if len(present) != 1:
        raise ValidationException(
            detail="Exactly one of chain, token, or protocol must be set."
        )
    return present[0]


def _assert_category_allowed_for_entity(
    user: User, entity_type: str, category: MetricCategory
) -> None:
    """Reject (entity, category) pairs that aren't valid or aren't permitted.

    Two layers:
      - Structural: category must be in the allowed set for the entity type
        (mirrors the DB CHECK constraint; we surface a clean 400 here).
      - Authorization: reserved categories (PROTOCOL_SCORE, TOKEN_RISK) are
        operator-only at the application layer; team editors get 403.

    Raises:
        ValidationException: structural mismatch (e.g. chain + ANCHORS).
        PermissionDeniedException: reserved category + non-operator user.
    """
    allowed = ALLOWED_CATEGORIES_BY_ENTITY.get(entity_type, frozenset())
    if category not in allowed:
        allowed_names = sorted(c.value for c in allowed)
        raise ValidationException(
            detail=(
                f"Category '{category.value}' is not valid for entity type "
                f"'{entity_type}'. Allowed: {', '.join(allowed_names)}."
            )
        )
    if category in RESERVED_CATEGORIES and not _is_operator_editor(user):
        raise PermissionDeniedException(
            detail=(
                f"Only operator-team editors may publish manual metrics "
                f"under the '{category.value}' category."
            )
        )


def _assert_anchor_value_valid(category: MetricCategory, value: str | None) -> None:
    """Surface an invalid ANCHORS probability as a clean 400.

    Delegates to the service-layer validator (the single source of the
    rule) and re-raises its ``ValueError`` as a ``ValidationException``.
    """
    try:
        validate_anchor_probability(category, value)
    except ValueError as exc:
        raise ValidationException(detail=str(exc)) from exc


def _assert_market_pin_structurally_valid(data: ManualMetricCreate) -> None:
    """Reject a structurally invalid market pin with a clean 400.

    Both pin columns must be set together, and a pin is only meaningful on
    a protocol-scoped ANCHORS metric (markets belong to a protocol; only
    anchors feed a market's anchors term).
    """
    chain_id = data.market_chain_id
    hex_id = data.market_id_hex
    if chain_id is None and hex_id is None:
        return
    if (chain_id is None) != (hex_id is None):
        raise ValidationException(
            detail="market_chain_id and market_id_hex must be set together."
        )
    if data.protocol is None:
        raise ValidationException(
            detail="A market pin is only valid on a protocol-scoped metric."
        )
    if data.category != MetricCategory.ANCHORS:
        raise ValidationException(
            detail="Only ANCHORS metrics can be pinned to a specific market."
        )


async def _assert_pinned_market_exists(
    service: ManualMetricService,
    *,
    protocol: ProtocolType,
    chain_id: int,
    market_id_hex: str,
) -> None:
    """Reject a pin that names no discovered market for the protocol.

    A pin only ever feeds a market's PD when the metric's ``protocol``
    equals the market's ``MarketConfig.assurance_protocol`` mapping and a
    snapshot exists for ``(chain_id, market_id_hex)``. Validate that here
    so an operator can't pin to a market the workers have never produced.
    """
    stmt = (
        select(AutomatedMarketSnapshotModel.id)
        .join(
            MarketConfigModel,
            MarketConfigModel.id == AutomatedMarketSnapshotModel.market_config_id,
        )
        .where(
            MarketConfigModel.assurance_protocol == protocol,
            AutomatedMarketSnapshotModel.chain_id == chain_id,
            AutomatedMarketSnapshotModel.market_id_hex == market_id_hex,
        )
        .limit(1)
    )
    found = (await service.repository.session.execute(stmt)).scalar_one_or_none()
    if found is None:
        raise ValidationException(
            detail=(
                "No discovered market matches this pin for the selected "
                "protocol. Pick a market from the list, or leave the pin "
                "empty to apply to every market of the protocol."
            )
        )


# ---------------------------------------------------------------------------
# Filter / scope helpers
# ---------------------------------------------------------------------------


async def _resolve_team_slug(
    service: ManualMetricService,
    user: User,
    team_slug: str,
) -> tuple[UUID | None, bool]:
    """Resolve a ``team_slug`` query param.

    Returns:
        Tuple ``(team_id, is_shared)``:
          - ``(None, True)`` when ``team_slug == SHARED_SLUG``.
          - ``(<UUID>, False)`` when slug matches a real team the user can read.

    Raises:
        NotFoundException: If the slug doesn't match any team.
        PermissionDeniedException: If the user is not a member of the team
            and is not a superuser.
    """
    if team_slug == SHARED_SLUG:
        return None, True
    stmt = select(TeamModel.id).where(TeamModel.slug == team_slug)
    result = await service.repository.session.execute(stmt)
    team_id = result.scalar_one_or_none()
    if team_id is None:
        msg = f"Team with slug '{team_slug}' not found."
        raise NotFoundException(detail=msg)
    if not user.is_superuser and team_id not in _user_team_ids(user):
        msg = "You do not have access to this team's manual metrics."
        raise PermissionDeniedException(detail=msg)
    return team_id, False


def _visibility_filter(user: User) -> object | None:
    """Build the SQL visibility filter for non-superusers.

    Combines scope and draft visibility into one OR clause:
      - Published shared metrics → visible to everyone.
      - Drafts of shared scope → visible to operator editors.
      - Published team metrics → visible to members of that team.
      - Drafts of team scope → visible to editors of that team.

    Returns:
        A SQLAlchemy clause, or ``None`` for superusers.
    """
    if user.is_superuser:
        return None
    clauses: list = [
        and_(
            ManualMetricModel.team_id.is_(None),
            ManualMetricModel.is_published.is_(True),
        ),
    ]
    if _is_operator_editor(user):
        clauses.append(ManualMetricModel.team_id.is_(None))
    team_ids = _user_team_ids(user)
    if team_ids:
        clauses.append(
            and_(
                ManualMetricModel.team_id.in_(team_ids),
                ManualMetricModel.is_published.is_(True),
            )
        )
    editable = _editable_team_ids(user)
    if editable:
        clauses.append(ManualMetricModel.team_id.in_(editable))
    return or_(*clauses) if len(clauses) > 1 else clauses[0]


def _limit_offset(filters: list[FilterTypes]) -> tuple[int, int]:
    """Extract ``(limit, offset)`` from the resolved filter list."""
    for f in filters:
        if isinstance(f, LimitOffset):
            return f.limit, f.offset
    return _BASE_FILTERS.get("pagination_size", 50), 0


def _build_filters(
    base_filters: list[FilterTypes],
    *,
    chain: ChainType | None,
    token: TokenType | None,
    protocol: ProtocolType | None,
    category: MetricCategory | None,
    sub_category: str | None,
) -> list[FilterTypes]:
    """Append strict-equality filters for chain, token, protocol, category, sub_category.

    Always excludes soft-deleted rows — they are retired data and never
    surface in any list or read view.
    """
    extra: list[FilterTypes] = [ManualMetricModel.deleted.is_(False)]  # type: ignore[list-item]
    if chain is not None:
        extra.append(CollectionFilter("chain", [chain]))
    if token is not None:
        extra.append(CollectionFilter("token", [token]))
    if protocol is not None:
        extra.append(CollectionFilter("protocol", [protocol]))
    if category is not None:
        extra.append(CollectionFilter("category", [category]))
    if sub_category is not None:
        extra.append(CollectionFilter("sub_category", [sub_category]))
    return [*base_filters, *extra]


# ---------------------------------------------------------------------------
# Response building
# ---------------------------------------------------------------------------


def _entity_type_of(metric: ManualMetricModel) -> str:
    """Return the entity-type name set on ``metric``.

    Falls back to empty string if (somehow) the row violates the DB CHECK.
    """
    if metric.chain is not None:
        return "chain"
    if metric.token is not None:
        return "token"
    if metric.protocol is not None:
        return "protocol"
    return ""


def _to_response_schema(
    metric: ManualMetricModel, user: User | None = None
) -> ManualMetric:
    """Convert a model row to its response schema.

    If ``user`` is provided, ``can_edit`` and ``can_publish`` are computed
    per-row; otherwise they default to ``False``. Team metadata is included
    when the row is team-owned.
    """
    can_mutate = _can_edit(metric, user) if user is not None else False
    return ManualMetric(
        id=metric.id,
        name=metric.name,
        desc=metric.desc,
        category=metric.category,
        risk_score=metric.risk_score,
        is_published=metric.is_published,
        entity_type=_entity_type_of(metric),
        chain=metric.chain,
        token=metric.token,
        protocol=metric.protocol,
        sub_category=metric.sub_category,
        value=metric.value,
        notes=metric.notes,
        deleted=metric.deleted,
        market_chain_id=metric.market_chain_id,
        market_id_hex=metric.market_id_hex,
        team_id=metric.team_id,
        team_slug=metric.team.slug if metric.team is not None else None,
        team_name=metric.team.name if metric.team is not None else None,
        can_edit=can_mutate,
        can_publish=can_mutate,
        created_at=metric.created_at,
        updated_at=metric.updated_at,
        created_by=metric.created_by,
        updated_by=metric.updated_by,
    )


def _group_by_category(
    metrics: Sequence[ManualMetricModel],
    user: User | None = None,
) -> list[ManualMetricGroup]:
    """Group metrics by category, then by sub_category (None last).

    Categories are emitted in ``MetricCategory`` declaration order.
    Within a category, sub-categories are sorted alphabetically with NULL last.
    """
    by_category: dict[MetricCategory, dict[str | None, list[ManualMetric]]] = {}
    for metric in metrics:
        by_category.setdefault(metric.category, {}).setdefault(
            metric.sub_category, []
        ).append(_to_response_schema(metric, user))

    groups: list[ManualMetricGroup] = []
    for category in MetricCategory:
        sub_buckets = by_category.get(category)
        if not sub_buckets:
            continue
        sub_keys = sorted(
            sub_buckets.keys(),
            key=lambda k: (k is None, k or ""),
        )
        groups.extend(
            ManualMetricGroup(
                category=category,
                sub_category=sub,
                items=sub_buckets[sub],
            )
            for sub in sub_keys
        )
    return groups


def _team_options_for_user(
    user: User,
    *,
    edit_only: bool,
) -> list[TeamOption]:
    """Build the team-scope options for the current user.

    If ``edit_only`` is True, the list includes only scopes where the user
    has write access (used by the admin form). Otherwise it includes every
    scope the user can read (used by the read page's filter).

    The synthetic "Shared (platform-wide)" option is always present unless
    ``edit_only`` is True and the user is not an operator editor.
    """
    options: list[TeamOption] = []
    can_edit_shared = _is_operator_editor(user)
    if not edit_only or can_edit_shared:
        options.append(
            TeamOption(
                team_id=None,
                team_slug=SHARED_SLUG,
                team_name="Shared (platform-wide)",
                is_shared=True,
                can_edit=can_edit_shared,
            )
        )
    for membership in user.teams:
        team = membership.team
        can_edit_team = membership.is_owner or membership.role in (
            TeamRoles.ADMIN,
            TeamRoles.EDITOR,
        )
        if edit_only and not can_edit_team:
            continue
        options.append(
            TeamOption(
                team_id=team.id,
                team_slug=team.slug,
                team_name=team.name,
                is_shared=False,
                can_edit=can_edit_team,
            )
        )
    return options


# ---------------------------------------------------------------------------
# JSON API controller
# ---------------------------------------------------------------------------


class ManualMetricApiController(Controller):
    """Manual metric JSON API."""

    path = "/api/manual-metrics"
    tags = ["Manual Metrics"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        ManualMetricService,
        key="manual_metrics_service",
        filters=_BASE_FILTERS,
    )
    signature_namespace = {  # noqa: RUF012
        "ManualMetricService": ManualMetricService,
        "ManualMetricCreate": ManualMetricCreate,
        "ManualMetricUpdate": ManualMetricUpdate,
    }

    @get(
        operation_id="ListManualMetrics",
        name="manual_metrics:list",
        summary="List manual metrics",
        path="/",
    )
    async def list_manual_metrics(
        self,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
        token: TokenType | None = None,
        protocol: ProtocolType | None = None,
        category: MetricCategory | None = None,
        sub_category: str | None = None,
        team_slug: str | None = None,
    ) -> OffsetPagination[ManualMetric]:
        """List manual metrics visible to the current user.

        Visibility for non-superusers: ``team_id IS NULL OR team_id IN user_team_ids``.

        ``team_slug=shared`` restricts to shared metrics; ``team_slug=<slug>``
        restricts to one team (membership required for non-superusers).

        ``?chain=NULL`` is not supported — passing ``chain=ETHEREUM`` excludes
        rows with ``chain IS NULL``. Same for ``token``, ``protocol``,
        ``category``, and ``sub_category``.

        Returns:
            Paginated list of manual metrics.
        """
        all_filters = _build_filters(
            list(filters),
            chain=chain,
            token=token,
            protocol=protocol,
            category=category,
            sub_category=sub_category,
        )
        if team_slug is not None:
            scope_team_id, is_shared = await _resolve_team_slug(
                manual_metrics_service, current_user, team_slug
            )
            scope_filter = (
                ManualMetricModel.team_id.is_(None)
                if is_shared
                else ManualMetricModel.team_id == scope_team_id
            )
            all_filters.append(scope_filter)  # type: ignore[arg-type]
        else:
            visibility = _visibility_filter(current_user)
            if visibility is not None:
                all_filters.append(visibility)  # type: ignore[arg-type]
        results, total = await manual_metrics_service.list_and_count(*all_filters)
        limit, offset = _limit_offset(all_filters)
        return OffsetPagination[ManualMetric](
            items=[_to_response_schema(m, current_user) for m in results],
            total=total,
            limit=limit,
            offset=offset,
        )

    @get(
        operation_id="GetManualMetric",
        name="manual_metrics:get",
        summary="Get a manual metric",
        path="/{metric_id:uuid}",
    )
    async def get_manual_metric(
        self,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        metric_id: Annotated[
            UUID,
            Parameter(title="Metric ID", description="The manual metric to retrieve."),
        ],
    ) -> ManualMetric:
        """Get one manual metric by id (must be visible to the current user).

        Returns:
            The requested manual metric.

        Raises:
            NotFoundException: If the row doesn't exist OR exists but isn't
                visible to the user (enumeration safety).
        """
        db_obj = await manual_metrics_service.get(metric_id)
        if not _is_visible(db_obj, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        return _to_response_schema(db_obj, current_user)

    @post(
        operation_id="CreateManualMetric",
        name="manual_metrics:create",
        summary="Create a manual metric",
        path="/",
    )
    async def create_manual_metric(
        self,
        request: Request,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        data: ManualMetricCreate,
    ) -> ManualMetric:
        """Create a new manual metric in draft state.

        Scope is derived from ``current_user``: operator editors create
        shared metrics; non-operator team editors create metrics for their
        current team. Exactly one of chain/token/protocol must be set;
        the (entity, category) pair must be in the allowed set.
        Reserved categories (PROTOCOL_SCORE, TOKEN_RISK) require operator
        editor membership.

        ``created_by`` / ``updated_by`` are injected from ``current_user``;
        client-supplied values are overwritten.

        Returns:
            The newly created (draft) manual metric.
        """
        entity_type = _entity_type_from_payload(data)
        _assert_category_allowed_for_entity(current_user, entity_type, data.category)
        _assert_anchor_value_valid(data.category, data.value)
        _assert_market_pin_structurally_valid(data)
        if (
            data.market_chain_id is not None
            and data.market_id_hex is not None
            and data.protocol is not None
        ):
            await _assert_pinned_market_exists(
                manual_metrics_service,
                protocol=data.protocol,
                chain_id=data.market_chain_id,
                market_id_hex=data.market_id_hex,
            )
        team_id = _derive_scope_for_create(request, current_user)
        payload = data.to_dict()
        payload["team_id"] = team_id
        payload["is_published"] = False
        payload["created_by"] = current_user.id
        payload["updated_by"] = current_user.id
        db_obj = await manual_metrics_service.create(payload)
        return _to_response_schema(db_obj, current_user)

    @patch(
        operation_id="UpdateManualMetric",
        name="manual_metrics:update",
        summary="Update a manual metric",
        path="/{metric_id:uuid}",
    )
    async def update_manual_metric(
        self,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        data: ManualMetricUpdate,
        metric_id: Annotated[
            UUID,
            Parameter(title="Metric ID", description="The manual metric to update."),
        ],
    ) -> ManualMetric:
        """Update an existing manual metric.

        Authorization is decided per-row using the existing ``team_id`` (which
        is immutable). ``updated_by`` is injected from ``current_user``;
        ``created_by`` is preserved.

        Returns:
            The updated manual metric.
        """
        existing = await manual_metrics_service.get(metric_id)
        if not _is_visible(existing, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        _assert_can_write_manual_metric(current_user, existing)
        payload = data.to_dict()
        # ``category`` is immutable, so validate the (possibly new) value
        # against the row's existing category.
        if "value" in payload:
            _assert_anchor_value_valid(existing.category, payload["value"])
        payload["updated_by"] = current_user.id
        db_obj = await manual_metrics_service.update(item_id=metric_id, data=payload)
        return _to_response_schema(db_obj, current_user)

    @delete(
        operation_id="DeleteManualMetric",
        name="manual_metrics:delete",
        summary="Delete a manual metric",
        path="/{metric_id:uuid}",
    )
    async def delete_manual_metric(
        self,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        metric_id: Annotated[
            UUID,
            Parameter(title="Metric ID", description="The manual metric to delete."),
        ],
    ) -> None:
        """Delete a manual metric (hard delete).

        Authorization is decided per-row using the existing ``team_id``.
        """
        existing = await manual_metrics_service.get(metric_id)
        if not _is_visible(existing, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        _assert_can_write_manual_metric(current_user, existing)
        _ = await manual_metrics_service.delete(metric_id)

    @patch(
        operation_id="PublishManualMetric",
        name="manual_metrics:publish",
        summary="Publish or unpublish a manual metric",
        path="/{metric_id:uuid}/publish",
    )
    async def publish_manual_metric(
        self,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        data: ManualMetricPublish,
        metric_id: Annotated[
            UUID,
            Parameter(
                title="Metric ID",
                description="The manual metric to publish or unpublish.",
            ),
        ],
    ) -> ManualMetric:
        """Toggle a metric's published state.

        Authorization mirrors edit (scope editor).

        Returns:
            The updated manual metric.
        """
        existing = await manual_metrics_service.get(metric_id)
        if not _is_visible(existing, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        _assert_can_write_manual_metric(current_user, existing)
        db_obj = await manual_metrics_service.set_published(
            metric_id,
            is_published=data.is_published,
            updated_by=current_user.id,
        )
        return _to_response_schema(db_obj, current_user)


# ---------------------------------------------------------------------------
# Inertia page controller
# ---------------------------------------------------------------------------


class ManualMetricPageController(Controller):
    """Manual metric Inertia pages."""

    tags = ["Manual Metrics"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        ManualMetricService,
        key="manual_metrics_service",
        filters=_BASE_FILTERS,
    )
    signature_namespace = {  # noqa: RUF012
        "ManualMetricService": ManualMetricService,
        "ManualMetricCreate": ManualMetricCreate,
        "ManualMetricUpdate": ManualMetricUpdate,
    }

    @get(
        component="manual-metrics/list",
        name="manual_metrics.list",
        operation_id="ManualMetricsListPage",
        path="/manual-metrics",
    )
    async def list_page(
        self,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        chain: ChainType | None = None,
        token: TokenType | None = None,
        protocol: ProtocolType | None = None,
        category: MetricCategory | None = None,
        sub_category: str | None = None,
        team_slug: str | None = None,
    ) -> ManualMetricListPage:
        """Read view — grouped by category, sub-grouped by sub_category.

        Returns:
            Page props with grouped metrics, eligible team scopes, and the
            ``is_operator_editor`` flag.
        """
        all_filters = _build_filters(
            list(filters),
            chain=chain,
            token=token,
            protocol=protocol,
            category=category,
            sub_category=sub_category,
        )
        if team_slug is not None:
            scope_team_id, is_shared = await _resolve_team_slug(
                manual_metrics_service, current_user, team_slug
            )
            scope_filter = (
                ManualMetricModel.team_id.is_(None)
                if is_shared
                else ManualMetricModel.team_id == scope_team_id
            )
            all_filters.append(scope_filter)  # type: ignore[arg-type]
        else:
            visibility = _visibility_filter(current_user)
            if visibility is not None:
                all_filters.append(visibility)  # type: ignore[arg-type]
        results, total = await manual_metrics_service.list_and_count(*all_filters)
        return ManualMetricListPage(
            groups=_group_by_category(results, current_user),
            items=[_to_response_schema(m, current_user) for m in results],
            total=total,
            is_operator_editor=_is_operator_editor(current_user),
            teams=_team_options_for_user(current_user, edit_only=False),
            selected_team_slug=team_slug,
            selected_category=category,
        )

    @get(
        component="manual-metrics/admin/list",
        name="manual_metrics.admin.list",
        operation_id="ManualMetricsAdminListPage",
        path="/manual-metrics/admin",
    )
    async def admin_list_page(
        self,
        request: Request,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
        team_slug: str | None = None,
    ) -> ManualMetricListPage:
        """Operator/team-editor admin list — flat list with audit fields.

        Reachable by any user who can edit at least one scope (operator
        editor OR any-team editor). Users with no edit-eligible scope get 403.

        If ``team_slug`` is supplied, the list is scoped to that team (or
        to shared metrics for ``shared``). Otherwise, the list shows every
        metric the user can edit across every editable scope.

        Returns:
            Page props with flat items list (groups field empty).
        """
        _ = request  # parameter kept for symmetry with other admin handlers
        is_operator_editor = _is_operator_editor(current_user)
        if not (is_operator_editor or _is_any_team_editor(current_user)):
            raise PermissionDeniedException(
                detail="You do not have permission to manage manual metrics."
            )
        if team_slug is not None:
            scope_team_id, is_shared = await _resolve_team_slug(
                manual_metrics_service, current_user, team_slug
            )
            if is_shared:
                if not is_operator_editor:
                    raise PermissionDeniedException(
                        detail=(
                            "Operator team editor access is required to manage "
                            "shared manual metrics."
                        )
                    )
                scope_filter: ColumnElement[bool] = ManualMetricModel.team_id.is_(None)
            else:
                if not _is_team_editor(current_user, scope_team_id):  # type: ignore[arg-type]
                    raise PermissionDeniedException(
                        detail=(
                            "Team editor access is required to manage this "
                            "team's manual metrics."
                        )
                    )
                scope_filter = ManualMetricModel.team_id == scope_team_id
            all_filters: list[FilterTypes] = [
                *filters,
                scope_filter,  # type: ignore[list-item]
                ManualMetricModel.deleted.is_(False),  # type: ignore[list-item]
            ]
        else:
            # Show every metric the user can edit (across all editable scopes).
            editable_team_ids = [
                m.team_id
                for m in current_user.teams
                if m.is_owner or m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR)
            ]
            scope_clauses: list[ColumnElement[bool]] = []
            if is_operator_editor:
                scope_clauses.append(ManualMetricModel.team_id.is_(None))
            if editable_team_ids:
                scope_clauses.append(ManualMetricModel.team_id.in_(editable_team_ids))
            scope_filter = (
                or_(*scope_clauses) if len(scope_clauses) > 1 else scope_clauses[0]
            )
            all_filters = [
                *filters,
                scope_filter,  # type: ignore[list-item]
                ManualMetricModel.deleted.is_(False),  # type: ignore[list-item]
            ]
        results, total = await manual_metrics_service.list_and_count(*all_filters)
        return ManualMetricListPage(
            groups=[],
            items=[_to_response_schema(m, current_user) for m in results],
            total=total,
            is_operator_editor=is_operator_editor,
            teams=_team_options_for_user(current_user, edit_only=True),
            selected_team_slug=team_slug,
        )

    @get(
        component="manual-metrics/admin/create",
        name="manual_metrics.admin.create_page",
        operation_id="ManualMetricsAdminCreatePage",
        path="/manual-metrics/admin/create",
    )
    async def admin_create_page(
        self,
        current_user: User,
    ) -> ManualMetricListPage:
        """Show the create-metric page with editable scopes preloaded.

        Users with no edit-eligible scope get 403.
        """
        if not (_is_operator_editor(current_user) or _is_any_team_editor(current_user)):
            raise PermissionDeniedException(
                detail="You do not have permission to create manual metrics."
            )
        return ManualMetricListPage(
            groups=[],
            items=[],
            total=0,
            is_operator_editor=_is_operator_editor(current_user),
            teams=_team_options_for_user(current_user, edit_only=True),
        )

    @post(
        name="manual_metrics.admin.create",
        operation_id="ManualMetricsAdminCreate",
        path="/manual-metrics/admin",
    )
    async def admin_create(
        self,
        request: Request,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        data: ManualMetricCreate,
    ) -> InertiaRedirect:
        """Create a manual metric (Inertia). Scope is server-derived; row is draft."""
        entity_type = _entity_type_from_payload(data)
        _assert_category_allowed_for_entity(current_user, entity_type, data.category)
        _assert_anchor_value_valid(data.category, data.value)
        _assert_market_pin_structurally_valid(data)
        if (
            data.market_chain_id is not None
            and data.market_id_hex is not None
            and data.protocol is not None
        ):
            await _assert_pinned_market_exists(
                manual_metrics_service,
                protocol=data.protocol,
                chain_id=data.market_chain_id,
                market_id_hex=data.market_id_hex,
            )
        team_id = _derive_scope_for_create(request, current_user)
        payload = data.to_dict()
        payload["team_id"] = team_id
        payload["is_published"] = False
        payload["created_by"] = current_user.id
        payload["updated_by"] = current_user.id
        db_obj = await manual_metrics_service.create(payload)
        flash(
            request,
            f'Created manual metric "{db_obj.name}" as a draft. '
            "Publish it to make it visible.",
            category="info",
        )
        return InertiaRedirect(request, request.url_for("manual_metrics.admin.list"))

    @get(
        component="manual-metrics/admin/edit",
        name="manual_metrics.admin.edit_page",
        operation_id="ManualMetricsAdminEditPage",
        path="/manual-metrics/admin/{metric_id:uuid}",
    )
    async def admin_edit_page(
        self,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        metric_id: Annotated[
            UUID,
            Parameter(title="Metric ID", description="The manual metric to edit."),
        ],
    ) -> ManualMetricListPage:
        """Show the edit-metric page. ``team_id`` is rendered read-only."""
        db_obj = await manual_metrics_service.get(metric_id)
        if not _is_visible(db_obj, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        _assert_can_write_manual_metric(current_user, db_obj)
        return ManualMetricListPage(
            groups=[],
            items=[_to_response_schema(db_obj, current_user)],
            total=1,
            is_operator_editor=_is_operator_editor(current_user),
            teams=_team_options_for_user(current_user, edit_only=True),
        )

    @patch(
        name="manual_metrics.admin.update",
        operation_id="ManualMetricsAdminUpdate",
        path="/manual-metrics/admin/{metric_id:uuid}",
    )
    async def admin_update(
        self,
        request: Request,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        data: ManualMetricUpdate,
        metric_id: Annotated[
            UUID,
            Parameter(title="Metric ID", description="The manual metric to update."),
        ],
    ) -> InertiaRedirect:
        """Update a manual metric (Inertia)."""
        existing = await manual_metrics_service.get(metric_id)
        if not _is_visible(existing, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        _assert_can_write_manual_metric(current_user, existing)
        payload = data.to_dict()
        # ``category`` is immutable, so validate the (possibly new) value
        # against the row's existing category.
        if "value" in payload:
            _assert_anchor_value_valid(existing.category, payload["value"])
        payload["updated_by"] = current_user.id
        db_obj = await manual_metrics_service.update(item_id=metric_id, data=payload)
        flash(
            request,
            f'Updated manual metric "{db_obj.name}".',
            category="info",
        )
        return InertiaRedirect(request, request.url_for("manual_metrics.admin.list"))

    @delete(
        name="manual_metrics.admin.delete",
        operation_id="ManualMetricsAdminDelete",
        path="/manual-metrics/admin/{metric_id:uuid}",
        status_code=303,
    )
    async def admin_delete(
        self,
        request: Request,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        metric_id: Annotated[
            UUID,
            Parameter(title="Metric ID", description="The manual metric to delete."),
        ],
    ) -> InertiaRedirect:
        """Delete a manual metric (Inertia)."""
        existing = await manual_metrics_service.get(metric_id)
        if not _is_visible(existing, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        _assert_can_write_manual_metric(current_user, existing)
        db_obj = await manual_metrics_service.delete(metric_id)
        flash(
            request,
            f'Deleted manual metric "{db_obj.name}".',
            category="info",
        )
        return InertiaRedirect(request, request.url_for("manual_metrics.admin.list"))

    @patch(
        name="manual_metrics.admin.publish",
        operation_id="ManualMetricsAdminPublish",
        path="/manual-metrics/admin/{metric_id:uuid}/publish",
    )
    async def admin_publish(
        self,
        request: Request,
        manual_metrics_service: ManualMetricService,
        current_user: User,
        data: ManualMetricPublish,
        metric_id: Annotated[
            UUID,
            Parameter(
                title="Metric ID",
                description="The manual metric to publish or unpublish.",
            ),
        ],
    ) -> InertiaRedirect:
        """Publish or unpublish a manual metric (Inertia)."""
        existing = await manual_metrics_service.get(metric_id)
        if not _is_visible(existing, current_user):
            raise NotFoundException(detail="Manual metric not found.")
        _assert_can_write_manual_metric(current_user, existing)
        db_obj = await manual_metrics_service.set_published(
            metric_id,
            is_published=data.is_published,
            updated_by=current_user.id,
        )
        flash(
            request,
            (
                f'Published "{db_obj.name}".'
                if data.is_published
                else f'Unpublished "{db_obj.name}".'
            ),
            category="info",
        )
        return InertiaRedirect(request, request.url_for("manual_metrics.admin.list"))
