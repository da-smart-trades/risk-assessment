# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.filters import OrderBy
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    is_dict_with_field,
    is_dict_without_field,
    schema_dump,
)
from sqlalchemy import ColumnElement, func, or_

from cert_ra.api.lib.token_hashing import hmac_sha256
from cert_ra.db.models import Team, TeamInvitation, TeamMember, TeamRoles
from cert_ra.db.models.team_invitation import InvitationKind
from cert_ra.db.models.user import User  # noqa: TC001
from cert_ra.settings.api import get_email_settings

if TYPE_CHECKING:
    from uuid import UUID

    from advanced_alchemy.service import ModelDictT

    from cert_ra.api.domain.teams.services._team_member import TeamMemberService

__all__ = ("TeamInvitationService",)


class TeamInvitationService(SQLAlchemyAsyncRepositoryService[TeamInvitation]):
    """Team Invitation Service."""

    class Repo(SQLAlchemyAsyncRepository[TeamInvitation]):
        """Team Invitation SQLAlchemy Repository."""

        model_type = TeamInvitation

    repository_type = Repo

    @staticmethod
    def _hash_token(token: str) -> str:
        """Hash a token using HMAC-SHA-256 keyed on the app signing secret.

        Replaces the legacy plain-SHA-256 implementation per the OIDC
        SSO design. HMAC defeats rainbow-table reuse if the DB ever
        leaks; SHA-256 (not argon2id) because the token is high-entropy
        and we need deterministic equality lookup.

        Returns:
            Hexadecimal digest of the HMAC.
        """
        return hmac_sha256(token)

    def _not_expired_filter(self) -> ColumnElement[bool]:
        """SQLAlchemy filter for non-expired invitations.

        Returns:
            Column expression filtering for invitations with no expiry or future expiry.
        """
        return or_(
            TeamInvitation.expires_at.is_(None), TeamInvitation.expires_at > func.now()
        )

    async def to_model_on_create(
        self, data: ModelDictT[TeamInvitation]
    ) -> ModelDictT[TeamInvitation]:
        """Set defaults for new invitations.

        Handles token hashing (if plain token provided) and default expiry.

        Returns:
            Invitation data with hashed token and expiry set.
        """
        data = schema_dump(data)
        if is_dict_with_field(data, "token") and is_dict_without_field(
            data, "token_hash"
        ):
            data["token_hash"] = self._hash_token(data.pop("token"))
        if is_dict_without_field(data, "expires_at"):
            data["expires_at"] = datetime.now(UTC) + timedelta(
                days=get_email_settings().invitation_token_expires_days
            )
        return data

    async def create_invitation(
        self,
        team: Team,
        email: str,
        role: TeamRoles,
        invited_by: User,
        *,
        kind: InvitationKind = InvitationKind.FIRST_TIME_ACTIVATION,
        user_id: UUID | None = None,
        force_provider: str | None = None,
        out_of_domain_override: bool = False,
    ) -> tuple[TeamInvitation, str]:
        """Create a new team invitation.

        Args:
            team: The target team.
            email: The invitee's email.
            role: Role the invitee will have on acceptance.
            invited_by: The admin issuing the invitation.
            kind: Invitation kind — FIRST_TIME_ACTIVATION for new users
                (the default), CROSS_TEAM_JOIN for adding an already-
                activated user to a new team.
            user_id: Pre-provisioned User row this invitation activates.
                Required by the OIDC SSO design's admin-driven flow;
                callers must create the User row first then pass its id.
            force_provider: If set, the invite link redirects directly to
                the named OIDC provider's sign-in (skips the password
                option). Meaningful only for FIRST_TIME_ACTIVATION.
            out_of_domain_override: True iff the admin clicked through
                the out-of-domain confirmation modal at provisioning
                time. Recorded for audit.

        Returns:
            Tuple of (invitation record, plain token for email).
        """
        token = secrets.token_urlsafe(32)
        payload: dict[str, object] = {
            "team_id": team.id,
            "email": email,
            "role": role,
            "invited_by_id": invited_by.id,
            "invited_by_email": invited_by.email,
            "token": token,  # to_model_on_create hashes this
            "kind": kind,
            "out_of_domain_override": out_of_domain_override,
        }
        if user_id is not None:
            payload["user_id"] = user_id
        if force_provider is not None:
            payload["force_provider"] = force_provider
        invitation = await self.create(payload, auto_commit=True)
        return invitation, token

    async def get_by_token(self, token: str) -> TeamInvitation | None:
        """Find an invitation by its plain token.

        Returns:
            TeamInvitation if found, None otherwise.
        """
        return await self.get_one_or_none(token_hash=self._hash_token(token))

    async def get_pending_for_team(self, team_id: UUID) -> list[TeamInvitation]:
        """Get all pending (not accepted, not expired) invitations for a team.

        Returns:
            List of pending invitations for the team.
        """
        return list(
            await self.list(
                self._not_expired_filter(),
                TeamInvitation.team_id == team_id,
                TeamInvitation.is_accepted.is_(False),
            ),
        )

    async def get_accepted_for_team(self, team_id: UUID) -> list[TeamInvitation]:
        """Get all accepted invitations for a team, most recent first.

        Used by the admin invitations page to surface acceptance history
        — the row is kept (not deleted) on accept (see ``accept_invitation``
        and ``claim_invitation_accepted``), so this is the canonical view.

        Returns:
            List of accepted invitations for the team, ordered by
            ``accepted_at`` descending.
        """
        return list(
            await self.list(
                TeamInvitation.team_id == team_id,
                TeamInvitation.is_accepted.is_(True),
                OrderBy(field_name="accepted_at", sort_order="desc"),
            ),
        )

    async def get_pending_for_email(self, email: str) -> list[TeamInvitation]:
        """Get all pending invitations for an email address.

        Returns:
            List of pending invitations for the email.
        """
        return list(
            await self.list(
                self._not_expired_filter(),
                TeamInvitation.email == email,
                TeamInvitation.is_accepted.is_(False),
            ),
        )

    async def accept_invitation(
        self,
        invitation: TeamInvitation,
        user: User,
        team_member_service: TeamMemberService,
    ) -> TeamMember:
        """Accept an invitation and create a team member.

        Returns:
            The created TeamMember.

        Raises:
            RepositoryError: If invitation is expired or already accepted.
        """
        if invitation.is_expired:
            msg = "This invitation has expired."
            raise RepositoryError(msg)
        if invitation.is_accepted:
            msg = "This invitation has already been accepted."
            raise RepositoryError(msg)

        team_member = await team_member_service.create(
            {
                "team_id": invitation.team_id,
                "user_id": user.id,
                "role": invitation.role,
                "is_owner": False,
            }
        )
        invitation.is_accepted = True
        invitation.accepted_at = datetime.now(UTC)
        await self.update(item_id=invitation.id, data=invitation)
        return team_member

    async def has_pending_invitation(self, team_id: UUID, email: str) -> bool:
        """Check if there's already a pending invitation for this email on this team.

        Returns:
            True if a pending invitation exists, False otherwise.
        """
        return await self.exists(
            self._not_expired_filter(),
            TeamInvitation.team_id == team_id,
            TeamInvitation.email == email,
            TeamInvitation.is_accepted.is_(False),
        )
