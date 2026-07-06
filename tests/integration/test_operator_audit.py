# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for operator audit + FK behavior (PR-8, Control 3).

An ``operator_tenant_admin`` write against a customer team records an
``OperatorAudit`` row synchronously in the action's transaction. The
actor FK is RESTRICT; the target FKs are SET NULL so the row survives
customer churn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from litestar.serialization import encode_json
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from cert_ra.api.lib.operator_audit import OperatorAction
from cert_ra.db.models import (
    OperatorAudit,
    SessionStore,
    Team,
    TeamMember,
    TeamRoles,
)
from cert_ra.settings.api import get_app_settings

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

TEST_TEAM_ID = "97108ac1-ffcb-411d-8b1e-d9183399f63b"
OWNER_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2"
SUPERUSER_ID = "97108ac1-ffcb-411d-8b1e-d9183399f63b"
SUPPORT_USER_ID = "5ef29f3c-3560-4d15-ba6b-a2e5c721e999"  # test@test.com


async def test_promote_support_to_tenant_admin(
    client: AsyncClient,
    session: AsyncSession,
    superuser_token_headers: dict[str, str],
) -> None:
    """AC #30: a tenant-admin promotes a support member; role + audit set."""
    team = Team(name="Op Promote", slug="op-promote", is_operator=True)
    session.add(team)
    await session.flush()
    member = TeamMember(
        user_id=UUID(SUPPORT_USER_ID),
        team_id=team.id,
        role=TeamRoles.OPERATOR_SUPPORT,
    )
    session.add(member)
    # Seed a live session for the promoted user (test@test.com).
    namespace = get_app_settings().slug
    session.add(
        SessionStore(
            key="promoted-sess",
            namespace=namespace,
            value=encode_json({"user_id": "test@test.com"}),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await session.commit()

    resp = await client.post(
        f"/admin/operator/members/{member.id}/promote/",
        headers=superuser_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code in (200, 303)

    await session.refresh(member)
    assert member.role == TeamRoles.OPERATOR_TENANT_ADMIN
    # AC #30 fresh re-auth: the promoted user's session was invalidated.
    remaining = await session.scalar(
        select(func.count())
        .select_from(SessionStore)
        .where(SessionStore.key == "promoted-sess")
    )
    assert remaining == 0
    count = await session.scalar(
        select(func.count())
        .select_from(OperatorAudit)
        .where(
            OperatorAudit.action == "promote_to_tenant_admin",
            OperatorAudit.target_user_id == UUID(SUPPORT_USER_ID),
        )
    )
    assert count == 1


async def test_operator_recovery_action_records_audit(
    client: AsyncClient,
    session: AsyncSession,
    superuser_token_headers: dict[str, str],
) -> None:
    """AC #31: an operator recovery write produces an OperatorAudit row."""
    member = await session.scalar(
        select(TeamMember).where(
            TeamMember.team_id == UUID(TEST_TEAM_ID),
            TeamMember.user_id == UUID(OWNER_ID),
        )
    )
    assert member is not None
    resp = await client.post(
        f"/admin/teams/{TEST_TEAM_ID}/members/{member.id}/reset-mfa/",
        headers=superuser_token_headers,
        follow_redirects=False,
    )
    assert resp.status_code in (200, 303)

    count = await session.scalar(
        select(func.count())
        .select_from(OperatorAudit)
        .where(
            OperatorAudit.action == OperatorAction.RESET_MFA_ONLY,
            OperatorAudit.target_team_id == UUID(TEST_TEAM_ID),
            OperatorAudit.target_user_id == UUID(OWNER_ID),
        )
    )
    assert count == 1


async def test_audit_target_team_set_null_on_team_delete(
    session: AsyncSession,
) -> None:
    """AC #121: deleting the target team nulls target_team_id; row survives."""
    row = OperatorAudit(
        actor_user_id=UUID(SUPERUSER_ID),
        actor_session_id="sess-1",
        actor_ip="127.0.0.1",
        action=OperatorAction.FORCE_UNLOCK,
        target_team_id=UUID(TEST_TEAM_ID),
        target_user_id=UUID(OWNER_ID),
        payload={"k": "v"},
    )
    session.add(row)
    await session.commit()

    team = await session.get(Team, TEST_TEAM_ID)
    assert team is not None
    await session.delete(team)
    await session.commit()

    await session.refresh(row)
    assert row.target_team_id is None  # SET NULL
    assert row.action == OperatorAction.FORCE_UNLOCK  # row survived


async def test_audit_actor_restrict_blocks_operator_delete(
    session: AsyncSession,
) -> None:
    """AC #120: an operator with audit history cannot be deleted (RESTRICT)."""
    from sqlalchemy.exc import IntegrityError

    from cert_ra.db.models import User

    row = OperatorAudit(
        actor_user_id=UUID(OWNER_ID),
        actor_session_id="sess-2",
        actor_ip="127.0.0.1",
        action=OperatorAction.RESET_MFA_ONLY,
        target_team_id=None,
        target_user_id=None,
        payload={"ts": datetime.now(UTC).isoformat()},
    )
    session.add(row)
    await session.commit()

    actor = await session.get(User, OWNER_ID)
    assert actor is not None
    await session.delete(actor)
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_promotion_page_lists_members(
    client: AsyncClient, superuser_inertia_headers: dict[str, str]
) -> None:
    """The promotion page lists operator-team members (operator-gated GET)."""
    resp = await client.get(
        "/admin/operator/promotion/",
        headers=superuser_inertia_headers,
        follow_redirects=False,
    )
    assert resp.status_code == 200


async def test_promotion_page_forbidden_for_non_operator(
    client: AsyncClient, user_token_headers: dict[str, str]
) -> None:
    """A non-operator is denied the promotion page (redirected, not 200)."""
    resp = await client.get(
        "/admin/operator/promotion/",
        headers=user_token_headers,
        follow_redirects=False,
    )
    # Page routes redirect on guard denial rather than returning a raw 403.
    assert resp.status_code in (302, 303, 307)
    assert resp.status_code != 200


async def _insert_row(session: AsyncSession) -> OperatorAudit:
    row = OperatorAudit(
        actor_user_id=UUID(OWNER_ID),
        actor_session_id="sess-immutable",
        actor_ip="127.0.0.1",
        action=OperatorAction.RESET_MFA_ONLY,
        target_team_id=UUID(TEST_TEAM_ID),
        target_user_id=UUID(OWNER_ID),
        payload={"k": "v"},
    )
    session.add(row)
    await session.commit()
    return row


async def test_operator_audit_update_rejected(session: AsyncSession) -> None:
    """AC #32: the app cannot UPDATE an operator_audit row (append-only)."""
    row = await _insert_row(session)
    row.action = "tampered"
    row.payload = {"k": "tampered"}
    with pytest.raises(DBAPIError):
        await session.commit()
    await session.rollback()


async def test_operator_audit_delete_rejected(session: AsyncSession) -> None:
    """AC #32: the app cannot DELETE an operator_audit row (append-only)."""
    row = await _insert_row(session)
    await session.delete(row)
    with pytest.raises(DBAPIError):
        await session.commit()
    await session.rollback()
