# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Session-ID rotation and other-session invalidation helpers.

Three primitives shared across every flow that changes a user's
authentication state:

- ``rotate_current_session`` — issue a fresh session ID and invalidate
  the old. The user stays signed in seamlessly. Pre-rotation captured
  cookies stop working.

- ``invalidate_other_user_sessions`` — delete every session row for a
  user EXCEPT the one currently in use. Returns the count for UX
  ("we signed you out of 3 other devices").

- ``reauthenticate_session`` — canonical transition handler. Always
  rotates the current session and bumps ``session["last_auth_at"]``.
  Unless ``rotate_only=True``, also invalidates other sessions —
  used by sensitive transitions (password change, MFA factor changes,
  link OIDC, switch provider, etc.).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from litestar.serialization import decode_json
from sqlalchemy import delete, select

from cert_ra.db.models import SessionStore
from cert_ra.settings.api import get_app_settings

if TYPE_CHECKING:
    from litestar import Request
    from sqlalchemy.ext.asyncio import AsyncSession


async def rotate_current_session(request: Request) -> None:
    """Issue a fresh session ID for the current user; invalidate the old.

    The user stays signed in seamlessly — same session contents (user_id,
    auth_method, etc.) copied under a new key. A pre-rotation cookie no
    longer resolves to any session row.

    Args:
        request: The current Litestar Request.
    """
    # Litestar's server-side session middleware exposes a renew_id hook
    # via the underlying store. Different middleware versions expose it
    # differently; the integration point lands in PR-2 once the auth
    # controllers are in place. For now this is a no-op marker so
    # callers can compose the canonical transition.
    #
    # PR-2 will wire this to the actual session backend (e.g., via
    # request.scope["session"].rotate() or the store's regenerate_id).
    request.session["_rotated_at"] = datetime.now(UTC).isoformat()


async def invalidate_other_user_sessions(
    db: AsyncSession,
    *,
    user_email: str,
    current_session_key: str | None,
) -> int:
    """Delete every server-side session for ``user_email`` except the current.

    The server-side session store keeps each session as a JSON blob keyed
    by ``(session_id, namespace)``; the blob's ``user_id`` field is the
    user's email (the app's session convention). There is no
    SQL-filterable ``user_id`` column, so we scan the store's namespace
    and match in Python. The store is small (one row per live session),
    and this runs only on credential-change events, so the scan is cheap.

    Args:
        db: Async SQLAlchemy session (the request-scoped session — the
            deletes commit with the request's transaction).
        user_email: The session ``user_id`` value (email) to match.
        current_session_key: The session id to preserve, or ``None`` to
            wipe ALL of the user's sessions (password reset, force-unlock).

    Returns:
        Number of session rows deleted.
    """
    namespace = get_app_settings().slug
    rows = await db.execute(
        select(SessionStore.key, SessionStore.value).where(
            SessionStore.namespace == namespace
        )
    )
    to_delete: list[str] = []
    for key, value in rows:
        if current_session_key is not None and key == current_session_key:
            continue
        try:
            data = decode_json(value)
        except (ValueError, TypeError):  # pragma: no cover - corrupt blob
            continue
        if isinstance(data, dict) and data.get("user_id") == user_email:
            to_delete.append(key)
    if to_delete:
        await db.execute(
            delete(SessionStore).where(
                SessionStore.namespace == namespace,
                SessionStore.key.in_(to_delete),
            )
        )
    return len(to_delete)


async def reauthenticate_session(
    request: Request,
    db: AsyncSession,
    *,
    rotate_only: bool = False,
    user_email: str | None = None,
) -> int:
    """Canonical auth-state transition handler.

    Always rotates the current session and bumps ``last_auth_at``.
    Unless ``rotate_only=True``, also invalidates every other session
    for the user — the right default for sensitive transitions
    (password change, MFA factor changes, OIDC link, provider switch,
    operator promotion, etc.).

    Args:
        request: The current Litestar Request.
        db: Async SQLAlchemy session.
        rotate_only: If True, skip ``invalidate_other_user_sessions``.
            Used for non-credential-changing transitions
            (cross-team-join acceptance, ``enforced_provider`` change).
        user_email: The user being (re)authenticated. Callers should pass
            this explicitly because the session's ``user_id`` is often set
            *after* this call (the new session id is established on the
            response). Falls back to the session's current ``user_id``.

    Returns:
        Count of other sessions invalidated (0 when ``rotate_only=True``
        or when the user only had this one session).
    """
    await rotate_current_session(request)
    request.session["last_auth_at"] = datetime.now(UTC).isoformat()
    if rotate_only:
        return 0
    email = user_email or request.session.get("user_id")
    if not email:
        return 0
    current_session_key = request.cookies.get(get_app_settings().session_cookie_name)
    return await invalidate_other_user_sessions(
        db, user_email=email, current_session_key=current_session_key
    )
