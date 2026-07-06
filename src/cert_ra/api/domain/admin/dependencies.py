# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Admin domain dependencies."""

from __future__ import annotations

from advanced_alchemy.extensions.litestar.providers import create_service_provider
from sqlalchemy.orm import joinedload

from cert_ra.api.domain.admin.services import AuditLogService
from cert_ra.db.models import AuditLog

provide_audit_service = create_service_provider(
    AuditLogService,
    load=[joinedload(AuditLog.actor)],
)
