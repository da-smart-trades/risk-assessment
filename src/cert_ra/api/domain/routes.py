# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING

from litestar import get
from litestar.static_files import create_static_files_router

from cert_ra.settings.db import get_storage_settings

from .accounts.controllers import (
    AccessController,
    EmailVerificationController,
    ForcePasswordChangeController,
    LandingController,
    LinkConfirmController,
    MfaChallengeController,
    MfaController,
    MfaEnrollmentController,
    MfaVerifyController,
    OAuthAccountController,
    OidcController,
    PasswordResetController,
    PasswordResetV2Controller,
    ProfileController,
    ProviderSwitchController,
    ReauthController,
    UnlockController,
    UserController,
    UserRoleController,
)
from .admin.controllers import (
    AdminAuditController,
    AdminDashboardController,
    AdminMarketConfigController,
    AdminRecoveryController,
    AdminTeamController,
    AdminUserController,
    AdminWeightingProfileController,
    EnforcementController,
    OperatorPromotionController,
    WeightingProfileApiController,
)
from .alerts.controllers import (
    AlertApiController,
    AlertIntegrationApiController,
    AlertPageController,
)
from .dashboards.controllers import DashboardApiController
from .manual_metrics.controllers import (
    ManualMetricApiController,
    ManualMetricPageController,
)
from .markets.controllers import MarketAlertApiController, MarketController
from .metrics.controllers import MetricsController
from .security_reports.controllers import (
    SecurityReportApiController,
    SecurityReportPageController,
)
from .tags.controllers import TagController
from .teams.controllers import (
    InvitationAcceptController,
    TeamController,
    TeamInvitationController,
    TeamMemberController,
    UserInvitationsController,
)
from .web.controllers import (
    ChainsController,
    ProtocolsController,
    TokensController,
    WebController,
)

if TYPE_CHECKING:
    from litestar.types.internal_types import ControllerRouterHandler


@get("/health", exclude_from_auth=True, include_in_schema=False)
async def health_check() -> dict[str, str]:
    """Health check endpoint for load balancers."""
    return {"status": "ok"}


def get_route_handlers() -> list[ControllerRouterHandler]:
    """Get the list of route handlers for the application."""
    uploads_router = create_static_files_router(
        directories=[get_storage_settings().upload_dir],
        path="/uploads",
        name="uploads",
    )

    return [
        # Health probe for the ALB target group. The `@get("/health", …)`
        # decorator only marks the function; Litestar doesn't see it until
        # we add it to the handler list explicitly. Without this line the
        # /health endpoint 404s and every new ECS task gets marked
        # unhealthy by the ALB, which causes the AppStack rolling deploy
        # to flap forever — the failure mode is silent enough that the
        # `_AUTH_ALLOWLIST` / NoTeam / MfaTrap middlewares all had
        # `/health` listed as if it were already a real route.
        health_check,
        uploads_router,
        AccessController,
        ForcePasswordChangeController,
        EmailVerificationController,
        LinkConfirmController,
        MfaChallengeController,
        MfaController,
        MfaEnrollmentController,
        MfaVerifyController,
        OAuthAccountController,
        OidcController,
        ProviderSwitchController,
        PasswordResetController,
        PasswordResetV2Controller,
        ProfileController,
        ReauthController,
        LandingController,
        UnlockController,
        UserController,
        TeamController,
        UserRoleController,
        TeamInvitationController,
        TeamMemberController,
        InvitationAcceptController,
        UserInvitationsController,
        TagController,
        MetricsController,
        ManualMetricApiController,
        ManualMetricPageController,
        AlertApiController,
        AlertIntegrationApiController,
        AlertPageController,
        DashboardApiController,
        SecurityReportApiController,
        SecurityReportPageController,
        ChainsController,
        TokensController,
        MarketController,
        MarketAlertApiController,
        ProtocolsController,
        WebController,
        # Admin controllers
        AdminDashboardController,
        AdminUserController,
        AdminTeamController,
        AdminAuditController,
        AdminRecoveryController,
        AdminMarketConfigController,
        AdminWeightingProfileController,
        EnforcementController,
        OperatorPromotionController,
        # Public JSON helpers for the admin weighting-profile form
        WeightingProfileApiController,
    ]
