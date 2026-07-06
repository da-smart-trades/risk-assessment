# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Alert / integration / history / notification API schemas (msgspec)."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any
from uuid import UUID  # noqa: TC003

import msgspec

from cert_ra.api.domain.alerts.history import AlertHistoryContext  # noqa: TC001
from cert_ra.api.domain.alerts.integrations import IntegrationConfig  # noqa: TC001
from cert_ra.api.domain.alerts.rules import RuleConfig  # noqa: TC001
from cert_ra.api.domain.alerts.targets import TargetConfig  # noqa: TC001
from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import (
    AlertHistoryStatus,
    AlertIntegrationKind,
    AlertRuleKind,
    AlertSeverity,
    AlertTargetKind,
    NotificationStatus,
)

__all__ = (
    "Alert",
    "AlertCreate",
    "AlertHistory",
    "AlertHistoryListPage",
    "AlertIntegration",
    "AlertIntegrationCreate",
    "AlertIntegrationListPage",
    "AlertIntegrationUpdate",
    "AlertListPage",
    "AlertOverrideUpdate",
    "AlertUpdate",
    "Notification",
)


class Alert(CamelizedBaseStruct):
    """Response schema for an alert row.

    ``target_config`` and ``rule_config`` are both exposed as discriminated
    unions so OpenAPI emits typed schemas and the generated TypeScript exposes
    typed values rather than ``Record<string, unknown>``.
    """

    id: UUID
    team_id: UUID | None
    is_template: bool
    name: str
    description: str
    target_kind: AlertTargetKind
    target_config: TargetConfig
    rule_kind: AlertRuleKind
    rule_config: RuleConfig
    severity: AlertSeverity
    is_enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None


class AlertCreate(CamelizedBaseStruct):
    """Create payload.

    ``target_config`` and ``rule_config`` arrive as raw dicts and are validated
    against ``target_kind`` / ``rule_kind`` in the service layer. ``created_by``
    and ``updated_by`` are injected by the controller from ``current_user``.
    ``team_id`` is injected by the controller for non-templates and rejected
    for templates.
    """

    name: str
    description: str
    target_kind: AlertTargetKind
    target_config: dict[str, Any]
    rule_kind: AlertRuleKind
    rule_config: dict[str, Any]
    severity: AlertSeverity = AlertSeverity.WARNING
    is_template: bool = False
    is_enabled: bool = True
    integration_ids: list[UUID] = msgspec.field(default_factory=list)
    """IDs of *additional* integrations to attach beyond the team's primary."""


class AlertUpdate(CamelizedBaseStruct, omit_defaults=True):
    """Partial update — every field is optional. ``updated_by`` is injected."""

    name: str | msgspec.UnsetType = msgspec.UNSET
    description: str | msgspec.UnsetType = msgspec.UNSET
    target_kind: AlertTargetKind | msgspec.UnsetType = msgspec.UNSET
    target_config: dict[str, Any] | msgspec.UnsetType = msgspec.UNSET
    rule_kind: AlertRuleKind | msgspec.UnsetType = msgspec.UNSET
    rule_config: dict[str, Any] | msgspec.UnsetType = msgspec.UNSET
    severity: AlertSeverity | msgspec.UnsetType = msgspec.UNSET
    is_enabled: bool | msgspec.UnsetType = msgspec.UNSET
    integration_ids: list[UUID] | msgspec.UnsetType = msgspec.UNSET


class AlertOverrideUpdate(CamelizedBaseStruct):
    """Per-team toggle / integration override for an operator template."""

    team_id: UUID
    is_enabled: bool
    integration_id: UUID | None = None


class AlertIntegration(CamelizedBaseStruct):
    """Response schema for a delivery channel.

    ``config`` is the discriminated union ``IntegrationConfig``. Sensitive
    fields (e.g. ``WebhookIntegrationConfig.secret``) are returned redacted.
    """

    id: UUID
    team_id: UUID
    kind: AlertIntegrationKind
    name: str
    config: IntegrationConfig
    is_primary: bool
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None


class AlertIntegrationCreate(CamelizedBaseStruct):
    """Create payload — config validated against ``kind`` in the service."""

    kind: AlertIntegrationKind
    name: str
    config: dict[str, Any]
    is_primary: bool = False
    is_active: bool = True


class AlertIntegrationUpdate(CamelizedBaseStruct, omit_defaults=True):
    """Partial update — every field optional. ``updated_by`` is injected."""

    name: str | msgspec.UnsetType = msgspec.UNSET
    config: dict[str, Any] | msgspec.UnsetType = msgspec.UNSET
    is_primary: bool | msgspec.UnsetType = msgspec.UNSET
    is_active: bool | msgspec.UnsetType = msgspec.UNSET


class AlertHistory(CamelizedBaseStruct):
    """Response schema for an evaluator-tick observation."""

    id: UUID
    alert_id: UUID
    team_id: UUID
    status: AlertHistoryStatus
    context: AlertHistoryContext
    evaluated_at: datetime
    metric_value: float | None = None
    threshold: float | None = None
    message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Notification(CamelizedBaseStruct):
    """Response schema for one delivery attempt."""

    id: UUID
    alert_history_id: UUID
    integration_id: UUID
    status: NotificationStatus
    attempt_count: int
    last_error: str | None = None
    sent_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AlertListPage(CamelizedBaseStruct):
    """Inertia page-props for the alerts list page."""

    items: list[Alert]
    total: int
    is_team_editor: bool
    is_operator_editor: bool


class AlertIntegrationListPage(CamelizedBaseStruct):
    """Inertia page-props for the integrations management page."""

    items: list[AlertIntegration]
    total: int
    is_team_editor: bool


class AlertHistoryListPage(CamelizedBaseStruct):
    """Inertia page-props for the per-alert history page."""

    alert: Alert
    items: list[AlertHistory]
    total: int
