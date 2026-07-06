# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Operator-action audit writer + alert fan-out trigger (PR-8, Control 3).

Every cross-customer write by an ``operator_tenant_admin`` records an
``OperatorAudit`` row **synchronously inside the action's transaction**
(``record_operator_action`` — the caller commits it together with the
action, so a failed action leaves no audit row and vice versa).

After the commit, the controller fires ``emit_operator_audit_fanout``,
which emits an ``operator_action_audited`` event. The handler delivers
the best-effort alerts (customer security-contact email + Slack);
fan-out failure never rolls back the audit row (design — Control 3).

The design specifies a Temporal workflow for the fan-out; we reuse the
project's litestar event/handler email path (as for the other auth
emails) and leave the Slack/Temporal delivery as a follow-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cert_ra.db.models import OperatorAudit
from cert_ra.settings.api import get_app_settings

if TYPE_CHECKING:
    from uuid import UUID

    from litestar import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from cert_ra.db.models import User

__all__ = (
    "OperatorAction",
    "emit_operator_audit_fanout",
    "record_operator_action",
)


class OperatorAction:
    """Stable ``OperatorAudit.action`` identifiers."""

    RESET_MFA_ONLY = "reset_mfa_only"
    TOTAL_RECOVERY = "total_recovery"
    FORCE_UNLOCK = "force_unlock"
    ENFORCED_PROVIDER_SET = "enforced_provider_set"
    ENFORCED_PROVIDER_UNSET = "enforced_provider_unset"
    PROVISION_MEMBER = "provision_member"


async def record_operator_action(
    db: AsyncSession,
    *,
    request: Request,
    actor: User,
    action: str,
    target_team_id: UUID | None = None,
    target_user_id: UUID | None = None,
    payload: dict | None = None,
) -> OperatorAudit:
    """Add an ``OperatorAudit`` row to ``db`` (caller commits).

    Captures the actor's session id + source IP for forensic tracing.
    Returns the (unflushed) row; the caller commits it in the same
    transaction as the action it audits.
    """
    cookie_name = get_app_settings().session_cookie_name
    session_id = (request.cookies.get(cookie_name) or "unknown")[:128]
    ip = (request.client.host if request.client else "unknown")[:45]
    row = OperatorAudit(
        actor_user_id=actor.id,
        actor_session_id=session_id,
        actor_ip=ip,
        action=action,
        target_team_id=target_team_id,
        target_user_id=target_user_id,
        payload=payload or {},
    )
    db.add(row)
    return row


def emit_operator_audit_fanout(
    request: Request,
    *,
    action: str,
    actor_email: str,
    target_team_name: str | None,
    security_contact_email: str | None,
) -> None:
    """Fire the best-effort Slack + customer-email fan-out event.

    Called AFTER the audit row is committed, so a fan-out failure cannot
    roll back the audit (design — Control 3).
    """
    request.app.emit(
        "operator_action_audited",
        action=action,
        actor_email=actor_email,
        target_team_name=target_team_name,
        security_contact_email=security_contact_email,
    )
