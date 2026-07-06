# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.db.models.team_roles import TeamRoles


class TeamTag(CamelizedBaseStruct):
    id: UUID
    slug: str
    name: str


class TeamMember(CamelizedBaseStruct):
    id: UUID
    user_id: UUID
    email: str
    name: str | None = None
    role: TeamRoles | None = TeamRoles.MEMBER
    is_owner: bool | None = False


class Team(CamelizedBaseStruct):
    id: UUID
    name: str
    description: str | None = None
    domain: str | None = None
    members: list[TeamMember] = msgspec.field(default_factory=list)
    tags: list[TeamTag] = msgspec.field(default_factory=list)


class TeamCreate(CamelizedBaseStruct):
    name: str
    description: str | None = None
    domain: str | None = None
    tags: list[str] = msgspec.field(default_factory=list)


class TeamUpdate(CamelizedBaseStruct, omit_defaults=True):
    name: str | None | msgspec.UnsetType = msgspec.UNSET
    description: str | None | msgspec.UnsetType = msgspec.UNSET
    tags: list[str] | None | msgspec.UnsetType = msgspec.UNSET


class TeamMemberModify(CamelizedBaseStruct):
    """Team Member Modify."""

    user_name: str


class TeamMemberRoleChange(CamelizedBaseStruct):
    """Change the role of an existing team member."""

    user_name: str
    role: str


class CurrentTeam(CamelizedBaseStruct):
    """Current team stored in session."""

    team_id: UUID
    team_name: str


# Page response schemas for Inertia pages


class TeamListItem(CamelizedBaseStruct):
    """Team item in list view."""

    id: UUID
    name: str
    slug: str
    member_count: int
    user_role: str
    description: str | None = None
    domain: str | None = None
    created_at: datetime | None = None


class TeamListPage(CamelizedBaseStruct):
    """Response for team list page."""

    teams: list[TeamListItem]
    total: int


class TeamPageMember(CamelizedBaseStruct):
    """Team member for page display (includes avatar)."""

    id: UUID
    user_id: UUID
    email: str
    role: str
    name: str | None = None
    avatar_url: str | None = None


class TeamPermissions(CamelizedBaseStruct):
    """User permissions for team management."""

    can_add_team_members: bool
    can_delete_team: bool
    can_remove_team_members: bool
    can_update_team: bool


class TeamDetail(CamelizedBaseStruct):
    """Team details for show/settings page."""

    id: UUID
    name: str
    slug: str
    description: str | None = None
    domain: str | None = None
    created_at: datetime | None = None
    tags: list[TeamTag] = msgspec.field(default_factory=list)
    enforced_provider: str | None = None
    """The OIDC provider this team locks sign-in to, or ``None``."""


class TeamDetailPage(CamelizedBaseStruct):
    """Response for team show/settings page."""

    team: TeamDetail
    members: list[TeamPageMember]
    permissions: TeamPermissions
    pending_invitations: list[TeamInvitationItem] = msgspec.field(default_factory=list)
    enforcement_enabled: bool = False
    """Whether the ``enforced_provider`` feature flag is on; gates the
    sign-in enforcement entry in the team settings UI."""


# Team Invitation schemas


class TeamInvitationCreate(CamelizedBaseStruct):
    """Create a new team invitation."""

    email: str
    role: TeamRoles = TeamRoles.MEMBER


class TeamInvitationItem(CamelizedBaseStruct):
    """Team invitation for list display."""

    id: UUID
    email: str
    role: str
    invited_by_email: str
    created_at: datetime
    expires_at: datetime | None = None
    is_expired: bool = False
    invitee_exists: bool = False
    accepted_at: datetime | None = None
    """Set when the invitation has been accepted; ``None`` for pending rows."""


class TeamInvitationDetail(CamelizedBaseStruct):
    """Team invitation details for accept/decline page."""

    id: UUID
    team_name: str
    team_slug: str
    inviter_name: str
    inviter_email: str
    role: str
    expires_at: datetime | None = None
    is_expired: bool = False


class TeamInvitationsPage(CamelizedBaseStruct):
    """Response for team invitations management page."""

    team: TeamDetail
    invitations: list[TeamInvitationItem]
    permissions: TeamPermissions
    accepted_invitations: list[TeamInvitationItem] = msgspec.field(default_factory=list)
    """Already-accepted invitations, most-recently-accepted first."""


class OidcProviderOption(CamelizedBaseStruct):
    """An OIDC sign-in choice shown on the activation page."""

    provider: str
    label: str
    url: str


class InvitationAcceptPage(CamelizedBaseStruct):
    """Response for invitation accept/decline page."""

    invitation: TeamInvitationDetail
    is_valid: bool = True
    error_message: str | None = None
    is_authenticated: bool = True
    is_correct_user: bool = True
    login_url: str | None = None
    # First-time activation of an admin-provisioned account: the invitee
    # picks an OIDC provider OR sets a password (unless the team enforces
    # a provider, in which case only the OIDC option is offered).
    is_activation: bool = False
    allow_password: bool = False
    invitee_email: str | None = None
    set_password_url: str | None = None
    oidc_options: list[OidcProviderOption] = msgspec.field(default_factory=list)


class UserPendingInvitation(CamelizedBaseStruct):
    """Pending invitation for user's dashboard."""

    id: UUID
    team_name: str
    team_slug: str
    inviter_name: str
    role: str
    created_at: datetime


class UserPendingInvitationsPage(CamelizedBaseStruct):
    """Response for user's pending invitations page."""

    invitations: list[UserPendingInvitation]
