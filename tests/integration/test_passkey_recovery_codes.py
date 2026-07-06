# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Passkey enrollment issues recovery codes on the first factor only.

Regression test for the ordering bug where the just-added ``UserPasskey``
autoflushed before the first-factor check, so ``_has_any_factor`` counted
the credential being enrolled and reported "not first factor" —
suppressing recovery codes on first-passkey setup.

The WebAuthn ceremony is mocked (``verify_registration``) so the test
exercises the controller's issuance logic without a real authenticator.
``/passkey/begin`` runs for real to seed the pending-challenge session key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from cert_ra.api.lib import crypt
from cert_ra.db.models import User

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_PW = "Test_Passkey_9!"
_VERIFY_TARGET = "cert_ra.api.domain.accounts.controllers._mfa_v2.verify_registration"


@dataclass
class _FakeVerified:
    """Stand-in for ``VerifiedRegistration`` (the fields ``passkey_finish`` reads)."""

    credential_id: bytes
    public_key: bytes
    sign_count: int
    aaguid: str | None


async def _make_user(session: AsyncSession, email: str) -> None:
    session.add(
        User(
            email=email,
            hashed_password=await crypt.get_password_hash(_PW),
            is_active=True,
            is_verified=True,
        )
    )
    await session.commit()


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    """Log in and return CSRF headers; the client cookie jar holds the session."""
    client.cookies.clear()
    await client.get("/login")
    csrf = client.cookies.get("XSRF-TOKEN") or ""
    await client.post(
        "/login/",
        json={"username": email, "password": _PW},
        headers={"X-XSRF-TOKEN": csrf, "Content-Type": "application/json"},
        follow_redirects=False,
    )
    csrf = client.cookies.get("XSRF-TOKEN") or csrf
    return {"X-XSRF-TOKEN": csrf, "Content-Type": "application/json"}


async def _enroll_passkey(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    credential_id: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    """Run begin + (mocked) finish; return the recovery codes from the response."""
    begin = await client.post(
        "/settings/security/mfa/passkey/begin",
        json={"deviceName": "Device"},
        headers=headers,
        follow_redirects=False,
    )
    assert begin.status_code in (200, 201), begin.text
    monkeypatch.setattr(
        _VERIFY_TARGET,
        lambda *_a, **_k: _FakeVerified(credential_id, b"public-key", 0, None),
    )
    finish = await client.post(
        "/settings/security/mfa/passkey/finish",
        json={"deviceName": "Device", "responseJson": "{}"},
        headers=headers,
        follow_redirects=False,
    )
    assert finish.status_code in (200, 201), finish.text
    return finish.json()["codes"]


async def test_first_passkey_issues_recovery_codes(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first passkey (no prior factor) must return recovery codes."""
    await _make_user(session, "passkey-first@example.com")
    headers = await _login(client, "passkey-first@example.com")
    codes = await _enroll_passkey(
        client, headers, credential_id=b"cred-1", monkeypatch=monkeypatch
    )
    assert len(codes) > 0


async def test_second_passkey_does_not_reissue_codes(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second passkey (a factor already exists) issues no new codes."""
    await _make_user(session, "passkey-second@example.com")
    headers = await _login(client, "passkey-second@example.com")
    first = await _enroll_passkey(
        client, headers, credential_id=b"cred-a", monkeypatch=monkeypatch
    )
    assert len(first) > 0
    second = await _enroll_passkey(
        client, headers, credential_id=b"cred-b", monkeypatch=monkeypatch
    )
    assert second == []
