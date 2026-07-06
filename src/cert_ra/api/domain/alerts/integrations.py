# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Typed configurations for the polymorphic ``alert_integration.config`` JSONB column.

Mirrors the rule-config approach in ``rules.py``: discriminator (``kind``) lives
in a separate column, validators live here in a registry. Adding a new
integration channel = new ``CamelizedBaseStruct`` + new entry in
``INTEGRATION_VALIDATORS`` + new dispatcher activity in
``cert_ra.alerts.activities`` — no migration needed.

Sensitive fields (e.g. webhook secrets) are encrypted-at-rest at the service
layer (Phase 3); this module is concerned only with shape validation.
"""

from __future__ import annotations

from typing import Any, cast

import msgspec

from cert_ra.api.lib.schema import CamelizedBaseStruct
from cert_ra.types import AlertIntegrationKind

__all__ = (
    "INTEGRATION_VALIDATORS",
    "EmailIntegrationConfig",
    "IntegrationConfig",
    "WebhookIntegrationConfig",
    "dump_integration_config",
    "parse_integration_config",
)


class EmailIntegrationConfig(CamelizedBaseStruct, tag="EMAIL"):
    """Email channel — deliver via the configured ``litestar-email`` backend.

    The ``tag`` mirrors ``AlertIntegrationKind.EMAIL`` so msgspec can serialise
    and deserialise this struct as a member of the ``IntegrationConfig`` union.
    """

    to: str
    """Single recipient address. Distribution lists are out of scope for v1."""

    cc: list[str] = msgspec.field(default_factory=list)
    """Optional carbon-copy recipients."""


class WebhookIntegrationConfig(CamelizedBaseStruct, tag="WEBHOOK"):
    """HTTP webhook channel — POST a signed JSON payload to ``url``.

    The dispatcher signs every request with ``HMAC-SHA256(secret, body)`` and
    sets the digest in ``X-CRA-Signature``. Receivers verify the header against
    their stored secret. Encryption-at-rest of ``secret`` is handled at the
    service layer.

    See ``EmailIntegrationConfig`` for the rationale behind the ``tag`` value.
    """

    url: str
    secret: str
    headers: dict[str, str] = msgspec.field(default_factory=dict)
    """Optional extra headers (e.g. tenant identifiers). The signature header
    is always added by the dispatcher and should not be supplied here."""


IntegrationConfig = EmailIntegrationConfig | WebhookIntegrationConfig
"""Discriminated union for ``AlertIntegration.config`` response schemas."""


INTEGRATION_VALIDATORS: dict[AlertIntegrationKind, type[CamelizedBaseStruct]] = {
    AlertIntegrationKind.EMAIL: EmailIntegrationConfig,
    AlertIntegrationKind.WEBHOOK: WebhookIntegrationConfig,
    # AlertIntegrationKind.SLACK / PAGERDUTY intentionally omitted — reserved.
}


def parse_integration_config(
    kind: AlertIntegrationKind,
    raw: dict[str, Any],
) -> IntegrationConfig:
    """JSONB ``dict`` → typed Struct (read path).

    Args:
        kind: Discriminator value from ``alert_integration.kind``.
        raw: Raw dict pulled from the JSONB column.

    Returns:
        A concrete struct of the variant matching ``kind``.

    Raises:
        ValueError: If ``kind`` has no registered validator.
        msgspec.ValidationError: If ``raw`` does not match the expected shape.
    """
    schema = INTEGRATION_VALIDATORS.get(kind)
    if schema is None:
        msg = f"No validator registered for AlertIntegrationKind.{kind.name}."
        raise ValueError(msg)
    return cast("IntegrationConfig", msgspec.convert(raw, type=schema))


def dump_integration_config(
    kind: AlertIntegrationKind,
    config: IntegrationConfig | dict[str, Any],
) -> dict[str, Any]:
    """Typed Struct (or raw dict) → validated JSONB ``dict`` (write path).

    Args:
        kind: Discriminator value to validate against.
        config: Either a typed struct or a raw dict to be coerced.

    Returns:
        A dict suitable for JSONB persistence (camelCase).

    Raises:
        ValueError: If ``kind`` has no registered validator.
        msgspec.ValidationError: If ``config`` does not match the expected shape.
    """
    schema = INTEGRATION_VALIDATORS.get(kind)
    if schema is None:
        msg = f"No validator registered for AlertIntegrationKind.{kind.name}."
        raise ValueError(msg)
    typed = (
        config if isinstance(config, schema) else msgspec.convert(config, type=schema)
    )
    return cast("dict[str, Any]", msgspec.to_builtins(typed))
