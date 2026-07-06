# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from litestar.events import listener

from cert_ra.api.config import alchemy
from cert_ra.api.domain.alerts.services import AlertIntegrationService
from cert_ra.api.domain.teams.dependencies import provide_teams_service
from cert_ra.api.domain.web.email import EmailMessageService
from cert_ra.api.lib.email import get_email_config
from cert_ra.types import AlertIntegrationKind

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from cert_ra.api.domain.teams.services import TeamService
    from cert_ra.db.models import Team, TeamMember

logger = structlog.get_logger()


@listener("team_created")
async def team_created_event_handler(team_id: UUID) -> None:
    """Executes when a new team is created.

    Provisions a default ``EMAIL`` alert integration pointing at the team
    owner's address so the team has a working notification channel out of the
    box. Failure to provision the default integration is logged but never
    blocks team creation — alerts will simply have no primary channel until
    the team adds one manually.

    Args:
        team_id: The primary key of the team that was created.
    """
    await logger.ainfo("Team created.", team_id=str(team_id))

    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[TeamService, None] = provide_teams_service(
            db_session
        )
        try:
            service = await anext(service_provider)
            obj = await service.get_one_or_none(id=team_id)
        finally:
            await service_provider.aclose()
        if obj is None:
            await logger.aerror("Could not locate the specified team", id=team_id)
            return
        await logger.ainfo("Found team", name=obj.name, slug=obj.slug)
        await _provision_default_integration(db_session, obj)


async def _provision_default_integration(db_session: object, team: Team) -> None:
    """Create a default EMAIL integration for the team owner."""
    owner = _team_owner(team)
    if owner is None:
        await logger.awarning(
            "No owner found for team; skipping default integration provisioning.",
            team_id=str(team.id),
        )
        return
    integrations_service = AlertIntegrationService(session=db_session)  # type: ignore[arg-type]
    try:
        await integrations_service.create(
            {
                "team_id": team.id,
                "kind": AlertIntegrationKind.EMAIL,
                "name": "Owner email (default)",
                "config": {"to": owner.email},
                "is_primary": True,
                "is_active": True,
                "created_by": owner.user_id,
                "updated_by": owner.user_id,
            }
        )
        await db_session.commit()  # type: ignore[attr-defined]
        await logger.ainfo(
            "Provisioned default email integration for team.",
            team_id=str(team.id),
            owner_email=owner.email,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; never block team creation
        await logger.awarning(
            "Failed to provision default integration; team can still add one manually.",
            team_id=str(team.id),
            error=str(exc),
        )


def _team_owner(team: Team) -> TeamMember | None:
    """Return the owner ``TeamMember`` for ``team``, or None if no owner is recorded."""
    return next((m for m in team.members if m.is_owner), None)


@listener("team_invitation_created")
async def team_invitation_created_handler(
    invitee_email: str, inviter_name: str, team_name: str, token: str
) -> None:
    """Executes when a team invitation is created.

    Sends an invitation email to the invitee.

    Args:
        invitee_email: Email address of the person being invited.
        inviter_name: Name of the person sending the invitation.
        team_name: Name of the team.
        token: Plain invitation token to include in the email.
    """
    await logger.ainfo(
        "Team invitation created, sending email.",
        invitee_email=invitee_email,
        team_name=team_name,
    )

    # Send invitation email using litestar-email plugin
    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        sent = await email_service.send_team_invitation_email(
            invitee_email=invitee_email,
            inviter_name=inviter_name,
            team_name=team_name,
            token=token,
        )

    if sent:
        await logger.ainfo(
            "Team invitation email sent", email=invitee_email, team=team_name
        )
    else:
        await logger.awarning(
            "Failed to send team invitation email", email=invitee_email, team=team_name
        )


@listener("out_of_domain_provision_alert")
async def out_of_domain_provision_alert_handler(
    team_id: UUID,
    team_name: str,
    invitee_email: str,
    inviter_name: str,
    allowed_domains: list[str],
) -> None:
    """Notify the team's security contact about an out-of-domain provision.

    The OIDC SSO design (#4 — allowed_email_domains soft enforcement)
    treats out-of-domain provisioning as an auditable override rather
    than a hard refusal. This handler closes the loop: every override
    produces an asynchronous alert to the team's ``security_contact_email``
    (if set) plus the team owner. The inviter's identity is included
    (admins are non-PII to other admins, unlike the "never reveal admin
    identity" rule which protects them from invitees).

    Args:
        team_id: The team that received the new member.
        team_name: Display name (snapshot — emails outlive renames).
        invitee_email: The provisioned user's email.
        inviter_name: The admin who triggered the override.
        allowed_domains: The team's allowed_email_domains at the time
            of provisioning. Snapshot — the team may change this later.
    """
    await logger.ainfo(
        "Out-of-domain provisioning alert.",
        team_id=str(team_id),
        invitee_email=invitee_email,
    )
    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[TeamService, None] = provide_teams_service(
            db_session
        )
        try:
            service = await anext(service_provider)
            team = await service.get_one_or_none(id=team_id)
        finally:
            await service_provider.aclose()
        if team is None:
            await logger.aerror(
                "Team disappeared between provisioning and alert.",
                team_id=str(team_id),
            )
            return
        recipients: list[str] = []
        if team.security_contact_email:
            recipients.append(team.security_contact_email)
        owner = _team_owner(team)
        if owner is not None and owner.email and owner.email not in recipients:
            recipients.append(owner.email)
        if not recipients:
            await logger.awarning(
                "No security contact or owner — out-of-domain alert dropped.",
                team_id=str(team_id),
            )
            return

    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        for recipient in recipients:
            sent = await email_service.send_out_of_domain_provision_alert(
                recipient_email=recipient,
                team_name=team_name,
                invitee_email=invitee_email,
                inviter_name=inviter_name,
                allowed_domains=allowed_domains,
            )
            if not sent:
                await logger.awarning(
                    "Failed to send out-of-domain alert.",
                    recipient=recipient,
                    team=team_name,
                )


@listener("oidc_account_linked")
async def oidc_account_linked_handler(
    user_email: str, provider: str, account_email: str
) -> None:
    """Notify a user when their password account is linked to an OIDC IDP.

    Sent from the link-confirm POST handler after a successful claim.
    Security signal — if a user did not initiate the link, they should
    see the email and contact support. Does not name the admin
    (consistent with the "never reveal admin identity" rule from
    design #16).

    Args:
        user_email: The Certora-account email to notify.
        provider: ``google`` | ``microsoft`` | ``github`` — IdP slug.
        account_email: The verified email returned by the IdP.
            Usually equals ``user_email`` but may differ for GitHub
            (primary verified email vs profile email).
    """
    await logger.ainfo(
        "OIDC account linked, sending confirmation.",
        user_email=user_email,
        provider=provider,
    )
    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        sent = await email_service.send_oidc_account_linked_email(
            user_email=user_email,
            provider=provider,
            account_email=account_email,
        )
    if not sent:
        await logger.awarning(
            "Failed to send OIDC link confirmation.",
            email=user_email,
            provider=provider,
        )
