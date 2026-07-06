# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Admin controller for weighting_profile + its entries.

Two-layer ACL:

* **Global default profiles** (``team_id IS NULL``) — operator
  superusers only. These are the fall-through used when no team
  profile applies.
* **Team-scoped profiles** — managed by team admins / editors of
  their own team. Other teams' profiles are invisible.

The form submits the whole profile + entries together; the service
replaces the entry collection atomically inside one transaction.

Cascading sub-category support: ``GET /api/weighting-profiles/
available-sub-categories`` returns the dynamic set of sub-category
strings the admin can pick from for a given (category, scope, target)
combination. The set is sourced from the latest scorer JSON (for
anchor / control) or from distinct ``ManualMetric.sub_category`` on
protocol-scoped ASSURANCE rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.extensions.litestar.providers import create_service_dependencies
from litestar import Controller, Request, delete, get, patch, post
from litestar.di import Provide
from litestar.exceptions import (
    NotFoundException,
    PermissionDeniedException,
    ValidationException,
)
from litestar.params import Dependency, Parameter
from litestar_vite.inertia import InertiaRedirect, flash
from sqlalchemy import desc, distinct, or_, select

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.admin.dependencies import provide_audit_service
from cert_ra.api.domain.market_config.services import MarketConfigService
from cert_ra.api.domain.weighting_profiles.schemas import (
    AdminWeightingProfileCreatePage,
    AdminWeightingProfileEditPage,
    AdminWeightingProfileListPage,
    AvailableSubCategoriesPage,
    TeamScopeOption,
    WeightingProfile as WeightingProfileSchema,
    WeightingProfileCreate,
    WeightingProfileEntry as WeightingProfileEntrySchema,
    WeightingProfileUpdate,
)
from cert_ra.api.domain.weighting_profiles.services import WeightingProfileService
from cert_ra.db.models import (
    AuditAction,
    AutomatedMarketSnapshot,
    ManualMetric,
    MarketConfig,
    TeamRoles,
    User,
    WeightingProfile,
)
from cert_ra.types import (
    MarketSnapshotKind,
    MetricCategory,
    WeightingProfileEntryCategory,
)

if TYPE_CHECKING:
    from advanced_alchemy.filters import FilterTypes
    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.api.domain.admin.services import AuditLogService

__all__ = (
    "AdminWeightingProfileController",
    "WeightingProfileApiController",
)


# ---------------------------------------------------------------------------
# Shared ACL helpers
# ---------------------------------------------------------------------------


def _is_operator_editor(user: User) -> bool:
    if user.is_superuser:
        return True
    return any(
        m.team.is_operator
        and (m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR) or m.is_owner)
        for m in user.teams
    )


def _editable_team_ids(user: User) -> set[UUID]:
    """Teams (excluding operator) where ``user`` can edit content."""
    return {
        m.team_id
        for m in user.teams
        if (m.is_owner or m.role in (TeamRoles.ADMIN, TeamRoles.EDITOR))
        and not m.team.is_operator
    }


def _visible_team_ids(user: User) -> set[UUID]:
    """Teams the user can read content from."""
    return {m.team_id for m in user.teams}


def _can_edit_profile(user: User, profile: WeightingProfile) -> bool:
    """Per-row write-permission check."""
    if user.is_superuser:
        return True
    if profile.team_id is None:
        return _is_operator_editor(user)
    return profile.team_id in _editable_team_ids(user)


def _assert_can_edit_profile(user: User, profile: WeightingProfile) -> None:
    """Raise ``PermissionDeniedException`` unless ``user`` can mutate ``profile``."""
    if _can_edit_profile(user, profile):
        return
    detail = (
        "Operator team editor access is required to mutate the global "
        "default weighting profile."
        if profile.team_id is None
        else "Team editor access is required to mutate this weighting profile."
    )
    raise PermissionDeniedException(detail=detail)


def _visibility_filter(user: User) -> object | None:
    """SQL filter limiting reads to profiles the user can see.

    Superusers see everything. Operator editors see all global defaults
    plus every team's profile (they manage the platform). Team editors
    see their team's profiles. Plain users see only published global
    defaults — but weighting profiles don't have a "published" concept
    yet, so plain users see global defaults outright.
    """
    if user.is_superuser:
        return None
    clauses = [WeightingProfile.team_id.is_(None)]
    team_ids = _visible_team_ids(user)
    if team_ids:
        clauses.append(WeightingProfile.team_id.in_(team_ids))
    return or_(*clauses) if len(clauses) > 1 else clauses[0]


def _derive_team_id_for_create(user: User, *, is_global: bool) -> UUID | None:
    """Pick ``team_id`` for a new profile based on the operator's intent.

    Args:
        user: The current user.
        is_global: When ``True``, target the global default
            (``team_id=None``). Requires operator/superuser.

    Returns:
        ``None`` for global, or a team UUID for team-scoped.
    """
    if is_global:
        if not _is_operator_editor(user):
            raise PermissionDeniedException(
                detail=(
                    "Operator team editor access is required to manage the "
                    "global default weighting profile."
                )
            )
        return None
    editable = _editable_team_ids(user)
    if not editable:
        raise PermissionDeniedException(
            detail="You do not have permission to create weighting profiles."
        )
    if len(editable) == 1:
        return next(iter(editable))
    msg = (
        "Pick a team via the team switcher before creating a weighting "
        "profile; multi-team users cannot infer the target team."
    )
    raise ValidationException(detail=msg)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _to_schema(
    row: WeightingProfile,
    user: User,
    market_label_lookup: dict[tuple[UUID, int, str], str],
) -> WeightingProfileSchema:
    market_key = (
        (row.target_market_config_id, row.target_chain_id, row.target_market_id_hex)
        if (
            row.target_market_config_id is not None
            and row.target_chain_id is not None
            and row.target_market_id_hex is not None
        )
        else None
    )
    cached_label = (
        market_label_lookup.get(market_key) if market_key is not None else None
    )
    return WeightingProfileSchema(
        id=row.id,
        team_id=row.team_id,
        team_slug=row.team.slug if row.team is not None else None,
        team_name=row.team.name if row.team is not None else None,
        name=row.name,
        scope=row.scope,
        target_protocol=row.target_protocol,
        target_market_config_id=row.target_market_config_id,
        target_chain_id=row.target_chain_id,
        target_market_id_hex=row.target_market_id_hex,
        # Prefer the live yarn-discovered label (from the snapshot stream);
        # fall back to the label cached on the profile row itself when the
        # snapshot history has been purged.
        target_market_label=cached_label or row.target_label,
        entries=[
            WeightingProfileEntrySchema(
                id=e.id,
                category=e.category,
                sub_category=e.sub_category,
                weight=e.weight,
            )
            for e in row.entries
        ],
        can_edit=_can_edit_profile(user, row),
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


def _scope_options(user: User) -> list[TeamScopeOption]:
    """Build the team-scope picker for the admin form."""
    options: list[TeamScopeOption] = []
    can_edit_global = _is_operator_editor(user)
    options.append(
        TeamScopeOption(
            team_id=None,
            team_slug="global",
            team_name="Global default",
            is_global=True,
            can_edit=can_edit_global,
        )
    )
    for membership in user.teams:
        if membership.team.is_operator:
            continue
        can_edit = membership.is_owner or membership.role in (
            TeamRoles.ADMIN,
            TeamRoles.EDITOR,
        )
        options.append(
            TeamScopeOption(
                team_id=membership.team.id,
                team_slug=membership.team.slug,
                team_name=membership.team.name,
                is_global=False,
                can_edit=can_edit,
            )
        )
    return options


async def _enabled_markets_catalog(
    market_config_service: MarketConfigService,
) -> tuple[list[dict], dict[tuple[UUID, int, str], str]]:
    """Return the enabled-market catalogue derived from the snapshot stream.

    With market_config protocol-only, the catalogue of pickable markets
    is whatever the workers have actually seen — i.e. the latest
    snapshot per ``(market_config_id, chain_id, market_id_hex)`` under
    an enabled protocol. The catalogue carries
    ``marketConfigId / chainId / marketIdHex / label`` so the form can
    submit all three pieces of identity, and the label lookup keys on
    the same trio so :func:`_to_schema` can resolve the friendly label
    for an existing MARKET-scope profile.
    """
    session = market_config_service.repository.session
    stmt = (
        select(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
            AutomatedMarketSnapshot.label,
            MarketConfig.protocol,
        )
        .join(MarketConfig, MarketConfig.id == AutomatedMarketSnapshot.market_config_id)
        .where(MarketConfig.enabled.is_(True))
        .order_by(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
            desc(AutomatedMarketSnapshot.created_at),
        )
        .distinct(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
        )
    )
    rows = (await session.execute(stmt)).all()
    catalogue: list[dict] = []
    labels: dict[tuple[UUID, int, str], str] = {}
    for row in rows:
        catalogue.append(
            {
                "marketConfigId": str(row.market_config_id),
                "protocol": row.protocol,
                "chainId": row.chain_id,
                "marketIdHex": row.market_id_hex,
                "label": row.label,
            }
        )
        labels[(row.market_config_id, row.chain_id, row.market_id_hex)] = row.label
    catalogue.sort(key=lambda entry: (entry["protocol"], entry["label"]))
    return catalogue, labels


# ---------------------------------------------------------------------------
# Admin (Inertia) controller
# ---------------------------------------------------------------------------


class AdminWeightingProfileController(Controller):
    """Team-aware CRUD on weighting_profile rows."""

    tags = ["Admin - Weighting Profiles"]  # noqa: RUF012
    path = "/admin/weighting-profiles"
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        WeightingProfileService,
        key="weighting_profile_service",
        filters={
            "id_filter": UUID,
            "search": "name",
            "pagination_type": "limit_offset",
            "pagination_size": 50,
            "created_at": True,
            "updated_at": True,
            "sort_field": "updated_at",
            "sort_order": "desc",
        },
    ) | {
        "audit_service": Provide(provide_audit_service),
        "market_config_service": Provide(
            create_service_dependencies(
                MarketConfigService, key="market_config_service"
            )["market_config_service"].dependency
        ),
    }
    signature_namespace = {  # noqa: RUF012
        "WeightingProfileService": WeightingProfileService,
        "MarketConfigService": MarketConfigService,
        "WeightingProfileCreate": WeightingProfileCreate,
        "WeightingProfileUpdate": WeightingProfileUpdate,
    }

    # -----------------------------------------------------------------
    # Pages
    # -----------------------------------------------------------------

    @get(
        component="admin/weighting-profiles/list",
        name="admin.weighting_profiles.list",
        operation_id="AdminWeightingProfilesList",
        path="/",
    )
    async def list_page(
        self,
        weighting_profile_service: WeightingProfileService,
        market_config_service: MarketConfigService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> AdminWeightingProfileListPage:
        """List profiles visible to the current user."""
        visibility = _visibility_filter(current_user)
        all_filters: list[FilterTypes] = list(filters)
        if visibility is not None:
            all_filters.append(visibility)  # type: ignore[arg-type]
        rows, total = await weighting_profile_service.list_and_count(*all_filters)
        _, label_lookup = await _enabled_markets_catalog(market_config_service)
        return AdminWeightingProfileListPage(
            profiles=[_to_schema(r, current_user, label_lookup) for r in rows],
            total=total,
            scopes=_scope_options(current_user),
            is_operator_editor=_is_operator_editor(current_user),
        )

    @get(
        component="admin/weighting-profiles/create",
        name="admin.weighting_profiles.create_page",
        operation_id="AdminWeightingProfileCreatePage",
        path="/create",
    )
    async def create_page(
        self,
        market_config_service: MarketConfigService,
        current_user: User,
    ) -> AdminWeightingProfileCreatePage:
        """Render the empty create form."""
        markets, _ = await _enabled_markets_catalog(market_config_service)
        return AdminWeightingProfileCreatePage(
            scopes=_scope_options(current_user),
            markets=markets,
            is_operator_editor=_is_operator_editor(current_user),
        )

    @get(
        component="admin/weighting-profiles/edit",
        name="admin.weighting_profiles.edit_page",
        operation_id="AdminWeightingProfileEditPage",
        path="/{profile_id:uuid}/",
    )
    async def edit_page(
        self,
        weighting_profile_service: WeightingProfileService,
        market_config_service: MarketConfigService,
        current_user: User,
        profile_id: Annotated[UUID, Parameter(title="Profile ID")],
    ) -> AdminWeightingProfileEditPage:
        """Render the edit form populated with current values + entries."""
        try:
            profile = await weighting_profile_service.get_with_entries(profile_id)
        except RepositoryError as exc:
            raise NotFoundException(detail=str(exc)) from exc
        _assert_can_edit_profile(current_user, profile)
        markets, label_lookup = await _enabled_markets_catalog(market_config_service)
        return AdminWeightingProfileEditPage(
            profile=_to_schema(profile, current_user, label_lookup),
            scopes=_scope_options(current_user),
            markets=markets,
            is_operator_editor=_is_operator_editor(current_user),
        )

    # -----------------------------------------------------------------
    # Mutations
    # -----------------------------------------------------------------

    @post(
        name="admin.weighting_profiles.create",
        operation_id="AdminWeightingProfileCreate",
        path="/",
    )
    async def create(
        self,
        request: Request,
        weighting_profile_service: WeightingProfileService,
        audit_service: AuditLogService,
        current_user: User,
        data: WeightingProfileCreate,
    ) -> InertiaRedirect:
        """Create a weighting profile + its entries in one transaction."""
        team_id = _derive_team_id_for_create(current_user, is_global=data.is_global)
        try:
            payload = data.to_dict()
            entries = payload.pop("entries", [])
            payload.pop("is_global", None)
            payload["team_id"] = team_id
            payload["created_by"] = current_user.id
            payload["updated_by"] = current_user.id
            profile = await weighting_profile_service.create(payload)
            if entries:
                await weighting_profile_service.replace_entries(profile, entries)
        except RepositoryError as exc:
            raise ValidationException(detail=str(exc)) from exc

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.WEIGHTING_PROFILE_CREATED,
            target_type="weighting_profile",
            target_id=profile.id,
            target_label=profile.name,
            details={
                "team_id": str(team_id) if team_id else None,
                "scope": profile.scope.value,
                "target_protocol": profile.target_protocol,
                "target_market_config_id": (
                    str(profile.target_market_config_id)
                    if profile.target_market_config_id
                    else None
                ),
                "target_chain_id": profile.target_chain_id,
                "target_market_id_hex": profile.target_market_id_hex,
                "target_label": profile.target_label,
                "entry_count": len(entries),
            },
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Created weighting profile {profile.name}.", category="success")
        return InertiaRedirect(
            request,
            request.url_for(
                "admin.weighting_profiles.edit_page", profile_id=profile.id
            ),
        )

    @patch(
        name="admin.weighting_profiles.update",
        operation_id="AdminWeightingProfileUpdate",
        path="/{profile_id:uuid}/",
    )
    async def update(
        self,
        request: Request,
        weighting_profile_service: WeightingProfileService,
        audit_service: AuditLogService,
        current_user: User,
        profile_id: Annotated[UUID, Parameter(title="Profile ID")],
        data: WeightingProfileUpdate,
    ) -> InertiaRedirect:
        """Update header fields and/or replace the entries collection."""
        try:
            profile = await weighting_profile_service.get_with_entries(profile_id)
        except RepositoryError as exc:
            raise NotFoundException(detail=str(exc)) from exc
        _assert_can_edit_profile(current_user, profile)

        try:
            payload = data.to_dict()
            entries = payload.pop("entries", None)
            payload["updated_by"] = current_user.id
            profile = await weighting_profile_service.update(
                item_id=profile_id, data=payload
            )
            if entries is not None:
                profile = await weighting_profile_service.replace_entries(
                    profile, entries
                )
        except RepositoryError as exc:
            raise ValidationException(detail=str(exc)) from exc

        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.WEIGHTING_PROFILE_UPDATED,
            target_type="weighting_profile",
            target_id=profile.id,
            target_label=profile.name,
            details=data.to_dict(),
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Updated weighting profile {profile.name}.", category="info")
        return InertiaRedirect(
            request,
            request.url_for(
                "admin.weighting_profiles.edit_page", profile_id=profile.id
            ),
        )

    @delete(
        name="admin.weighting_profiles.delete",
        operation_id="AdminWeightingProfileDelete",
        path="/{profile_id:uuid}/",
        status_code=303,
    )
    async def delete(
        self,
        request: Request,
        weighting_profile_service: WeightingProfileService,
        audit_service: AuditLogService,
        current_user: User,
        profile_id: Annotated[UUID, Parameter(title="Profile ID")],
    ) -> InertiaRedirect:
        """Delete a weighting profile + its entries (cascade)."""
        try:
            profile = await weighting_profile_service.get_with_entries(profile_id)
        except RepositoryError as exc:
            raise NotFoundException(detail=str(exc)) from exc
        _assert_can_edit_profile(current_user, profile)
        label = profile.name
        await weighting_profile_service.delete(profile_id)
        await audit_service.log_action(
            actor=current_user,
            action=AuditAction.WEIGHTING_PROFILE_DELETED,
            target_type="weighting_profile",
            target_id=profile_id,
            target_label=label,
            ip_address=request.client.host if request.client else None,
        )
        flash(request, f"Deleted weighting profile {label}.", category="warning")
        return InertiaRedirect(
            request, request.url_for("admin.weighting_profiles.list")
        )


# ---------------------------------------------------------------------------
# JSON API: available sub-categories (cascading dropdown helper)
# ---------------------------------------------------------------------------


class WeightingProfileApiController(Controller):
    """Read-only JSON helpers for the admin form."""

    tags = ["Weighting Profiles"]  # noqa: RUF012
    path = "/api/weighting-profiles"
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        WeightingProfileService, key="weighting_profile_service"
    )
    signature_namespace = {"WeightingProfileService": WeightingProfileService}  # noqa: RUF012

    @get(
        operation_id="ListAvailableSubCategories",
        name="weighting_profiles:available_sub_categories",
        summary="List sub-categories currently in use",
        path="/available-sub-categories",
    )
    async def list_available_sub_categories(
        self,
        weighting_profile_service: WeightingProfileService,
        category: WeightingProfileEntryCategory,
        protocol: str | None = None,
        market_config_id: UUID | None = None,
        chain_id: int | None = None,
        market_id_hex: str | None = None,
    ) -> AvailableSubCategoriesPage:
        """Populate the sub-category dropdown for ``category``.

        * ``anchor`` / ``control`` — read the keys from the latest
          ``automated_market_snapshot`` of ``kind='SCORE'`` for the
          relevant scope. When ``market_config_id`` is set, only that
          market's latest snapshot is consulted. Otherwise, the union
          of keys across every enabled market for ``protocol`` is
          returned (lets a protocol-scoped profile cover sub-categories
          that any one market currently exposes).
        * ``assurance`` — distinct ``ManualMetric.name`` for published,
          shared (``team_id IS NULL``) ASSURANCE rows scoped to
          ``protocol``. The dimension (Audits, Testing, …) is the row's
          ``name``; the ``sub_category`` column on those rows only marks
          the Evidence/Multiplier pair, so a weighting profile targets
          the ``name``.

        Returns:
            Sorted, de-duplicated list of sub-category strings.
        """
        session = weighting_profile_service.repository.session
        if category == WeightingProfileEntryCategory.ASSURANCE:
            if protocol is None and market_config_id is None:
                raise ValidationException(
                    detail=(
                        "protocol or market_config_id is required for "
                        "category='assurance'."
                    )
                )
            # ASSURANCE manual metrics are keyed by the uppercase
            # ``ProtocolType`` the operator mapped via
            # ``MarketConfig.assurance_protocol`` — NOT the lowercase market
            # slug carried in ``protocol``. Resolve that mapping first (the
            # same indirection markets/assurance.py uses); comparing the slug
            # against the enum column directly silently matches nothing.
            ap_stmt = select(distinct(MarketConfig.assurance_protocol)).where(
                MarketConfig.assurance_protocol.is_not(None)
            )
            if market_config_id is not None:
                ap_stmt = ap_stmt.where(MarketConfig.id == market_config_id)
            else:
                ap_stmt = ap_stmt.where(MarketConfig.protocol == protocol)
            assurance_protocols = [
                ap for ap in (await session.scalars(ap_stmt)).all() if ap is not None
            ]
            if not assurance_protocols:
                return AvailableSubCategoriesPage(sub_categories=[])
            # NB: de-dup with DISTINCT but sort in Python — `ORDER BY
            # DISTINCT col` is invalid Postgres syntax (the old code's
            # `order_by(distinct(...))` raised a 500 the form swallowed
            # into an empty dropdown).
            stmt = select(distinct(ManualMetric.name)).where(
                ManualMetric.category == MetricCategory.ASSURANCE,
                ManualMetric.protocol.in_(assurance_protocols),
                ManualMetric.is_published.is_(True),
                ManualMetric.team_id.is_(None),
            )
            subs = [
                row for row in (await session.scalars(stmt)).all() if row is not None
            ]
            return AvailableSubCategoriesPage(sub_categories=sorted(subs))

        # anchor / control share the score-snapshot path. The scorer
        # renamed ``controlModifiers`` -> ``controls``; union both so the
        # dropdown still populates against either old or new snapshots.
        json_keys = (
            ("anchors",)
            if category == WeightingProfileEntryCategory.ANCHOR
            else ("controls", "controlModifiers")
        )
        snapshots = await _latest_score_snapshots(
            session,
            protocol=protocol,
            market_config_id=market_config_id,
            chain_id=chain_id,
            market_id_hex=market_id_hex,
        )
        sub_set: set[str] = set()
        for snap in snapshots:
            score = snap.score or {}
            for json_key in json_keys:
                block = score.get(json_key)
                if isinstance(block, dict):
                    sub_set.update(block.keys())
        return AvailableSubCategoriesPage(sub_categories=sorted(sub_set))


async def _latest_score_snapshots(
    session: AsyncSession,
    *,
    protocol: str | None,
    market_config_id: UUID | None,
    chain_id: int | None = None,
    market_id_hex: str | None = None,
) -> list[AutomatedMarketSnapshot]:
    """Latest SCORE snapshot per market, scoped to one market or one protocol.

    When ``market_config_id`` is provided alongside ``chain_id`` and
    ``market_id_hex``, returns only the latest snapshot for that exact
    market (MARKET-scope sub-category lookup). When only the protocol
    is provided, returns the latest snapshot per
    ``(chain_id, market_id_hex)`` under that protocol (PROTOCOL-scope
    sub-category lookup, where the union of all current markets'
    keys is what we want).
    """
    base = select(AutomatedMarketSnapshot).where(
        AutomatedMarketSnapshot.kind == MarketSnapshotKind.SCORE
    )
    if (
        market_config_id is not None
        and chain_id is not None
        and market_id_hex is not None
    ):
        stmt = (
            base.where(
                AutomatedMarketSnapshot.market_config_id == market_config_id,
                AutomatedMarketSnapshot.chain_id == chain_id,
                AutomatedMarketSnapshot.market_id_hex == market_id_hex,
            )
            .order_by(desc(AutomatedMarketSnapshot.created_at))
            .limit(1)
        )
        return list((await session.scalars(stmt)).all())
    if protocol is None:
        return []
    market_ids_stmt = select(MarketConfig.id).where(
        MarketConfig.protocol == protocol,
        MarketConfig.enabled.is_(True),
    )
    market_ids = list((await session.scalars(market_ids_stmt)).all())
    if not market_ids:
        return []
    # Latest per (chain_id, market_id_hex) within the protocol via DISTINCT ON.
    stmt = (
        select(AutomatedMarketSnapshot)
        .where(
            AutomatedMarketSnapshot.kind == MarketSnapshotKind.SCORE,
            AutomatedMarketSnapshot.market_config_id.in_(market_ids),
        )
        .order_by(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
            desc(AutomatedMarketSnapshot.created_at),
        )
        .distinct(
            AutomatedMarketSnapshot.market_config_id,
            AutomatedMarketSnapshot.chain_id,
            AutomatedMarketSnapshot.market_id_hex,
        )
    )
    return list((await session.scalars(stmt)).all())
