# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

from .accounts.signals import (
    admin_mfa_reset_handler,
    admin_total_recovery_handler,
    enforcement_reminder_handler,
    force_unlock_notification_handler,
    oidc_provider_switched_handler,
    operator_action_audited_handler,
    password_reset_completed_handler,
    password_reset_requested_handler,
    password_reset_v2_completed_handler,
    password_reset_v2_requested_handler,
    team_enforced_provider_set_handler,
    team_enforced_provider_unset_handler,
    unlock_completed_handler,
    unlock_email_handler,
    user_created_event_handler,
    user_verified_event_handler,
)
from .teams.signals import (
    oidc_account_linked_handler,
    out_of_domain_provision_alert_handler,
    team_created_event_handler,
    team_invitation_created_handler,
)

if TYPE_CHECKING:
    from litestar.events.listener import EventListener


def get_listeners() -> list[EventListener]:
    """Get the lib's event listeners.

    Returns:
        A list of event listeners.
    """
    return [
        user_created_event_handler,
        user_verified_event_handler,
        password_reset_requested_handler,
        password_reset_completed_handler,
        team_created_event_handler,
        team_invitation_created_handler,
        out_of_domain_provision_alert_handler,
        oidc_account_linked_handler,
        unlock_email_handler,
        unlock_completed_handler,
        force_unlock_notification_handler,
        password_reset_v2_requested_handler,
        password_reset_v2_completed_handler,
        admin_mfa_reset_handler,
        admin_total_recovery_handler,
        oidc_provider_switched_handler,
        enforcement_reminder_handler,
        team_enforced_provider_set_handler,
        team_enforced_provider_unset_handler,
        operator_action_audited_handler,
    ]
