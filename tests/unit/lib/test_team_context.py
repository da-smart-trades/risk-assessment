# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Effective-team resolution helpers.

Covers the two pieces behind "a team profile is always active":

* :func:`current_team_id_from_session` — reads the team id back out of
  the session, tolerating the in-request struct, the round-tripped
  camelCase dict (the shape that previously made every reader return
  ``None``), and the legacy snake_case dict.
* :func:`select_default_team` — picks the default active membership
  (owned first, else earliest), excluding the operator team.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from litestar.serialization import decode_json, encode_json

from cert_ra.api.domain.teams.schemas import CurrentTeam
from cert_ra.api.lib.team_context import (
    current_team_id_from_session,
    select_default_team,
)

if TYPE_CHECKING:
    from cert_ra.db.models import TeamMember, User


def _member(
    *, owner: bool, operator: bool, created_at: datetime, name: str = "T"
) -> TeamMember:
    """A TeamMember-shaped stand-in for the attributes the helper reads."""
    team_id = uuid4()
    return cast(
        "TeamMember",
        SimpleNamespace(
            team_id=team_id,
            is_owner=owner,
            created_at=created_at,
            team=SimpleNamespace(is_operator=operator, name=name),
        ),
    )


def _user(*members: TeamMember) -> User:
    """A User-shaped stand-in carrying the given memberships."""
    return cast("User", SimpleNamespace(teams=list(members)))


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


# ---------------------------------------------------------------------------
# current_team_id_from_session
# ---------------------------------------------------------------------------


def test_session_none_returns_none() -> None:
    assert current_team_id_from_session(None) is None
    assert current_team_id_from_session({}) is None


def test_reads_camelcase_dict() -> None:
    """The round-tripped session shape uses ``teamId`` — must be honoured."""
    tid = uuid4()
    session = {"currentTeam": {"teamId": str(tid), "teamName": "X"}}
    assert current_team_id_from_session(session) == tid


def test_reads_legacy_snake_case_dict() -> None:
    tid = uuid4()
    session = {"currentTeam": {"team_id": str(tid)}}
    assert current_team_id_from_session(session) == tid


def test_reads_in_request_struct() -> None:
    """Before serialization the value is a CurrentTeam struct."""
    tid = uuid4()
    session = {"currentTeam": CurrentTeam(team_id=tid, team_name="X")}
    assert current_team_id_from_session(session) == tid


def test_session_round_trip_through_serializer() -> None:
    """End-to-end: a struct stored in the session decodes to a readable id.

    This is the exact path that previously failed — the struct serializes
    to camelCase, which the old snake_case readers could not see.
    """
    tid = uuid4()
    stored = {"currentTeam": CurrentTeam(team_id=tid, team_name="312 Cayuga")}
    round_tripped = decode_json(encode_json(stored))
    assert current_team_id_from_session(round_tripped) == tid


def test_invalid_id_returns_none() -> None:
    assert (
        current_team_id_from_session({"currentTeam": {"teamId": "not-a-uuid"}}) is None
    )


# ---------------------------------------------------------------------------
# select_default_team
# ---------------------------------------------------------------------------


def test_no_teams_returns_none() -> None:
    assert select_default_team(_user()) is None


def test_single_non_operator_team_is_default() -> None:
    m = _member(owner=False, operator=False, created_at=_dt(5))
    assert select_default_team(_user(m)) is m


def test_owned_team_wins_over_earlier_membership() -> None:
    earlier_not_owned = _member(owner=False, operator=False, created_at=_dt(1))
    later_owned = _member(owner=True, operator=False, created_at=_dt(9))
    assert select_default_team(_user(earlier_not_owned, later_owned)) is later_owned


def test_earliest_membership_when_none_owned() -> None:
    later = _member(owner=False, operator=False, created_at=_dt(9))
    earlier = _member(owner=False, operator=False, created_at=_dt(2))
    assert select_default_team(_user(later, earlier)) is earlier


def test_operator_team_excluded() -> None:
    operator = _member(owner=True, operator=True, created_at=_dt(1))
    customer = _member(owner=False, operator=False, created_at=_dt(3))
    assert select_default_team(_user(operator, customer)) is customer


def test_only_operator_team_returns_none() -> None:
    operator = _member(owner=True, operator=True, created_at=_dt(1))
    assert select_default_team(_user(operator)) is None
