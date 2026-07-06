# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Resolve the viewer's *effective* team from the session.

The team switcher writes a ``currentTeam`` value into the server-side
session. That value round-trips through the session serializer, so by
the time a later request reads it back it is a camelCase ``dict``
(``{"teamId": ..., "teamName": ...}``) — not the snake_case shape the
:class:`CurrentTeam` struct uses in Python. Readers must account for
all three shapes:

* an in-request :class:`CurrentTeam` struct (set this same request,
  before serialization),
* the round-tripped camelCase ``dict`` (the common case), and
* a legacy snake_case ``dict``.

On top of that, a signed-in user should *always* have an effective team
when they belong to one — a profile/dashboard scoped to their team must
apply without first clicking the switcher. :func:`select_default_team`
picks that default: the team they own, else their earliest membership,
excluding the internal operator team (which is an admin context, not a
content-owning team).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from cert_ra.db.models import TeamMember, User

__all__ = (
    "current_team_id_from_session",
    "select_default_team",
)


def current_team_id_from_session(session: Mapping[str, Any] | None) -> UUID | None:
    """Extract the active team id from a session ``currentTeam`` value.

    Handles the in-request struct, the round-tripped camelCase dict, and
    a legacy snake_case dict. Returns ``None`` when no team is active.
    """
    raw = (session or {}).get("currentTeam")
    if raw is None:
        return None
    # In-request CurrentTeam struct (not yet serialized to a dict).
    team_id: object = getattr(raw, "team_id", None)
    if team_id is None and isinstance(raw, dict):
        # Round-tripped session value: camelCase first, snake_case fallback.
        team_id = raw.get("teamId") or raw.get("team_id")
    if isinstance(team_id, UUID):
        return team_id
    if isinstance(team_id, str):
        try:
            return UUID(team_id)
        except ValueError:
            return None
    return None


def select_default_team(user: User) -> TeamMember | None:
    """Pick the membership that should be active when none is selected.

    Prefers a team the user owns, tie-breaking by earliest membership
    (``created_at``). The operator team is never the default — it is an
    admin context, and operators view the global default profile.
    Returns ``None`` when the user belongs to no non-operator team.
    """
    candidates = [
        m for m in user.teams if m.team is not None and not m.team.is_operator
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda m: (not bool(m.is_owner), m.created_at))
