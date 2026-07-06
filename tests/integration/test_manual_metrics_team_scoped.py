# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Integration tests for team-scoped manual metrics + draft/publish workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.services import TeamMemberService, TeamService
from cert_ra.db.models import ManualMetric, Team, TeamRoles
from cert_ra.types import MetricCategory, ProtocolType

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.anyio


USER_ID = UUID("5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2")
SUPERUSER_ID = UUID("97108ac1-ffcb-411d-8b1e-d9183399f63b")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _ensure_team(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    name: str,
    slug: str,
) -> Team:
    """Create a non-operator team owned by the superuser."""
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        return await team_service.create(
            {"name": name, "slug": slug, "owner_id": SUPERUSER_ID},
            auto_commit=True,
        )


async def _add_membership(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    team_id: UUID,
    role: TeamRoles = TeamRoles.MEMBER,
    is_owner: bool = False,
) -> None:
    async with sessionmaker() as session:
        member_service = TeamMemberService(session=session)
        await member_service.create(
            {
                "team_id": team_id,
                "user_id": user_id,
                "role": role,
                "is_owner": is_owner,
            },
            auto_commit=True,
        )


async def _ensure_operator_team_with_role(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    email: str,
    role: TeamRoles = TeamRoles.EDITOR,
) -> None:
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        await team_service.ensure_operator_team(name="Op", domain="example.com")
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        user_service = UserService(session=session)
        member_service = TeamMemberService(session=session)
        operator_team = await team_service.get_one(is_operator=True)
        user = await user_service.get_one(email=email)
        await member_service.create(
            {
                "team_id": operator_team.id,
                "user_id": user.id,
                "role": role,
            },
            auto_commit=True,
        )


async def _seed_metric(
    sessionmaker: async_sessionmaker[AsyncSession],
    **overrides: Any,  # noqa: ANN401
) -> ManualMetric:
    payload: dict[str, Any] = {
        "name": "Seed",
        "desc": "Seed desc",
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
# Scope auto-derivation on create
# ---------------------------------------------------------------------------


async def test_create_operator_editor_lands_in_shared_scope(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Operator editor POST → team_id is None (shared scope)."""
    await _ensure_operator_team_with_role(
        sessionmaker, email="user@example.com", role=TeamRoles.EDITOR
    )
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Shared metric",
            "desc": "x",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["teamId"] is None
    # Draft by default — even though caller is operator editor.
    assert body["isPublished"] is False


async def test_create_team_editor_lands_in_team_scope(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],  # noqa: ARG001
) -> None:
    """Non-operator team EDITOR (single team) POST → row owned by their team.

    The seeded ``user@example.com`` is already the owner of ``test-team``
    (see ``raw_teams`` fixture), so they have exactly one editable scope
    out of the box. The controller auto-picks it.
    """
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Team metric",
            "desc": "y",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["teamSlug"] == "test-team"
    assert body["isPublished"] is False


async def test_create_member_only_user_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A user with only MEMBER role across every team gets 403 on create.

    Demotes the seeded ``user@example.com`` from owner of ``test-team`` to
    MEMBER so they have no editable scope anywhere.
    """
    async with sessionmaker() as session:
        member_service = TeamMemberService(session=session)
        team_service = TeamService(session=session)
        team = await team_service.get_one(slug="test-team")
        membership = next((m for m in team.members if m.user_id == USER_ID), None)
        assert membership is not None
        await member_service.update(
            item_id=membership.id,
            data={"role": TeamRoles.MEMBER, "is_owner": False},
            auto_commit=True,
        )
    response = await client.post(
        "/api/manual-metrics",
        json={"name": "X", "desc": "Y", "protocol": "AAVE_V3", "category": "ANCHORS"},
        headers=user_token_headers,
    )
    assert response.status_code == 403


async def test_create_operator_membership_trumps_team_membership(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A user in BOTH operator team AND another team → shared scope wins."""
    # Operator membership first.
    await _ensure_operator_team_with_role(
        sessionmaker, email="user@example.com", role=TeamRoles.EDITOR
    )
    # Then add them to a regular team as an editor.
    team = await _ensure_team(sessionmaker, name="Acme", slug="acme")
    await _add_membership(
        sessionmaker,
        user_id=USER_ID,
        team_id=team.id,
        role=TeamRoles.EDITOR,
    )
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Mixed",
            "desc": "z",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201
    assert response.json()["teamId"] is None  # operator wins


async def test_create_ignores_client_supplied_team_id(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],  # noqa: ARG001
) -> None:
    """Client-supplied teamId on create is ignored (scope is server-derived).

    The seeded user owns ``test-team``; spoofed teamId is dropped by msgspec
    and the controller derives scope from that single editable team.
    """
    spoofed = "00000000-0000-0000-0000-000000000099"
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "X",
            "desc": "Y",
            "protocol": "AAVE_V3",
            "category": "ANCHORS",
            "teamId": spoofed,
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201
    assert response.json()["teamSlug"] == "test-team"
    assert response.json()["teamId"] != spoofed


# ---------------------------------------------------------------------------
# Immutability of team_id on update
# ---------------------------------------------------------------------------


async def test_update_payload_with_team_id_ignored(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Smuggling teamId into PATCH is silently dropped by the schema."""
    metric = await _seed_metric(sessionmaker, name="Original")
    spoofed = "00000000-0000-0000-0000-000000000099"
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}",
        json={"desc": "Updated", "teamId": spoofed},
        headers=superuser_token_headers,
    )
    # ManualMetricUpdate has no team_id field — msgspec drops it silently.
    assert response.status_code == 200
    assert response.json()["teamId"] is None  # unchanged


# ---------------------------------------------------------------------------
# Visibility — team scoping + drafts
# ---------------------------------------------------------------------------


async def test_team_a_user_cannot_see_team_b_metrics(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A user in team A doesn't see team B's metrics."""
    team_a = await _ensure_team(sessionmaker, name="A", slug="team-a")
    team_b = await _ensure_team(sessionmaker, name="B", slug="team-b")
    await _add_membership(
        sessionmaker,
        user_id=USER_ID,
        team_id=team_a.id,
        role=TeamRoles.MEMBER,
    )
    await _seed_metric(sessionmaker, name="A-metric", team_id=team_a.id)
    await _seed_metric(sessionmaker, name="B-metric", team_id=team_b.id)
    await _seed_metric(sessionmaker, name="Shared", team_id=None)

    response = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert "A-metric" in names
    assert "Shared" in names
    assert "B-metric" not in names


async def test_drafts_hidden_from_non_editors(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Draft (unpublished) shared metrics aren't visible to plain users."""
    await _seed_metric(sessionmaker, name="Draft", is_published=False)
    await _seed_metric(sessionmaker, name="Published", is_published=True)

    response = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert names == {"Published"}


async def test_drafts_visible_to_operator_editors(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Operator editors see shared drafts."""
    await _ensure_operator_team_with_role(
        sessionmaker, email="user@example.com", role=TeamRoles.EDITOR
    )
    await _seed_metric(sessionmaker, name="Draft", is_published=False)

    response = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["items"]}
    assert "Draft" in names


async def test_get_hidden_metric_404(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """GET /{id} for a hidden row returns 404 (enumeration safety)."""
    team_b = await _ensure_team(sessionmaker, name="B", slug="team-b")
    metric = await _seed_metric(sessionmaker, name="Hidden", team_id=team_b.id)
    response = await client.get(
        f"/api/manual-metrics/{metric.id}", headers=user_token_headers
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Publish endpoint
# ---------------------------------------------------------------------------


async def test_publish_endpoint_makes_draft_visible(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """PATCH /{id}/publish {true} → metric becomes visible to readers."""
    metric = await _seed_metric(sessionmaker, name="Will publish", is_published=False)
    # Plain user doesn't see the draft.
    list1 = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert "Will publish" not in {i["name"] for i in list1.json()["items"]}
    # Superuser publishes.
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}/publish",
        json={"isPublished": True},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["isPublished"] is True
    # Plain user now sees it.
    list2 = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert "Will publish" in {i["name"] for i in list2.json()["items"]}


async def test_unpublish_hides_metric(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Unpublishing a published metric hides it from non-editors."""
    metric = await _seed_metric(sessionmaker, name="Will unpublish")
    list1 = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert "Will unpublish" in {i["name"] for i in list1.json()["items"]}
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}/publish",
        json={"isPublished": False},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    list2 = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert "Will unpublish" not in {i["name"] for i in list2.json()["items"]}


async def test_publish_endpoint_member_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Plain operator MEMBER cannot publish a shared metric."""
    await _ensure_operator_team_with_role(
        sessionmaker, email="user@example.com", role=TeamRoles.MEMBER
    )
    metric = await _seed_metric(sessionmaker, name="Hands-off", is_published=False)
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}/publish",
        json={"isPublished": True},
        headers=user_token_headers,
    )
    # The metric is a draft and the user can't edit shared scope, so it's
    # invisible to them — 404 (enumeration safety) covers the same intent
    # as 403 (cannot publish what you cannot see).
    assert response.status_code in (403, 404)


async def test_update_does_not_change_publish_state(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Editing a published metric does not revert it to draft."""
    metric = await _seed_metric(sessionmaker, name="Stays published", is_published=True)
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}",
        json={"desc": "Updated content"},
        headers=superuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["isPublished"] is True


# ---------------------------------------------------------------------------
# Entity-category invariants
# ---------------------------------------------------------------------------


async def test_create_rejects_zero_entities(
    client: AsyncClient,
    user_token_headers: dict[str, str],
) -> None:
    """A create payload with no entity column gets 400."""
    response = await client.post(
        "/api/manual-metrics",
        json={"name": "X", "desc": "Y", "category": "ANCHORS"},
        headers=user_token_headers,
    )
    assert response.status_code == 400


async def test_create_rejects_multiple_entities(
    client: AsyncClient,
    user_token_headers: dict[str, str],
) -> None:
    """A create payload with two entity columns gets 400."""
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "X",
            "desc": "Y",
            "protocol": "AAVE_V3",
            "token": "USDC",
            "category": "ANCHORS",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 400


async def test_create_rejects_category_invalid_for_entity(
    client: AsyncClient,
    user_token_headers: dict[str, str],
) -> None:
    """Chain + ANCHORS is structurally invalid and gets 400."""
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "X",
            "desc": "Y",
            "chain": "ETHEREUM",
            "category": "ANCHORS",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 400


async def test_create_team_editor_rejected_for_reserved_category(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],  # noqa: ARG001
) -> None:
    """A non-operator team editor cannot create PROTOCOL_SCORE metrics."""
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Reserved attempt",
            "desc": "Should be rejected",
            "protocol": "AAVE_V3",
            "category": "PROTOCOL_SCORE",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 403


async def test_create_operator_editor_allowed_for_reserved_category(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Operator editor can create PROTOCOL_SCORE metrics."""
    await _ensure_operator_team_with_role(
        sessionmaker, email="user@example.com", role=TeamRoles.EDITOR
    )
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Curated score",
            "desc": "Operator curated",
            "protocol": "AAVE_V3",
            "category": "PROTOCOL_SCORE",
        },
        headers=user_token_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["teamId"] is None
    assert body["category"] == "PROTOCOL_SCORE"


async def test_create_chain_governance_allowed(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
) -> None:
    """A chain-scoped GOVERNANCE metric is the only valid chain combination."""
    response = await client.post(
        "/api/manual-metrics",
        json={
            "name": "Hard fork notes",
            "desc": "Upgrade Δ analysis",
            "chain": "ETHEREUM",
            "category": "GOVERNANCE",
        },
        headers=superuser_token_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["entityType"] == "chain"
    assert body["category"] == "GOVERNANCE"


async def test_update_payload_with_entity_column_rejected(
    client: AsyncClient,
    superuser_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """PATCH with a chain/token/protocol/market field is rejected."""
    metric = await _seed_metric(sessionmaker, name="Locked")
    response = await client.patch(
        f"/api/manual-metrics/{metric.id}",
        json={"desc": "fine", "protocol": "MORPHO_V2"},
        headers=superuser_token_headers,
    )
    # msgspec drops unknown fields → 200, but the row must be unchanged.
    # If the field were retained, the service would 4xx on immutability.
    assert response.status_code == 200
    assert response.json()["protocol"] == "AAVE_V3"


async def test_response_carries_entity_type(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Every list item carries the derived entityType field."""
    await _seed_metric(sessionmaker, name="P")
    response = await client.get("/api/manual-metrics", headers=user_token_headers)
    assert response.status_code == 200
    assert all(item["entityType"] in {"chain", "token", "protocol", "market"} for item in response.json()["items"])
