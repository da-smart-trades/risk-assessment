# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.services import TeamMemberService, TeamService
from cert_ra.db.models import ManualMetric, TeamRoles
from cert_ra.types import ChainType, MetricCategory, ProtocolType, TokenType

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.anyio


USER_ID = UUID("5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2")
SUPERUSER_ID = UUID("97108ac1-ffcb-411d-8b1e-d9183399f63b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _add_user_to_operator_team(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    member_email: str,
    role: TeamRoles = TeamRoles.MEMBER,
    is_owner: bool = False,
) -> None:
    """Provision the operator team and attach a member with a given role."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        await team_service.ensure_operator_team(name="Op", domain="example.com")

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        user_service = UserService(session=session)
        member_service = TeamMemberService(session=session)
        operator_team = await team_service.get_one(is_operator=True)
        member_user = await user_service.get_one(email=member_email)
        await member_service.create(
            {
                "team_id": operator_team.id,
                "user_id": member_user.id,
                "role": role,
                "is_owner": is_owner,
            }
        )
        await member_service.repository.session.commit()


async def _seed_metric(
    sessionmaker: async_sessionmaker[AsyncSession],
    **overrides: Any,  # noqa: ANN401
) -> ManualMetric:
    """Insert one manual metric authored by the seeded regular user.

    Defaults to ``is_published=True`` so existing read-side tests see the
    row. Pass ``is_published=False`` to seed a draft explicitly.
    """
    payload: dict[str, Any] = {
        "name": "Sample Metric",
        "desc": "Sample description",
        "protocol": ProtocolType.AAVE_V3,
        "category": MetricCategory.ANCHORS,
        "is_published": True,
        "created_by": USER_ID,
        "updated_by": USER_ID,
    }
    # If the caller overrides an entity column, clear the default protocol.
    if any(k in overrides for k in ("chain", "token", "market")):
        payload["protocol"] = None
    payload.update(overrides)
    async with sessionmaker() as session:
        metric = ManualMetric(**payload)
        session.add(metric)
        await session.commit()
        await session.refresh(metric)
        return metric


# ---------------------------------------------------------------------------
# Read endpoints (open to authenticated users)
# ---------------------------------------------------------------------------


async def test_list_manual_metrics_anonymous_rejected(client: AsyncClient) -> None:
    """Anonymous clients cannot list manual metrics."""
    response = await client.get("/api/manual-metrics")
    assert response.status_code in (401, 403)


async def test_list_manual_metrics_user_can_read(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A regular authenticated user (no operator team) can read manual metrics."""
    await _seed_metric(sessionmaker, name="A")
    await _seed_metric(sessionmaker, name="B")

    response = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["name"] for item in body["items"]} == {"A", "B"}


async def test_list_filters_chain_strict_equality(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """``?chain=ETHEREUM`` excludes other chains and non-chain entities."""
    # Chain-scoped metrics must use category=GOVERNANCE under the new rules.
    await _seed_metric(
        sessionmaker,
        name="Eth",
        chain=ChainType.ETHEREUM,
        category=MetricCategory.GOVERNANCE,
    )
    await _seed_metric(
        sessionmaker,
        name="Sol",
        chain=ChainType.SOLANA,
        category=MetricCategory.GOVERNANCE,
    )
    # A non-chain entity (default protocol/ANCHORS) — must be excluded.
    await _seed_metric(sessionmaker, name="Global")

    response = await client.get(
        "/api/manual-metrics?chain=ETHEREUM", headers=user_token_headers
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["name"] for item in items} == {"Eth"}


async def test_list_filters_token_strict_equality(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Token-scoped metrics use ANCHORS/CONTROL/ASSURANCE/TOKEN_RISK.
    await _seed_metric(
        sessionmaker,
        name="Has-Token",
        token=TokenType.USDC,
        category=MetricCategory.ANCHORS,
    )
    # Non-token entity (default protocol) — must be excluded.
    await _seed_metric(sessionmaker, name="No-Token")

    response = await client.get(
        "/api/manual-metrics?token=USDC", headers=user_token_headers
    )
    assert response.status_code == 200
    assert {item["name"] for item in response.json()["items"]} == {"Has-Token"}


async def test_list_filters_category(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Categories are entity-scoped: GOVERNANCE pairs with a chain;
    # ANCHORS/CONTROL/ASSURANCE pair with token/protocol/market.
    await _seed_metric(
        sessionmaker,
        name="Gov",
        chain=ChainType.ETHEREUM,
        category=MetricCategory.GOVERNANCE,
    )
    await _seed_metric(sessionmaker, name="Anchor", category=MetricCategory.ANCHORS)

    response = await client.get(
        "/api/manual-metrics?category=GOVERNANCE", headers=user_token_headers
    )
    assert response.status_code == 200
    assert {item["name"] for item in response.json()["items"]} == {"Gov"}


async def test_list_filters_combined(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Filter combinations apply with AND semantics."""
    await _seed_metric(
        sessionmaker,
        name="Match",
        chain=ChainType.ETHEREUM,
        category=MetricCategory.GOVERNANCE,
    )
    # WrongChain — different chain, same category (still valid).
    await _seed_metric(
        sessionmaker,
        name="WrongChain",
        chain=ChainType.SOLANA,
        category=MetricCategory.GOVERNANCE,
    )
    # WrongEntity — protocol-scoped, different category.
    await _seed_metric(
        sessionmaker,
        name="WrongEntity",
        category=MetricCategory.ANCHORS,
    )

    response = await client.get(
        "/api/manual-metrics?chain=ETHEREUM&category=GOVERNANCE",
        headers=user_token_headers,
    )
    assert response.status_code == 200
    assert {item["name"] for item in response.json()["items"]} == {"Match"}


async def test_get_manual_metric_user_can_read(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_metric(sessionmaker, name="Fetch")

    response = await client.get(
        f"/api/manual-metrics/{metric.id}", headers=user_token_headers
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Fetch"


# ---------------------------------------------------------------------------
# Write endpoints — auth gates (requires_operator_editor)
# ---------------------------------------------------------------------------


async def test_create_user_without_operator_team_lands_in_team_scope(
    client: AsyncClient,
    user_token_headers: dict[str, str],
) -> None:
    """A user without operator-team membership but who can edit a team scope.

    The seeded ``user@example.com`` is owner of ``test-team`` (see
    ``raw_teams`` fixture), so creating a manual metric succeeds and lands
    in that team's scope.
    """
    response = await client.post(
        "/api/manual-metrics",
        json={"name": "X", "desc": "Y", "protocol": "AAVE_V3", "category": "ANCHORS"},
        headers=user_token_headers,
    )
    assert response.status_code == 201
    assert response.json()["teamSlug"] == "test-team"


async def test_create_operator_member_lands_in_team_scope(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Operator MEMBER (not editor) falls back to their editable team scope.

    Operator membership only forces shared scope when the user is also an
    operator editor/admin/owner. A plain operator MEMBER is treated like
    any other team editor — they publish to their team.
    """
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.MEMBER
    )
    response = await client.post(
        "/api/manual-metrics",
        json={"name": "X", "desc": "Y", "protocol": "AAVE_V3", "category": "ANCHORS"},
        headers=user_token_headers,
    )
    assert response.status_code == 201
    # Plain operator MEMBER does not unlock shared scope.
    assert response.json()["teamSlug"] == "test-team"


async def test_create_operator_editor_succeeds(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Operator-team EDITOR can create a manual metric."""
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Editor metric",
            "desc": "Created by editor",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
            "riskScore": 3,
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Editor metric"
    assert body["createdBy"] == str(USER_ID)
    assert body["updatedBy"] == str(USER_ID)
    assert body["riskScore"] == 3


async def test_create_operator_admin_succeeds(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Operator-team ADMIN can create a manual metric."""
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.ADMIN
    )
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Admin metric",
            "desc": "Created by admin",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201


async def test_create_superuser_bypass(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
) -> None:
    """Superuser bypasses the editor guard."""
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Super metric",
            "desc": "Created by superuser",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
        },
        headers=superuser_token_headers,
    )
    assert response.status_code == 201
    assert response.json()["createdBy"] == str(SUPERUSER_ID)


async def test_create_ignores_client_supplied_audit_fields(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Client-supplied createdBy/updatedBy are overwritten by current_user.id."""
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    spoofed_id = "00000000-0000-0000-0000-000000000000"
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "X",
            "desc": "Y",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
            "createdBy": spoofed_id,
            "updatedBy": spoofed_id,
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["createdBy"] == str(USER_ID)
    assert body["updatedBy"] == str(USER_ID)


async def test_create_invalid_risk_score_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """``riskScore`` outside [1, 5] is rejected by the DB CHECK constraint."""
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "X",
            "desc": "Y",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
            "riskScore": 9,
        },
        headers=user_token_headers,
    )
    # CHECK constraint fails inside advanced-alchemy, yielding a non-2xx
    # response. The exact status depends on framework error mapping.
    assert response.status_code not in (200, 201)


async def test_update_preserves_created_by(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """PATCH updates ``updated_by`` but preserves the original ``created_by``."""
    metric = await _seed_metric(sessionmaker, name="Original", desc="Original desc")
    # `metric.created_by` is USER_ID (seeded) — superuser updates it.
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}",
        json={"desc": "Updated by superuser"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["desc"] == "Updated by superuser"
    assert body["createdBy"] == str(USER_ID)
    assert body["updatedBy"] == str(SUPERUSER_ID)
    # Plain-user re-fetch sees the same record (no auth issue on read)
    fetch = await client.get(
        f"/api/manual-metrics/{metric.id}", headers=user_token_headers
    )
    assert fetch.json()["updatedBy"] == str(SUPERUSER_ID)


async def test_update_member_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_metric(sessionmaker, name="Untouchable")
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.MEMBER
    )
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}",
        json={"desc": "Tampering"},
        headers=user_token_headers,
    )
    assert response.status_code == 403


async def test_delete_editor_succeeds(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_metric(sessionmaker, name="ToDelete")
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    response = await client.delete(
        f"/api/manual-metrics/{metric.id}", headers=user_token_headers
    )
    assert response.status_code == 204
    fetch = await client.get(
        f"/api/manual-metrics/{metric.id}", headers=user_token_headers
    )
    # advanced-alchemy raises NotFoundError → 404, but some integrations
    # map it differently. Either way, the deleted row is gone.
    assert fetch.status_code != 200


async def test_delete_member_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_metric(sessionmaker, name="StaysAlive")
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.MEMBER
    )
    response = await client.delete(
        f"/api/manual-metrics/{metric.id}", headers=user_token_headers
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Inertia pages
# ---------------------------------------------------------------------------


async def test_inertia_list_includes_groups_and_flag(
    client: AsyncClient,
    user_inertia_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Inertia read view groups by category and exposes ``isOperatorEditor``."""
    await _seed_metric(
        sessionmaker, name="A", category=MetricCategory.ANCHORS, sub_category="x"
    )
    await _seed_metric(
        sessionmaker, name="B", category=MetricCategory.ANCHORS, sub_category="x"
    )
    await _seed_metric(
        sessionmaker,
        name="C",
        chain=ChainType.ETHEREUM,
        category=MetricCategory.GOVERNANCE,
        sub_category=None,
    )

    response = await client.get("/manual-metrics", headers=user_inertia_headers)
    assert response.status_code == 200
    page = response.json()
    content = page["props"]["content"]
    assert content["total"] == 3
    assert content["isOperatorEditor"] is False
    categories = [g["category"] for g in content["groups"]]
    # Groups are emitted in MetricCategory declaration order
    # (GOVERNANCE, ANCHORS, CONTROL, ASSURANCE, TOKEN_RISK, PROTOCOL_SCORE).
    assert categories == ["GOVERNANCE", "ANCHORS"]
    # The ANCHORS group with sub_category "x" contains both A and B.
    anchors_group = next(g for g in content["groups"] if g["category"] == "ANCHORS")
    assert anchors_group["subCategory"] == "x"
    assert {item["name"] for item in anchors_group["items"]} == {"A", "B"}


async def test_inertia_admin_list_accessible_to_team_editor(
    client: AsyncClient,
    user_inertia_headers: dict[str, str],
) -> None:
    """A team editor (no operator membership) can reach the admin list page.

    Under the new design the admin list is open to anyone who can edit at
    least one scope — operator editor OR any-team editor. The seeded user
    owns ``test-team`` so they qualify.
    """
    response = await client.get("/manual-metrics/admin", headers=user_inertia_headers)
    assert response.status_code == 200
    content = response.json()["props"]["content"]
    assert content["isOperatorEditor"] is False


async def test_inertia_admin_list_succeeds_for_editor(
    client: AsyncClient,
    user_inertia_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    await _seed_metric(sessionmaker, name="Visible to admin")
    response = await client.get("/manual-metrics/admin", headers=user_inertia_headers)
    assert response.status_code == 200
    content = response.json()["props"]["content"]
    assert content["total"] >= 1
    assert content["isOperatorEditor"] is True
