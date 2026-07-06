# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Email service for sending transactional emails.

This module provides the EmailMessageService class which offers a high-level
API for sending various types of transactional emails including
verification, password reset, welcome, and team invitation emails.

It uses litestar-email's EmailService for actual email delivery and
includes a simple template renderer with {{PLACEHOLDER}} syntax.
"""

from __future__ import annotations

import html
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote

from litestar_email import EmailMultiAlternatives

from cert_ra.settings.api import get_app_settings, get_email_settings

if TYPE_CHECKING:
    from litestar_email import EmailService

logger = logging.getLogger(__name__)

# Template directory for email templates
TEMPLATE_DIR = Path(__file__).parent / "templates" / "email"

# Pattern for placeholder matching: {{VARIABLE_NAME}}
PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")

# Pattern for stripping HTML tags for plain text fallback
HTML_TAG_PATTERN = re.compile(r"<[^<]+?>")


class TemplateRenderer:
    """Renders pre-built email templates with data injection.

    Uses simple placeholder replacement instead of Jinja2.
    Placeholders use {{VARIABLE_NAME}} syntax.
    """

    def __init__(self, template_dir: Path | None = None) -> None:
        """Initialize the template renderer.

        Args:
            template_dir: Directory containing template files.
        """
        self.template_dir = template_dir or TEMPLATE_DIR
        self._cache: dict[str, str] = {}

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        """Render a template with the given context.

        Placeholders in the template are replaced with values from the
        context dictionary. Values are HTML-escaped for security.

        Args:
            template_name: Name of template file (without extension).
            context: Dictionary of values to inject into placeholders.

        Returns:
            Rendered HTML string.

        Raises:
            FileNotFoundError: If template file does not exist.
        """
        template = self._load_template(template_name)

        def replace_placeholder(match: re.Match[str]) -> str:
            key = match.group(1)
            value = context.get(key)
            if value is None:
                return f"{{{{MISSING:{key}}}}}"
            return html.escape(str(value), quote=True)

        return PLACEHOLDER_PATTERN.sub(replace_placeholder, template)

    def render_unsafe(self, template_name: str, context: dict[str, Any]) -> str:
        """Render a template without HTML escaping.

        Use this only when context values are already safe HTML.
        """
        template = self._load_template(template_name)

        def replace_placeholder(match: re.Match[str]) -> str:
            key = match.group(1)
            value = context.get(key)
            if value is None:
                return f"{{{{MISSING:{key}}}}}"
            return str(value)

        return PLACEHOLDER_PATTERN.sub(replace_placeholder, template)

    def _load_template(self, template_name: str) -> str:
        """Load template from file or cache."""
        if template_name not in self._cache:
            template_path = self.template_dir / f"{template_name}.html"
            if not template_path.exists():
                msg = f"Email template not found: {template_path}"
                raise FileNotFoundError(msg)
            self._cache[template_name] = template_path.read_text(encoding="utf-8")
        return self._cache[template_name]

    def template_exists(self, template_name: str) -> bool:
        """Check if a template exists."""
        template_path = self.template_dir / f"{template_name}.html"
        return template_path.exists()

    def clear_cache(self) -> None:
        """Clear template cache."""
        self._cache.clear()


@lru_cache(maxsize=1)
def get_template_renderer() -> TemplateRenderer:
    """Get the global template renderer instance."""
    return TemplateRenderer()


class UserProtocol(Protocol):
    """Protocol for User objects used in email methods."""

    email: str
    name: str | None


class EmailMessageService:
    """High-level service for sending transactional emails.

    This service provides methods for sending various types of emails
    including verification, password reset, welcome, and invitation emails.

    Uses litestar-email's EmailService for actual delivery.

    Example:
        # In a signal handler (outside request context)
        from app import config

        async with config.email.provide_service() as mailer:
            service = EmailMessageService(mailer=mailer)
            await service.send_verification_email(user, token)

        # In a route handler (with dependency injection)
        service = EmailMessageService(mailer=mailer)  # mailer injected
        await service.send_verification_email(user, token)
    """

    def __init__(
        self,
        mailer: EmailService,
        renderer: TemplateRenderer | None = None,
        fail_silently: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize the email message service.

        Args:
            mailer: The litestar-email EmailService instance.
            renderer: Template renderer to use. If None, uses default.
            fail_silently: If True, suppress exceptions during send.
        """
        self.mailer = mailer
        self.renderer = renderer or get_template_renderer()
        self.fail_silently = fail_silently
        self._settings = get_email_settings()

    @property
    def app_name(self) -> str:
        """Get application name from settings."""
        return get_app_settings().name

    @property
    def base_url(self) -> str:
        """Get base URL from settings."""
        return get_app_settings().url

    async def send_email(
        self,
        to_email: str | list[str],
        subject: str,
        html_content: str,
        text_content: str | None = None,
        from_email: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """Send an email with HTML and optional text content.

        Args:
            to_email: Recipient email address(es).
            subject: Email subject.
            html_content: HTML email content.
            text_content: Plain text content (generated from HTML if not provided).
            from_email: Sender email (uses default if not provided).
            reply_to: Reply-to email address.

        Returns:
            True if email was sent successfully, False otherwise.
        """
        if not self._settings.enabled:
            logger.info(
                "Email service disabled. Would send email to %s with subject: %s",
                to_email,
                subject,
            )
            return False

        # Generate plain text from HTML if not provided
        if not text_content:
            text_content = self._html_to_text(html_content)

        # Normalize recipients to list
        recipients = [to_email] if isinstance(to_email, str) else to_email

        message = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            html_body=html_content,
            from_email=from_email,
            to=recipients,
            reply_to=[reply_to] if reply_to else [],
        )

        try:
            await self.mailer.send_message(message)
        except Exception:
            logger.exception("Failed to send email to %s", to_email)
            if not self.fail_silently:
                raise
            return False
        else:
            return True

    async def send_template_email(
        self,
        template_name: str,
        to_email: str | list[str],
        subject: str,
        context: dict[str, Any],
        from_email: str | None = None,
    ) -> bool:
        """Send email using a template.

        If the template doesn't exist, falls back to the provided context
        values for generating a simple HTML email.

        Args:
            template_name: Name of template file (without extension).
            to_email: Recipient email address(es).
            subject: Email subject.
            context: Template context variables.
            from_email: Sender email (uses default if not provided).

        Returns:
            True if email was sent successfully, False otherwise.
        """
        try:
            if self.renderer.template_exists(template_name):
                html_content = self.renderer.render(template_name, context)
            else:
                logger.debug("Template %s not found, using fallback", template_name)
                html_content = self._generate_fallback_html(template_name, context)

            return await self.send_email(
                to_email=to_email,
                subject=subject,
                html_content=html_content,
                from_email=from_email,
            )

        except Exception:
            logger.exception("Failed to send template email %s", template_name)
            if not self.fail_silently:
                raise
            return False

    async def send_verification_email(self, user: UserProtocol, token: str) -> bool:
        """Send email verification email to user."""
        verification_url = f"{self.base_url}/verify-email?token={token}"

        context = {
            "APP_NAME": self.app_name,
            "USER_NAME": user.name or "there",
            "USER_EMAIL": user.email,
            "VERIFICATION_URL": verification_url,
            "EXPIRES_HOURS": self._settings.verification_token_expires_hours,
        }

        return await self.send_template_email(
            template_name="email-verification",
            to_email=user.email,
            subject=f"Verify your email address for {self.app_name}",
            context=context,
        )

    async def send_welcome_email(self, user: UserProtocol) -> bool:
        """Send welcome email to newly verified user."""
        context = {
            "APP_NAME": self.app_name,
            "USER_NAME": user.name or "there",
            "USER_EMAIL": user.email,
            "LOGIN_URL": f"{self.base_url}/login",
        }

        return await self.send_template_email(
            template_name="welcome",
            to_email=user.email,
            subject=f"Welcome to {self.app_name}!",
            context=context,
        )

    async def send_password_reset_email(
        self, user: UserProtocol, token: str, ip_address: str = "unknown"
    ) -> bool:
        """Send password reset email to user."""
        reset_url = (
            f"{self.base_url}/reset-password?token={token}&email={quote(user.email)}"
        )
        expires_minutes = self._settings.password_reset_token_expires_minutes

        context = {
            "APP_NAME": self.app_name,
            "USER_NAME": user.name or "there",
            "USER_EMAIL": user.email,
            "RESET_URL": reset_url,
            "EXPIRES_MINUTES": expires_minutes,
            "IP_ADDRESS": ip_address,
        }

        return await self.send_template_email(
            template_name="password-reset",
            to_email=user.email,
            subject=f"Reset your password for {self.app_name}",
            context=context,
        )

    async def send_password_reset_confirmation_email(self, user: UserProtocol) -> bool:
        """Send password reset confirmation email to user."""
        context = {
            "APP_NAME": self.app_name,
            "USER_NAME": user.name or "there",
            "USER_EMAIL": user.email,
            "LOGIN_URL": f"{self.base_url}/login",
        }

        return await self.send_template_email(
            template_name="password-reset-confirmation",
            to_email=user.email,
            subject=f"Your password has been reset for {self.app_name}",
            context=context,
        )

    async def send_alert_triggered_email(
        self,
        to_email: str,
        alert_name: str,
        severity: str,
        chain: str | None,
        token: str | None,
        metric_value: float | None,
        threshold: float | None,
        message: str | None,
        evaluated_at: str,
    ) -> bool:
        """Send a notification that an alert just transitioned to TRIGGERED.

        Built without a Jinja template — uses inline HTML so a fresh
        deployment doesn't need template files for the alerting subsystem to
        work. Add a proper template later if richer formatting is wanted.

        Args:
            to_email: Destination address.
            alert_name: Human-readable alert name (also used in the subject).
            severity: ``INFO`` / ``WARNING`` / ``CRITICAL``.
            chain: Optional chain scope (``ETHEREUM`` etc.) shown in the body.
            token: Optional token scope.
            metric_value: Observed value at evaluator time.
            threshold: The rule's threshold for context.
            message: Free-form rule description.
            evaluated_at: ISO-formatted UTC timestamp.

        Returns:
            ``True`` on successful send, ``False`` if email is disabled or
            delivery failed.
        """
        scope_parts = [s for s in (chain, token) if s]
        scope = " · ".join(scope_parts) if scope_parts else "global"
        value_html = (
            f"<p><strong>Observed:</strong> {html.escape(str(metric_value))}</p>"
            if metric_value is not None
            else ""
        )
        threshold_html = (
            f"<p><strong>Threshold:</strong> {html.escape(str(threshold))}</p>"
            if threshold is not None
            else ""
        )
        message_html = f"<p>{html.escape(message)}</p>" if message else ""
        body = (
            f"<h2>Alert triggered: {html.escape(alert_name)}</h2>"
            f"<p><strong>Severity:</strong> {html.escape(severity)}</p>"
            f"<p><strong>Scope:</strong> {html.escape(scope)}</p>"
            f"{value_html}{threshold_html}{message_html}"
            f'<p style="color:#666;font-size:12px;">Evaluated at {html.escape(evaluated_at)} · {html.escape(self.app_name)}</p>'
        )
        return await self.send_email(
            to_email=to_email,
            subject=f"[ALERT][{severity}] {alert_name} — {scope}",
            html_content=body,
        )

    async def send_alert_recovered_email(
        self,
        to_email: str,
        alert_name: str,
        chain: str | None,
        token: str | None,
        metric_value: float | None,
        evaluated_at: str,
    ) -> bool:
        """Send a notification that an alert just transitioned back to OK.

        Args:
            to_email: Destination address.
            alert_name: Human-readable alert name.
            chain: Optional chain scope.
            token: Optional token scope.
            metric_value: Observed value at recovery time.
            evaluated_at: ISO-formatted UTC timestamp.

        Returns:
            ``True`` on successful send, ``False`` otherwise.
        """
        scope_parts = [s for s in (chain, token) if s]
        scope = " · ".join(scope_parts) if scope_parts else "global"
        value_html = (
            f"<p><strong>Observed:</strong> {html.escape(str(metric_value))}</p>"
            if metric_value is not None
            else ""
        )
        body = (
            f"<h2>Alert recovered: {html.escape(alert_name)}</h2>"
            f"<p><strong>Scope:</strong> {html.escape(scope)}</p>"
            f"{value_html}"
            f'<p style="color:#666;font-size:12px;">Recovered at {html.escape(evaluated_at)} · {html.escape(self.app_name)}</p>'
        )
        return await self.send_email(
            to_email=to_email,
            subject=f"[ALERT][RECOVERED] {alert_name} — {scope}",
            html_content=body,
        )

    async def send_team_invitation_email(
        self,
        invitee_email: str,
        inviter_name: str,
        team_name: str,
        token: str,
    ) -> bool:
        """Send team invitation email."""
        invitation_url = f"{self.base_url}/invitations/{token}/"

        context = {
            "APP_NAME": self.app_name,
            "INVITER_NAME": inviter_name,
            "TEAM_NAME": team_name,
            "INVITATION_URL": invitation_url,
            "EXPIRES_DAYS": self._settings.invitation_token_expires_days,
        }

        return await self.send_template_email(
            template_name="team-invitation",
            to_email=invitee_email,
            subject=f"{inviter_name} invited you to join {team_name} on {self.app_name}",
            context=context,
        )

    async def send_out_of_domain_provision_alert(
        self,
        recipient_email: str,
        team_name: str,
        invitee_email: str,
        inviter_name: str,
        allowed_domains: list[str],
    ) -> bool:
        """Notify a team's security contact of an out-of-domain provisioning override.

        Triggered by ``out_of_domain_provision_alert`` events emitted by
        the admin invite endpoint when an admin uses the
        ``out_of_domain_override`` flag. The body names the inviter
        (admin-to-admin, not invitee-facing — the design's
        "never-reveal-admin-identity" rule does not apply here).
        """
        context = {
            "APP_NAME": self.app_name,
            "TEAM_NAME": team_name,
            "INVITEE_EMAIL": invitee_email,
            "INVITER_NAME": inviter_name,
            "ALLOWED_DOMAINS": ", ".join(allowed_domains) or "(none configured)",
        }
        return await self.send_template_email(
            template_name="out-of-domain-provision-alert",
            to_email=recipient_email,
            subject=(
                f"[Security] Out-of-domain invitation to {team_name} on {self.app_name}"
            ),
            context=context,
        )

    async def send_oidc_account_linked_email(
        self,
        user_email: str,
        provider: str,
        account_email: str,
    ) -> bool:
        """Confirm to a user that their Certora account is now linked to an IdP.

        Triggered by ``oidc_account_linked`` events emitted from the
        link-confirm POST handler after a successful claim. Security
        signal — if the user did not initiate the link they have
        evidence to contact support.
        """
        provider_label = {
            "google": "Google",
            "microsoft": "Microsoft",
            "github": "GitHub",
        }.get(provider, provider.capitalize())
        context = {
            "APP_NAME": self.app_name,
            "USER_EMAIL": user_email,
            "PROVIDER": provider_label,
            "ACCOUNT_EMAIL": account_email,
        }
        return await self.send_template_email(
            template_name="oidc-account-linked",
            to_email=user_email,
            subject=f"You linked {provider_label} to your {self.app_name} account",
            context=context,
        )

    async def send_oidc_provider_switched_email(
        self,
        user_email: str,
        from_provider: str,
        to_provider: str,
    ) -> bool:
        """Confirm an enforcement-driven sign-in provider switch.

        Triggered by ``oidc_provider_switched`` after the enforcement
        self-migration swaps the user's linked provider. Security
        signal — if the user did not expect the change they have
        evidence to contact support (design — Enforcement migration
        flow, step 4).
        """
        labels = {"google": "Google", "microsoft": "Microsoft", "github": "GitHub"}
        from_label = labels.get(from_provider, from_provider.capitalize())
        to_label = labels.get(to_provider, to_provider.capitalize())
        html = (
            f"<p>Your sign-in method for {self.app_name} was changed from "
            f"<strong>{from_label}</strong> to <strong>{to_label}</strong> "
            "because your team now requires it.</p>"
            "<p>If this wasn't expected, contact support immediately.</p>"
        )
        return await self.send_email(
            to_email=user_email,
            subject=f"Your {self.app_name} sign-in method was changed",
            html_content=html,
        )

    async def send_operator_action_alert_email(
        self,
        security_contact_email: str,
        action: str,
        actor_email: str,
        team_name: str,
    ) -> bool:
        """Alert a customer's security contact to an operator action.

        Triggered by ``operator_action_audited`` after an operator
        tenant-admin performs a cross-customer write (design — Control 3).
        """
        html = (
            f"<p>A Certora operator (<strong>{actor_email}</strong>) performed "
            f"the action <strong>{action}</strong> on your team "
            f"<strong>{team_name}</strong> in {self.app_name}.</p>"
            "<p>This is an audited operator action. If it was unexpected, "
            "contact support immediately.</p>"
        )
        return await self.send_email(
            to_email=security_contact_email,
            subject=f"Operator action on your {self.app_name} team",
            html_content=html,
        )

    async def send_enforcement_reminder_email(
        self,
        user_email: str,
        team_name: str,
        provider: str,
    ) -> bool:
        """Remind a stuck member to migrate to the team's enforced provider.

        Triggered by the ``enforcement_reminder`` admin action from the
        stuck-members list (design — The "stuck list" admin view).
        """
        labels = {"google": "Google", "microsoft": "Microsoft", "github": "GitHub"}
        provider_label = labels.get(provider, provider.capitalize())
        login_url = f"{self.base_url}/auth/{provider}/login"
        html = (
            f"<p>Your team <strong>{team_name}</strong> now requires "
            f"<strong>{provider_label}</strong> sign-in for {self.app_name}.</p>"
            f'<p><a href="{login_url}">Sign in with {provider_label}</a> to '
            "finish migrating your account.</p>"
        )
        return await self.send_email(
            to_email=user_email,
            subject=f"Action needed: switch to {provider_label} sign-in",
            html_content=html,
        )

    async def send_unlock_email(self, user_email: str, token: str, ip: str) -> bool:
        """Send the unlock-via-email recovery link.

        Triggered by ``unlock_email`` events emitted from the login
        handler on the first lockout within the throttle window.
        """
        unlock_url = f"{self.base_url}/auth/unlock/{token}"
        context = {
            "APP_NAME": self.app_name,
            "USER_EMAIL": user_email,
            "UNLOCK_URL": unlock_url,
            "ATTEMPT_IP": ip,
        }
        return await self.send_template_email(
            template_name="unlock-email",
            to_email=user_email,
            subject=f"Unlock your {self.app_name} account",
            context=context,
        )

    async def send_unlock_confirmation_email(self, user_email: str) -> bool:
        """Confirm to the user that their account was unlocked via the link."""
        context = {
            "APP_NAME": self.app_name,
            "USER_EMAIL": user_email,
            "LOGIN_URL": f"{self.base_url}/login",
        }
        return await self.send_template_email(
            template_name="unlock-confirmation",
            to_email=user_email,
            subject=f"Your {self.app_name} account is unlocked",
            context=context,
        )

    async def send_force_unlock_notification_email(
        self, user_email: str, team_name: str, actor_role: str
    ) -> bool:
        """Notify the user that an admin force-unlocked their account.

        The body names the team and the actor's role (e.g., ``admin``)
        but never the admin's identity.
        """
        context = {
            "APP_NAME": self.app_name,
            "USER_EMAIL": user_email,
            "TEAM_NAME": team_name,
            "ACTOR_ROLE": actor_role,
            "LOGIN_URL": f"{self.base_url}/login",
        }
        return await self.send_template_email(
            template_name="force-unlock-notification",
            to_email=user_email,
            subject=f"Your {self.app_name} account was unlocked by an admin",
            context=context,
        )

    async def send_admin_mfa_reset_email(
        self, user_email: str, team_name: str, actor_role: str
    ) -> bool:
        """Notify the user that an admin reset their MFA factors.

        Triggered by ``admin_mfa_reset`` events. The user retains
        their password — they need to re-enroll MFA on next sign-in
        (where the enrollment-trap middleware funnels them).
        """
        context = {
            "APP_NAME": self.app_name,
            "USER_EMAIL": user_email,
            "TEAM_NAME": team_name,
            "ACTOR_ROLE": actor_role,
            "LOGIN_URL": f"{self.base_url}/login",
        }
        return await self.send_template_email(
            template_name="admin-mfa-reset",
            to_email=user_email,
            subject=f"Your {self.app_name} MFA factors were reset by an admin",
            context=context,
        )

    async def send_admin_total_recovery_email(
        self,
        user_email: str,
        team_name: str,
        actor_role: str,
        token: str,
    ) -> bool:
        """Notify the user that an admin triggered Total Recovery.

        Includes a password-reset link. The user's MFA has been
        cleared; once they set a new password they'll be funneled
        into the MFA enrollment trap.
        """
        reset_url = f"{self.base_url}/auth/reset/{token}"
        context = {
            "APP_NAME": self.app_name,
            "USER_EMAIL": user_email,
            "TEAM_NAME": team_name,
            "ACTOR_ROLE": actor_role,
            "RESET_URL": reset_url,
        }
        return await self.send_template_email(
            template_name="admin-total-recovery",
            to_email=user_email,
            subject=(f"Account recovery for your {self.app_name} account"),
            context=context,
        )

    def _html_to_text(self, html_content: str) -> str:
        """Convert HTML to plain text."""
        text = HTML_TAG_PATTERN.sub("", html_content)
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _generate_fallback_html(
        self, template_name: str, context: dict[str, Any]
    ) -> str:
        """Generate fallback HTML when template is not found."""
        app_name = context.get("APP_NAME", self.app_name)
        user_name = context.get("USER_NAME", "there")
        primary = "#202235"
        accent = "#EDB641"
        surface = "#ffffff"
        border = "#DCDFE4"

        if "verification" in template_name:
            url = context.get("VERIFICATION_URL", "")
            expires = context.get("EXPIRES_HOURS", 24)
            content = f"""
                <p>Hi {user_name},</p>
                <p>Please verify your email address by clicking the link below:</p>
                <p><a href="{url}" style="display: inline-block; padding: 10px 20px;
                    background-color: {accent}; color: {primary}; text-decoration: none;
                    border-radius: 4px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">Verify Email</a></p>
                <p>Or copy and paste this URL: {url}</p>
                <p>This link expires in {expires} hours.</p>
            """
        elif "reset" in template_name and "confirmation" not in template_name:
            url = context.get("RESET_URL", "")
            expires = context.get("EXPIRES_MINUTES", 60)
            content = f"""
                <p>Hi {user_name},</p>
                <p>You requested to reset your password. Click the link below:</p>
                <p><a href="{url}" style="display: inline-block; padding: 10px 20px;
                    background-color: {accent}; color: {primary}; text-decoration: none;
                    border-radius: 4px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">Reset Password</a></p>
                <p>Or copy and paste this URL: {url}</p>
                <p>This link expires in {expires} minutes.</p>
                <p>If you didn't request this, please ignore this email.</p>
            """
        elif "confirmation" in template_name:
            url = context.get("LOGIN_URL", "")
            content = f"""
                <p>Hi {user_name},</p>
                <p>Your password has been successfully reset.</p>
                <p><a href="{url}" style="display: inline-block; padding: 10px 20px;
                    background-color: {accent}; color: {primary}; text-decoration: none;
                    border-radius: 4px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">Log In</a></p>
                <p>If you didn't make this change, contact support immediately.</p>
            """
        elif "welcome" in template_name:
            url = context.get("LOGIN_URL", "")
            content = f"""
                <p>Hi {user_name},</p>
                <p>Welcome to {app_name}! Your account is now active.</p>
                <p><a href="{url}" style="display: inline-block; padding: 10px 20px;
                    background-color: {accent}; color: {primary}; text-decoration: none;
                    border-radius: 4px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">Log In</a></p>
            """
        elif "invitation" in template_name:
            url = context.get("INVITATION_URL", "")
            inviter = context.get("INVITER_NAME", "Someone")
            team = context.get("TEAM_NAME", "a team")
            expires = context.get("EXPIRES_DAYS", 7)
            content = f"""
                <p>Hi there,</p>
                <p>{inviter} has invited you to join {team} on {app_name}.</p>
                <p><a href="{url}" style="display: inline-block; padding: 10px 20px;
                    background-color: {accent}; color: {primary}; text-decoration: none;
                    border-radius: 4px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;">Accept Invitation</a></p>
                <p>Or copy and paste this URL: {url}</p>
                <p>This invitation expires in {expires} days.</p>
            """
        else:
            content = f"""
                <p>Hi {user_name},</p>
                <p>This is a message from {app_name}.</p>
            """

        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
                     line-height: 1.6; color: {primary}; background: {border};
                     max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: {primary}; color: white; padding: 20px; text-align: center;">
                <h1 style="margin: 0;">{app_name}</h1>
            </div>
            <div style="background: {surface}; padding: 30px; border: 1px solid {border};">
                {content}
            </div>
            <div style="text-align: center; padding: 20px; font-size: 12px; color: {primary};">
                <p>&copy; {app_name}</p>
            </div>
        </body>
        </html>
        """
