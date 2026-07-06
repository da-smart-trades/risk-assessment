# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Integration tests for saved dashboards (named home pages) + favorites.

Covers dashboard CRUD, the single-default invariant, dashboard-scoped favorite
pinning (auto + PROTOCOL_SCORE manual), ownership enforcement, team sharing
visibility, and the Inertia dashboard page's board selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from cert_ra.api.domain.teams.services import TeamMemberService
from cert_ra.db.models import ManualMetric, TeamRoles
from cert_ra.types import MetricCategory, ProtocolType, TokenType

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.anyio

USER_ID = UUID("5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2")  # user@example.com
SUPERUSER_ID = UUID("97108ac1-ffcb-411d-8b1e-d9183399f63b")  # superuser@example.com
TEST_TEAM_ID = UUID(
    "97108ac1-ffcb-411d-8b1e-d9183399f63b"
)  # "test-team", owned by USER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_dashboard(
    client: AsyncClient, headers: dict[str, str], name: str
) -> dict[str, Any]:
    resp = await client.post("/api/dashboards", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _add_membership(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    team_id: UUID,
    role: TeamRoles = TeamRoles.MEMBER,
) -> None:
    async with sessionmaker() as session:
        member_service = TeamMemberService(session=session)
        await member_service.create(
            {"team_id": team_id, "user_id": user_id, "role": role},
            auto_commit=True,
        )


async def _seed_manual_metric(
    sessionmaker: async_sessionmaker[AsyncSession],
    **overrides: Any,  # noqa: ANN401
) -> ManualMetric:
    payload: dict[str, Any] = {
        "name": "Seed",
        "desc": "Seed desc",
        "protocol": ProtocolType.AAVE_V3,
        "category": MetricCategory.PROTOCOL_SCORE,
        "is_published": True,
        "created_by": USER_ID,
        "updated_by": USER_ID,
    }
    payload.update(overrides)
    async with sessionmaker() as session:
        metric = ManualMetric(**payload)
        session.add(metric)
        await session.commit()
        await session.refresh(metric)
        return metric


# ---------------------------------------------------------------------------
# CRUD + default invariant
# ---------------------------------------------------------------------------


async def test_first_dashboard_is_default_second_is_not(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    first = await _create_dashboard(client, user_token_headers, "Alpha")
    second = await _create_dashboard(client, user_token_headers, "Beta")

    assert first["isDefault"] is True
    assert first["isOwner"] is True
    assert first["visibility"] == "private"
    assert second["isDefault"] is False


async def test_list_returns_owned_dashboards(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    await _create_dashboard(client, user_token_headers, "Alpha")
    await _create_dashboard(client, user_token_headers, "Beta")

    resp = await client.get("/api/dashboards", headers=user_token_headers)
    assert resp.status_code == 200
    names = {d["name"] for d in resp.json()}
    assert {"Alpha", "Beta"} <= names


async def test_duplicate_name_rejected(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    await _create_dashboard(client, user_token_headers, "Alpha")
    # The unique (owner, name) constraint surfaces as an InertiaBack redirect
    # (flash error), the app-wide convention for constraint violations.
    resp = await client.post(
        "/api/dashboards", json={"name": "Alpha"}, headers=user_token_headers
    )
    assert resp.status_code != 201
    listing = (await client.get("/api/dashboards", headers=user_token_headers)).json()
    assert sum(1 for d in listing if d["name"] == "Alpha") == 1


async def test_blank_name_rejected(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    resp = await client.post(
        "/api/dashboards", json={"name": "   "}, headers=user_token_headers
    )
    assert resp.status_code != 201
    listing = (await client.get("/api/dashboards", headers=user_token_headers)).json()
    assert listing == []


async def test_rename_dashboard(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.patch(
        f"/api/dashboards/{board['id']}",
        json={"name": "Renamed"},
        headers=user_token_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"


async def test_set_default_moves_default(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    first = await _create_dashboard(client, user_token_headers, "Alpha")
    second = await _create_dashboard(client, user_token_headers, "Beta")

    resp = await client.patch(
        f"/api/dashboards/{second['id']}",
        json={"isDefault": True},
        headers=user_token_headers,
    )
    assert resp.status_code == 200

    listing = {
        d["id"]: d
        for d in (
            await client.get("/api/dashboards", headers=user_token_headers)
        ).json()
    }
    assert listing[second["id"]]["isDefault"] is True
    assert listing[first["id"]]["isDefault"] is False


async def test_delete_promotes_new_default(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    first = await _create_dashboard(client, user_token_headers, "Alpha")  # default
    second = await _create_dashboard(client, user_token_headers, "Beta")

    resp = await client.delete(
        f"/api/dashboards/{first['id']}", headers=user_token_headers
    )
    assert resp.status_code in (200, 204)

    listing = {
        d["id"]: d
        for d in (
            await client.get("/api/dashboards", headers=user_token_headers)
        ).json()
    }
    assert first["id"] not in listing
    assert listing[second["id"]]["isDefault"] is True


# ---------------------------------------------------------------------------
# Favorite pinning
# ---------------------------------------------------------------------------


async def test_pin_auto_favorite_and_list(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/auto",
        json={"metricType": "TVL", "chain": "ETHEREUM"},
        headers=user_token_headers,
    )
    assert resp.status_code == 201, resp.text
    fav = resp.json()
    assert fav["metricType"] == "TVL"
    assert fav["dashboardId"] == board["id"]

    items = (
        await client.get(
            f"/api/dashboards/{board['id']}/favorites", headers=user_token_headers
        )
    ).json()
    assert [i["id"] for i in items] == [fav["id"]]


async def test_pin_duplicate_auto_favorite_rejected(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    payload = {"metricType": "TVL", "chain": "ETHEREUM"}
    first = await client.post(
        f"/api/dashboards/{board['id']}/favorites/auto",
        json=payload,
        headers=user_token_headers,
    )
    assert first.status_code == 201
    dup = await client.post(
        f"/api/dashboards/{board['id']}/favorites/auto",
        json=payload,
        headers=user_token_headers,
    )
    assert dup.status_code != 201
    items = (
        await client.get(
            f"/api/dashboards/{board['id']}/favorites", headers=user_token_headers
        )
    ).json()
    assert len(items) == 1


async def test_pin_manual_protocol_score_favorite(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_manual_metric(
        sessionmaker
    )  # shared, published, PROTOCOL_SCORE
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/manual",
        json={"manualMetricId": str(metric.id)},
        headers=user_token_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["manualMetricId"] == str(metric.id)


async def test_pin_manual_token_score_favorite(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Shared, published TOKEN_SCORE summary row — the token PD card. It is
    # favoritable just like PROTOCOL_SCORE, and resolves to a "token" card.
    metric = await _seed_manual_metric(
        sessionmaker,
        protocol=None,
        token=TokenType.AAVE,
        category=MetricCategory.TOKEN_SCORE,
        sub_category="SUMMARY",
        value="3.63%",
        name="Probability of default",
    )
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/manual",
        json={"manualMetricId": str(metric.id)},
        headers=user_token_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["manualMetricId"] == str(metric.id)

    items = (
        await client.get(
            f"/api/dashboards/{board['id']}/favorites", headers=user_token_headers
        )
    ).json()
    resolved = next(i for i in items if i["manualMetricId"] == str(metric.id))
    assert resolved["cardKind"] == "token"
    assert resolved["value"] == "3.63%"
    assert resolved["href"] == "/tokens/AAVE/"


async def test_pin_manual_non_protocol_score_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_manual_metric(sessionmaker, category=MetricCategory.ANCHORS)
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/manual",
        json={"manualMetricId": str(metric.id)},
        headers=user_token_headers,
    )
    assert resp.status_code != 201
    items = (
        await client.get(
            f"/api/dashboards/{board['id']}/favorites", headers=user_token_headers
        )
    ).json()
    assert items == []


async def test_delete_favorite(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    fav = (
        await client.post(
            f"/api/dashboards/{board['id']}/favorites/auto",
            json={"metricType": "TVL", "chain": "ETHEREUM"},
            headers=user_token_headers,
        )
    ).json()
    resp = await client.delete(
        f"/api/dashboards/{board['id']}/favorites/{fav['id']}",
        headers=user_token_headers,
    )
    assert resp.status_code in (200, 204)
    items = (
        await client.get(
            f"/api/dashboards/{board['id']}/favorites", headers=user_token_headers
        )
    ).json()
    assert items == []


# ---------------------------------------------------------------------------
# Ownership + sharing
# ---------------------------------------------------------------------------


async def test_cannot_pin_to_another_users_dashboard(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
) -> None:
    board = await _create_dashboard(client, superuser_token_headers, "Owner board")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/auto",
        json={"metricType": "TVL", "chain": "ETHEREUM"},
        headers=user_token_headers,
    )
    assert resp.status_code == 404


async def test_team_shared_dashboard_visible_and_readonly_to_teammate(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # user@example.com owns "test-team"; add superuser as a teammate.
    await _add_membership(sessionmaker, user_id=SUPERUSER_ID, team_id=TEST_TEAM_ID)
    board = await _create_dashboard(client, user_token_headers, "Shared board")
    share = await client.patch(
        f"/api/dashboards/{board['id']}",
        json={"visibility": "team"},
        headers=user_token_headers,
    )
    assert share.status_code == 200, share.text
    assert share.json()["isShared"] is True

    # Teammate sees it, marked as not-owned + shared, with the owner's name.
    listing = (
        await client.get("/api/dashboards", headers=superuser_token_headers)
    ).json()
    shared = next((d for d in listing if d["id"] == board["id"]), None)
    assert shared is not None
    assert shared["isOwner"] is False
    assert shared["isShared"] is True
    assert shared["ownerName"] == "Example User"

    # Teammate cannot edit it (read-only) — patch + pin both 404.
    assert (
        await client.patch(
            f"/api/dashboards/{board['id']}",
            json={"name": "Hijack"},
            headers=superuser_token_headers,
        )
    ).status_code == 404


async def test_delete_favorite_wrong_dashboard_404(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board_a = await _create_dashboard(client, user_token_headers, "A")
    board_b = await _create_dashboard(client, user_token_headers, "B")
    fav = (
        await client.post(
            f"/api/dashboards/{board_a['id']}/favorites/auto",
            json={"metricType": "TVL", "chain": "ETHEREUM"},
            headers=user_token_headers,
        )
    ).json()
    # Deleting via the wrong (but owned) dashboard must 404.
    resp = await client.delete(
        f"/api/dashboards/{board_b['id']}/favorites/{fav['id']}",
        headers=user_token_headers,
    )
    assert resp.status_code == 404


async def test_rename_blank_rejected(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.patch(
        f"/api/dashboards/{board['id']}",
        json={"name": "   "},
        headers=user_token_headers,
    )
    assert resp.status_code != 200


async def test_delete_only_dashboard(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Solo")
    resp = await client.delete(
        f"/api/dashboards/{board['id']}", headers=user_token_headers
    )
    assert resp.status_code in (200, 204)
    listing = (await client.get("/api/dashboards", headers=user_token_headers)).json()
    assert listing == []


async def test_unshare_dashboard_clears_team(
    client: AsyncClient,
    user_token_headers: dict[str, str],
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Toggle")
    shared = await client.patch(
        f"/api/dashboards/{board['id']}",
        json={"visibility": "team"},
        headers=user_token_headers,
    )
    assert shared.json()["isShared"] is True
    private = await client.patch(
        f"/api/dashboards/{board['id']}",
        json={"visibility": "private"},
        headers=user_token_headers,
    )
    assert private.status_code == 200
    assert private.json()["isShared"] is False
    assert private.json()["visibility"] == "private"


async def test_share_multi_team_without_current_team_forbidden(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # Put user@example.com in a second team; with no session "current team",
    # the ambiguous share target must be refused.
    await _add_membership(
        sessionmaker,
        user_id=USER_ID,
        team_id=UUID("81108ac1-ffcb-411d-8b1e-d91833999999"),  # "simple-team"
    )
    board = await _create_dashboard(client, user_token_headers, "Ambiguous")
    await client.patch(
        f"/api/dashboards/{board['id']}",
        json={"visibility": "team"},
        headers=user_token_headers,
    )
    listing = (await client.get("/api/dashboards", headers=user_token_headers)).json()
    assert next(d for d in listing if d["id"] == board["id"])["isShared"] is False


async def test_pin_manual_nonexistent_rejected(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/manual",
        json={"manualMetricId": "00000000-0000-0000-0000-000000000000"},
        headers=user_token_headers,
    )
    assert resp.status_code != 201


async def test_pin_manual_team_owned_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_manual_metric(sessionmaker, team_id=TEST_TEAM_ID)
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/manual",
        json={"manualMetricId": str(metric.id)},
        headers=user_token_headers,
    )
    assert resp.status_code != 201


async def test_pin_manual_draft_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    metric = await _seed_manual_metric(sessionmaker, is_published=False)
    board = await _create_dashboard(client, user_token_headers, "Alpha")
    resp = await client.post(
        f"/api/dashboards/{board['id']}/favorites/manual",
        json={"manualMetricId": str(metric.id)},
        headers=user_token_headers,
    )
    assert resp.status_code != 201


async def test_private_dashboard_not_visible_to_others(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    superuser_token_headers: dict[str, str],
) -> None:
    board = await _create_dashboard(client, user_token_headers, "Private board")
    listing = (
        await client.get("/api/dashboards", headers=superuser_token_headers)
    ).json()
    assert all(d["id"] != board["id"] for d in listing)


# ---------------------------------------------------------------------------
# Inertia dashboard page board selection
# ---------------------------------------------------------------------------


async def test_dashboard_page_defaults_then_honors_board_param(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    user_inertia_headers: dict[str, str],
) -> None:
    first = await _create_dashboard(client, user_token_headers, "Alpha")  # default
    second = await _create_dashboard(client, user_token_headers, "Beta")

    # No board param -> the default page.
    default_page = await client.get("/dashboard/", headers=user_inertia_headers)
    assert default_page.status_code == 200
    content = default_page.json()["props"]["content"]
    assert content["current"]["id"] == first["id"]
    assert content["canEdit"] is True
    assert {d["id"] for d in content["dashboards"]} >= {first["id"], second["id"]}

    # Explicit board param -> that page.
    picked = await client.get(
        "/dashboard/", params={"board": second["id"]}, headers=user_inertia_headers
    )
    assert picked.json()["props"]["content"]["current"]["id"] == second["id"]


async def test_dashboard_page_autocreates_default_when_none(
    client: AsyncClient, user_inertia_headers: dict[str, str]
) -> None:
    page = await client.get("/dashboard/", headers=user_inertia_headers)
    assert page.status_code == 200
    content = page.json()["props"]["content"]
    assert content["current"]["isDefault"] is True
    assert content["current"]["name"] == "My favorites"
