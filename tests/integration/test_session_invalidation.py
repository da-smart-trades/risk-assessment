# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Tests for real multi-session invalidation (deferred item #6).

``invalidate_other_user_sessions`` deletes a user's other server-side
sessions on a credential change by scanning the session store's
namespace and matching the JSON blob's ``user_id`` (email).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from litestar.serialization import encode_json
from sqlalchemy import select

from cert_ra.api.lib.session_rotation import invalidate_other_user_sessions
from cert_ra.db.models import SessionStore
from cert_ra.settings.api import get_app_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


async def _seed_sessions(session: AsyncSession) -> str:
    """Insert sessions: two for a@x.com, one for b@x.com. Returns namespace."""
    namespace = get_app_settings().slug
    expires = datetime.now(UTC) + timedelta(hours=1)
    for key, email in (("a1", "a@x.com"), ("a2", "a@x.com"), ("b1", "b@x.com")):
        session.add(
            SessionStore(
                key=key,
                namespace=namespace,
                value=encode_json({"user_id": email, "auth_method": "password"}),
                expires_at=expires,
            )
        )
    await session.commit()
    return namespace


async def _keys(session: AsyncSession, namespace: str) -> set[str]:
    rows = await session.scalars(
        select(SessionStore.key).where(SessionStore.namespace == namespace)
    )
    return set(rows)


async def test_invalidates_other_sessions_preserving_current(
    session: AsyncSession,
) -> None:
    """Other sessions for the user are deleted; the current one survives."""
    namespace = await _seed_sessions(session)
    count = await invalidate_other_user_sessions(
        session, user_email="a@x.com", current_session_key="a1"
    )
    await session.commit()
    assert count == 1
    keys = await _keys(session, namespace)
    assert "a2" not in keys  # the user's other session was killed
    assert {"a1", "b1"} <= keys  # current + the other user untouched


async def test_wipe_all_sessions_when_no_current(session: AsyncSession) -> None:
    """current_session_key=None wipes ALL of the user's sessions."""
    namespace = await _seed_sessions(session)
    count = await invalidate_other_user_sessions(
        session, user_email="a@x.com", current_session_key=None
    )
    await session.commit()
    assert count == 2
    keys = await _keys(session, namespace)
    assert "a1" not in keys
    assert "a2" not in keys
    assert "b1" in keys  # other user untouched


async def test_no_match_returns_zero(session: AsyncSession) -> None:
    """A user with no stored sessions invalidates nothing."""
    await _seed_sessions(session)
    count = await invalidate_other_user_sessions(
        session, user_email="nobody@x.com", current_session_key=None
    )
    assert count == 0
