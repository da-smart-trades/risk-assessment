# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import httpx
import structlog
from litestar.events import listener

from cert_ra.api.config import alchemy
from cert_ra.api.domain.accounts.dependencies import provide_users_service
from cert_ra.api.domain.accounts.services import EmailTokenService
from cert_ra.api.domain.web.email import EmailMessageService
from cert_ra.api.lib.email import get_email_config
from cert_ra.db.models import TokenType
from cert_ra.settings.api import get_email_settings, get_operator_team_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from uuid import UUID

    from cert_ra.api.domain.accounts.services import UserService

logger = structlog.get_logger()


@dataclass
class UserInfo:
    """Simple container for user data passed to email service."""

    email: str
    name: str | None


@listener("user_created")
async def user_created_event_handler(
    user_id: UUID,
    send_verification: bool = True,  # noqa: FBT001, FBT002
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Executes when a new user is created.

    Sends a verification email to the new user with a secure token.

    Args:
        user_id: The primary key of the user that was created.
        send_verification: Whether to send verification email.
        ip_address: IP address where signup occurred.
        user_agent: User agent from signup request.
    """
    await logger.ainfo("Running post signup flow.", user_id=str(user_id))

    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()

        if user is None:
            await logger.aerror("Could not locate the specified user", id=user_id)
            return

        await logger.ainfo("Found user", email=user.email, name=user.name)

        if not send_verification:
            await logger.ainfo("Skipping verification email", user_id=str(user_id))
            return

        # Create verification token and send email
        settings = get_email_settings()
        token_service = EmailTokenService(session=db_session)

        # Invalidate any existing verification tokens
        await token_service.invalidate_existing_tokens(
            email=user.email, token_type=TokenType.EMAIL_VERIFICATION
        )

        # Create new verification token
        expires_delta = timedelta(hours=settings.verification_token_expires_hours)
        _, plain_token = await token_service.create_token(
            email=user.email,
            token_type=TokenType.EMAIL_VERIFICATION,
            expires_delta=expires_delta,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Send verification email using litestar-email plugin
        email_config = get_email_config()
        async with email_config.provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            user_info = UserInfo(email=user.email, name=user.name)
            sent = await email_service.send_verification_email(user_info, plain_token)

        if sent:
            await logger.ainfo("Verification email sent", email=user.email)
        else:
            await logger.awarning("Failed to send verification email", email=user.email)


@listener("user_verified")
async def user_verified_event_handler(user_id: UUID) -> None:
    """Executes when a user verifies their email.

    Sends a welcome email to the newly verified user.

    Args:
        user_id: The primary key of the user that was verified.
    """
    await logger.ainfo("User verified, sending welcome email.", user_id=str(user_id))

    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()

        if user is None:
            await logger.aerror("Could not locate the specified user", id=user_id)
            return

        # Send welcome email using litestar-email plugin
        email_config = get_email_config()
        async with email_config.provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            user_info = UserInfo(email=user.email, name=user.name)
            sent = await email_service.send_welcome_email(user_info)

        if sent:
            await logger.ainfo("Welcome email sent", email=user.email)
        else:
            await logger.awarning("Failed to send welcome email", email=user.email)


@listener("password_reset_requested")
async def password_reset_requested_handler(
    email: str,
    ip_address: str = "unknown",
    user_agent: str | None = None,
) -> None:
    """Executes when a password reset is requested.

    Creates a reset token and sends the password reset email.
    If the email doesn't exist, logs but doesn't reveal this to caller.

    Args:
        email: The email address for password reset.
        ip_address: IP address where reset was requested.
        user_agent: User agent from the request.
    """
    await logger.ainfo("Password reset requested", email=email)

    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(email=email)
        finally:
            await service_provider.aclose()

        if user is None:
            # Don't reveal if email exists or not
            await logger.ainfo(
                "Password reset for unknown email (not revealing)", email=email
            )
            return

        # Create reset token and send email
        settings = get_email_settings()
        token_service = EmailTokenService(session=db_session)

        # Invalidate any existing reset tokens
        await token_service.invalidate_existing_tokens(
            email=user.email, token_type=TokenType.PASSWORD_RESET
        )

        # Create new reset token
        expires_delta = timedelta(minutes=settings.password_reset_token_expires_minutes)
        _, plain_token = await token_service.create_token(
            email=user.email,
            token_type=TokenType.PASSWORD_RESET,
            expires_delta=expires_delta,
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Send password reset email using litestar-email plugin
        email_config = get_email_config()
        async with email_config.provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            user_info = UserInfo(email=user.email, name=user.name)
            sent = await email_service.send_password_reset_email(
                user_info, plain_token, ip_address
            )

        if sent:
            await logger.ainfo("Password reset email sent", email=user.email)
        else:
            await logger.awarning(
                "Failed to send password reset email", email=user.email
            )


@listener("password_reset_completed")
async def password_reset_completed_handler(user_id: UUID) -> None:
    """Executes when a password reset is completed.

    Sends a confirmation email to the user.

    Args:
        user_id: The primary key of the user whose password was reset.
    """
    await logger.ainfo("Password reset completed", user_id=str(user_id))

    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()

        if user is None:
            await logger.aerror("Could not locate the specified user", id=user_id)
            return

        # Send confirmation email using litestar-email plugin
        email_config = get_email_config()
        async with email_config.provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            user_info = UserInfo(email=user.email, name=user.name)
            sent = await email_service.send_password_reset_confirmation_email(user_info)

        if sent:
            await logger.ainfo("Password reset confirmation sent", email=user.email)
        else:
            await logger.awarning(
                "Failed to send password reset confirmation", email=user.email
            )


@listener("unlock_email")
async def unlock_email_handler(user_email: str, token: str, ip: str) -> None:
    """Send the unlock-via-email recovery link.

    Emitted by ``_access.login`` on the call that crosses the lockout
    threshold AND wins the ``enqueue_unlock_email_if_due`` CAS. The
    throttle is enforced upstream — by the time this handler runs the
    email is already authorized.

    Args:
        user_email: Recipient email.
        token: Plaintext unlock token to include in the URL.
        ip: Client IP that triggered the lockout — useful in the
            email body for the user to recognize whether the failed
            attempts came from them or someone else.
    """
    await logger.ainfo("Unlock email enqueued", email=user_email, ip=ip)
    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        sent = await email_service.send_unlock_email(
            user_email=user_email,
            token=token,
            ip=ip,
        )
    if not sent:
        await logger.awarning("Failed to send unlock email", email=user_email)


@listener("unlock_completed")
async def unlock_completed_handler(user_id: UUID) -> None:
    """Notify the user that their account was unlocked via their email link.

    Confirms the unlock so the user has a paper trail if it wasn't them.
    """
    await logger.ainfo("Unlock completed", user_id=str(user_id))
    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()
        if user is None:
            return
        async with get_email_config().provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            await email_service.send_unlock_confirmation_email(user_email=user.email)


@listener("password_reset_v2_requested")
async def password_reset_v2_requested_handler(
    user_email: str, user_name: str | None, token: str, ip_address: str
) -> None:
    """Send the v2 password-reset email.

    Emitted by the canonical-helper-backed forgot-password endpoint
    when (and only when) the email maps to a real password account.
    """
    await logger.ainfo("Password reset v2 requested", email=user_email, ip=ip_address)
    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        user_info = UserInfo(email=user_email, name=user_name)
        sent = await email_service.send_password_reset_email(
            user_info, token, ip_address=ip_address
        )
    if not sent:
        await logger.awarning(
            "Failed to send v2 password reset email", email=user_email
        )


@listener("password_reset_v2_completed")
async def password_reset_v2_completed_handler(user_id: UUID) -> None:
    """Confirm a successful v2 password reset to the user."""
    await logger.ainfo("Password reset v2 completed", user_id=str(user_id))
    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()
        if user is None:
            return
        async with get_email_config().provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            user_info = UserInfo(email=user.email, name=user.name)
            await email_service.send_password_reset_confirmation_email(user_info)


@listener("admin_mfa_reset")
async def admin_mfa_reset_handler(
    user_id: UUID, team_name: str, actor_role: str
) -> None:
    """Notify the user that an admin reset their MFA factors.

    Mentions the team + role but NEVER the admin's identity. The
    user retains their password and lockout state — they only need
    to re-enroll MFA.
    """
    await logger.ainfo("Admin MFA reset", user_id=str(user_id), team=team_name)
    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()
        if user is None:
            return
        async with get_email_config().provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            await email_service.send_admin_mfa_reset_email(
                user_email=user.email,
                team_name=team_name,
                actor_role=actor_role,
            )


@listener("admin_total_recovery")
async def admin_total_recovery_handler(
    user_id: UUID, team_name: str, actor_role: str, token: str
) -> None:
    """Notify the user that an admin triggered Total Recovery.

    Includes a password-reset link. The user's MFA factors have
    been cleared and they need to set a new password before they
    can re-enroll MFA. Naming rules same as ``admin_mfa_reset``.
    """
    await logger.ainfo("Admin total recovery", user_id=str(user_id), team=team_name)
    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()
        if user is None:
            return
        async with get_email_config().provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            await email_service.send_admin_total_recovery_email(
                user_email=user.email,
                team_name=team_name,
                actor_role=actor_role,
                token=token,
            )


@listener("force_unlock_notification")
async def force_unlock_notification_handler(
    user_id: UUID, team_name: str, actor_role: str
) -> None:
    """Notify the user that an admin force-unlocked their account.

    Mentions the team + role of the actor but NOT the admin's
    identity (design #16 — never reveal admin identity to users).
    """
    await logger.ainfo(
        "Force-unlock notification",
        user_id=str(user_id),
        team=team_name,
    )
    async with alchemy.get_session() as db_session:
        service_provider: AsyncGenerator[UserService, None] = provide_users_service(
            db_session
        )
        try:
            users_service = await anext(service_provider)
            user = await users_service.get_one_or_none(id=user_id)
        finally:
            await service_provider.aclose()
        if user is None:
            return
        async with get_email_config().provide_service() as mailer:
            email_service = EmailMessageService(mailer=mailer)
            await email_service.send_force_unlock_notification_email(
                user_email=user.email,
                team_name=team_name,
                actor_role=actor_role,
            )


@listener("oidc_provider_switched")
async def oidc_provider_switched_handler(
    user_email: str, from_provider: str, to_provider: str
) -> None:
    """Confirm an enforcement-driven sign-in provider switch (design #19).

    Emitted by the enforcement self-migration after the user's linked
    provider is swapped. Security signal — if they didn't expect it,
    they have evidence to contact support.
    """
    await logger.ainfo(
        "OIDC provider switched",
        user_email=user_email,
        from_provider=from_provider,
        to_provider=to_provider,
    )
    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        await email_service.send_oidc_provider_switched_email(
            user_email=user_email,
            from_provider=from_provider,
            to_provider=to_provider,
        )


@listener("enforcement_reminder")
async def enforcement_reminder_handler(
    user_email: str, team_name: str, provider: str
) -> None:
    """Remind a stuck member to migrate to the team's enforced provider.

    Emitted by the admin "Send reminder" action on the stuck-members
    list. The throttle (design open question — 1 per 48h) is enforced
    by the caller; this handler just delivers the mail.
    """
    await logger.ainfo("Enforcement reminder", user_email=user_email, team=team_name)
    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        await email_service.send_enforcement_reminder_email(
            user_email=user_email,
            team_name=team_name,
            provider=provider,
        )


@listener("team_enforced_provider_set")
async def team_enforced_provider_set_handler(
    team_id: UUID, team_name: str, provider: str
) -> None:
    """Audit-log a team enforcing a sign-in provider.

    No fan-out email yet (members learn via the self-migration flow on
    next sign-in); the listener exists so the emit has a handler.
    """
    await logger.ainfo(
        "Team enforced provider set",
        team_id=str(team_id),
        team=team_name,
        provider=provider,
    )


@listener("team_enforced_provider_unset")
async def team_enforced_provider_unset_handler(team_id: UUID, team_name: str) -> None:
    """Audit-log a team removing its sign-in provider enforcement."""
    await logger.ainfo(
        "Team enforced provider unset", team_id=str(team_id), team=team_name
    )


@listener("operator_action_audited")
async def operator_action_audited_handler(
    action: str,
    actor_email: str,
    target_team_name: str | None,
    security_contact_email: str | None,
) -> None:
    """Fan out a recorded operator action (PR-8, Control 3).

    Best-effort, off the critical path (the ``OperatorAudit`` row is
    already committed): posts to the operator Slack channel (when a
    webhook is configured) and emails the customer's security contact
    (when set). Failures are logged, never raised.
    """
    await logger.ainfo(
        "Operator action audited",
        action=action,
        actor=actor_email,
        team=target_team_name,
    )
    await _post_operator_action_to_slack(
        action=action, actor_email=actor_email, team_name=target_team_name
    )
    if not security_contact_email:
        return
    async with get_email_config().provide_service() as mailer:
        email_service = EmailMessageService(mailer=mailer)
        await email_service.send_operator_action_alert_email(
            security_contact_email=security_contact_email,
            action=action,
            actor_email=actor_email,
            team_name=target_team_name or "your team",
        )


async def _post_operator_action_to_slack(
    *, action: str, actor_email: str, team_name: str | None
) -> None:
    """Post an operator-action alert to Slack (best-effort, no-op if unset)."""
    webhook_url = get_operator_team_settings().slack_webhook_url
    if not webhook_url:
        return
    text = (
        f":rotating_light: Operator action *{action}* by `{actor_email}` "
        f"on team *{team_name or '—'}*"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(webhook_url, json={"text": text})
            response.raise_for_status()
    except httpx.HTTPError as exc:
        await logger.awarning(
            "Operator action Slack fan-out failed", error=str(exc), action=action
        )
