# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Alert / integration / history / notification services.

All services follow the existing repo pattern: extend
``SQLAlchemyAsyncRepositoryService[Model]`` with a nested ``Repo``.

``AlertService`` and ``AlertIntegrationService`` validate the polymorphic JSONB
config columns through the typed registries in ``rules.py`` /
``integrations.py``. ``created_by`` / ``updated_by`` are required on every
write — controllers inject from ``current_user``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    schema_dump,
)

from cert_ra.api.domain.alerts._encryption import encrypt_secret
from cert_ra.api.domain.alerts.history import dump_history_context
from cert_ra.api.domain.alerts.integrations import dump_integration_config
from cert_ra.api.domain.alerts.rules import dump_rule_config
from cert_ra.api.domain.alerts.targets import dump_target_config
from cert_ra.db.models import (
    Alert,
    AlertHistory,
    AlertIntegration,
    Notification,
    TeamAlertOverride,
)
from cert_ra.types import AlertIntegrationKind, AlertRuleKind, AlertTargetKind

if TYPE_CHECKING:
    from advanced_alchemy.service import ModelDictT

__all__ = (
    "AlertHistoryService",
    "AlertIntegrationService",
    "AlertService",
    "NotificationService",
    "TeamAlertOverrideService",
)


def _require_audit_fields(data: dict, *, on_update: bool = False) -> None:
    """Verify the controller injected ``created_by`` / ``updated_by``.

    Raises:
        ValueError: If the required audit fields are missing.
    """
    if not on_update and "created_by" not in data:
        msg = "created_by must be set by the controller."
        raise ValueError(msg)
    if "updated_by" not in data:
        msg = "updated_by must be set by the controller."
        raise ValueError(msg)


def _encrypt_sensitive_fields(kind: AlertIntegrationKind, config: dict) -> None:
    """Encrypt secret-bearing fields in-place before persistence.

    Today only ``WebhookIntegrationConfig.secret`` is sensitive. Future kinds
    (Slack, PagerDuty) that carry tokens should add their fields here.
    ``encrypt_secret`` is idempotent so repeat encryption on update paths is
    safe.
    """
    if kind is AlertIntegrationKind.WEBHOOK and "secret" in config:
        config["secret"] = encrypt_secret(config["secret"])


class AlertService(SQLAlchemyAsyncRepositoryService[Alert]):
    """CRUD service for alerts (templates and team-owned)."""

    class Repo(SQLAlchemyAsyncRepository[Alert]):
        """Alert SQLAlchemy repository."""

        model_type = Alert

    repository_type = Repo
    match_fields = ["name"]  # noqa: RUF012

    async def to_model_on_create(self, data: ModelDictT[Alert]) -> ModelDictT[Alert]:
        """Validate audit fields and round-trip the polymorphic JSONB columns.

        Both ``target_config`` and ``rule_config`` are validated against their
        discriminator columns. The controller is responsible for setting
        ``team_id`` correctly (``None`` for templates, set otherwise).

        Args:
            data: Raw payload (dict or schema) for the new alert.

        Returns:
            Validated payload with both JSONB columns normalised.
        """
        data = schema_dump(data)
        _require_audit_fields(data)
        rule_kind = AlertRuleKind(data["rule_kind"])
        data["rule_config"] = dump_rule_config(rule_kind, data["rule_config"])
        target_kind = AlertTargetKind(data["target_kind"])
        data["target_config"] = dump_target_config(target_kind, data["target_config"])
        return data

    async def to_model_on_update(self, data: ModelDictT[Alert]) -> ModelDictT[Alert]:
        """Validate audit fields and (if present) ``rule_config`` / ``target_config``.

        Both polymorphic columns follow the same "kind and config update
        together or not at all" rule, because changing one without the other
        leaves the typed contract inconsistent.

        Args:
            data: Raw payload (dict or schema) for the update.

        Returns:
            Validated payload.

        Raises:
            ValueError: If exactly one of (rule_kind, rule_config) or
                (target_kind, target_config) is set.
        """
        data = schema_dump(data)
        _require_audit_fields(data, on_update=True)
        has_rule_kind = "rule_kind" in data
        has_rule_cfg = "rule_config" in data
        if has_rule_kind != has_rule_cfg:
            msg = (
                "rule_kind and rule_config must be updated together; "
                "supplying one without the other would break the typed contract."
            )
            raise ValueError(msg)
        if has_rule_kind and has_rule_cfg:
            rule_kind = AlertRuleKind(data["rule_kind"])
            data["rule_config"] = dump_rule_config(rule_kind, data["rule_config"])

        has_target_kind = "target_kind" in data
        has_target_cfg = "target_config" in data
        if has_target_kind != has_target_cfg:
            msg = (
                "target_kind and target_config must be updated together; "
                "supplying one without the other would break the typed contract."
            )
            raise ValueError(msg)
        if has_target_kind and has_target_cfg:
            target_kind = AlertTargetKind(data["target_kind"])
            data["target_config"] = dump_target_config(
                target_kind, data["target_config"]
            )
        return data


class AlertIntegrationService(SQLAlchemyAsyncRepositoryService[AlertIntegration]):
    """CRUD service for delivery channels."""

    class Repo(SQLAlchemyAsyncRepository[AlertIntegration]):
        """AlertIntegration SQLAlchemy repository."""

        model_type = AlertIntegration

    repository_type = Repo
    match_fields = ["name"]  # noqa: RUF012

    async def to_model_on_create(
        self, data: ModelDictT[AlertIntegration]
    ) -> ModelDictT[AlertIntegration]:
        """Validate audit fields, validate ``config``, and encrypt sensitive fields.

        Args:
            data: Raw payload (dict or schema) for the new integration.

        Returns:
            Validated payload with ``config`` normalised to a JSONB dict and
            secret-bearing fields encrypted.
        """
        data = schema_dump(data)
        _require_audit_fields(data)
        kind = AlertIntegrationKind(data["kind"])
        config = dump_integration_config(kind, data["config"])
        _encrypt_sensitive_fields(kind, config)
        data["config"] = config
        return data

    async def to_model_on_update(
        self, data: ModelDictT[AlertIntegration]
    ) -> ModelDictT[AlertIntegration]:
        """Validate audit fields and (if present) ``config``.

        ``kind`` is immutable for an integration row — once you've created an
        EMAIL integration it stays an EMAIL integration. Updates that try to
        change ``kind`` are rejected at the API layer; here we only need to
        validate ``config`` against the existing kind, which the controller is
        responsible for passing in.

        Args:
            data: Raw payload (dict or schema) for the update.

        Returns:
            Validated payload.

        Raises:
            ValueError: If ``config`` is supplied without ``kind`` for context.
        """
        data = schema_dump(data)
        _require_audit_fields(data, on_update=True)
        if "config" in data:
            if "kind" not in data:
                msg = (
                    "Updating config requires kind to be passed alongside it "
                    "(controller should fetch the existing row's kind)."
                )
                raise ValueError(msg)
            kind = AlertIntegrationKind(data["kind"])
            config = dump_integration_config(kind, data["config"])
            _encrypt_sensitive_fields(kind, config)
            data["config"] = config
            # ``kind`` is immutable — drop it from the update payload after
            # using it for validation.
            del data["kind"]
        return data


class TeamAlertOverrideService(SQLAlchemyAsyncRepositoryService[TeamAlertOverride]):
    """Service for per-team toggles of operator templates."""

    class Repo(SQLAlchemyAsyncRepository[TeamAlertOverride]):
        """TeamAlertOverride SQLAlchemy repository."""

        model_type = TeamAlertOverride

    repository_type = Repo
    match_fields = ["team_id"]  # noqa: RUF012


class AlertHistoryService(SQLAlchemyAsyncRepositoryService[AlertHistory]):
    """Read-mostly service for evaluator events.

    Writes happen exclusively from the alerts Temporal worker activities;
    HTTP-side code only reads.
    """

    class Repo(SQLAlchemyAsyncRepository[AlertHistory]):
        """AlertHistory SQLAlchemy repository."""

        model_type = AlertHistory

    repository_type = Repo

    async def to_model_on_create(
        self, data: ModelDictT[AlertHistory]
    ) -> ModelDictT[AlertHistory]:
        """Round-trip ``context`` through the typed validator on writes.

        Args:
            data: Raw payload (dict or schema) for the new history row.

        Returns:
            Validated payload with ``context`` normalised to a JSONB dict.
        """
        data = schema_dump(data)
        if "context" in data:
            data["context"] = dump_history_context(data["context"])
        return data


class NotificationService(SQLAlchemyAsyncRepositoryService[Notification]):
    """Service for notification delivery attempts.

    Writes happen exclusively from the alerts Temporal worker activities;
    HTTP-side code only reads.
    """

    class Repo(SQLAlchemyAsyncRepository[Notification]):
        """Notification SQLAlchemy repository."""

        model_type = Notification

    repository_type = Repo
