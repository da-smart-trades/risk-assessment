# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import SQLAlchemyAsyncRepositoryService
from sqlalchemy import update

from cert_ra.db.models import TeamMember
from cert_ra.db.models.team_roles import TeamRoles

if TYPE_CHECKING:
    from uuid import UUID

__all__ = ("TeamMemberService",)


class TeamMemberService(SQLAlchemyAsyncRepositoryService[TeamMember]):
    """Team Member Service."""

    class Repo(SQLAlchemyAsyncRepository[TeamMember]):
        """Team Member SQLAlchemy Repository."""

        model_type = TeamMember

    repository_type = Repo

    async def transfer_ownership(
        self, *, team_id: UUID, new_owner_member_id: UUID
    ) -> TeamMember:
        """Make ``new_owner_member_id`` the team's sole owner.

        Clears ``is_owner`` on every current owner of the team, then sets
        it on the target member and bumps their role to ``ADMIN`` (an owner
        always holds full permissions). A team has exactly one owner; this
        is the only sanctioned way to move it.

        Args:
            team_id: The team whose ownership is moving.
            new_owner_member_id: The ``TeamMember`` row to promote.

        Returns:
            The promoted ``TeamMember``.

        Raises:
            ValueError: If the target member does not belong to ``team_id``.
        """
        member = await self.get(new_owner_member_id)
        if member.team_id != team_id:
            msg = "Member does not belong to this team."
            raise ValueError(msg)
        await self.repository.session.execute(
            update(TeamMember)
            .where(
                TeamMember.team_id == team_id,
                TeamMember.is_owner.is_(True),
                TeamMember.id != new_owner_member_id,
            )
            .values(is_owner=False)
        )
        member.is_owner = True
        member.role = TeamRoles.ADMIN
        await self.repository.session.flush()
        return member
