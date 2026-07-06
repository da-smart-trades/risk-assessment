# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Admin controllers."""

from cert_ra.api.domain.admin.controllers._audit import AdminAuditController
from cert_ra.api.domain.admin.controllers._dashboard import AdminDashboardController
from cert_ra.api.domain.admin.controllers._market_config import (
    AdminMarketConfigController,
)
from cert_ra.api.domain.admin.controllers._recovery import AdminRecoveryController
from cert_ra.api.domain.admin.controllers._teams import AdminTeamController
from cert_ra.api.domain.admin.controllers._users import AdminUserController
from cert_ra.api.domain.admin.controllers._weighting_profiles import (
    AdminWeightingProfileController,
    WeightingProfileApiController,
)
from cert_ra.api.domain.admin.controllers.enforcement import EnforcementController
from cert_ra.api.domain.admin.controllers.operator_promotion import (
    OperatorPromotionController,
)

__all__ = (
    "AdminAuditController",
    "AdminDashboardController",
    "AdminMarketConfigController",
    "AdminRecoveryController",
    "AdminTeamController",
    "AdminUserController",
    "AdminWeightingProfileController",
    "EnforcementController",
    "OperatorPromotionController",
    "WeightingProfileApiController",
)
