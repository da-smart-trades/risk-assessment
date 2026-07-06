# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from pathlib import Path

from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.template import TemplateConfig
from litestar_vite import (
    InertiaConfig,
    PathConfig,
    RuntimeConfig,
    TypeGenConfig,
    ViteConfig,
)

from cert_ra.api.domain.teams.schemas import CurrentTeam
from cert_ra.settings.api import get_app_settings, get_vite_settings
from cert_ra.utils import PACKAGE_ROOT


def get_vite_config() -> ViteConfig:
    """Get Vite configuration for the application."""
    app = get_app_settings()
    vite = get_vite_settings()

    return ViteConfig(
        dev_mode=vite.dev_mode,
        runtime=RuntimeConfig(executor="bun", trusted_proxies="*"),
        static_props={
            "appName": "My Application",
            "version": "1.0.0",
            "features": {"darkMode": True},
        },
        paths=PathConfig(
            root=PACKAGE_ROOT.parent.parent.resolve(),
            bundle_dir=PACKAGE_ROOT / "api/domain/web/public",
            resource_dir=Path("resources"),
        ),
        inertia=InertiaConfig(
            redirect_unauthorized_to="/login",
            extra_static_page_props={
                "canResetPassword": True,
                "hasTermsAndPrivacyPolicyFeature": True,
                "mustVerifyEmail": app.must_verify_email,
                "githubOAuthEnabled": app.github_oauth_enabled,
                "googleOAuthEnabled": app.google_oauth_enabled,
                "microsoftOAuthEnabled": app.microsoft_oauth_enabled,
            },
            extra_session_page_props={"currentTeam": CurrentTeam},
        ),
        types=TypeGenConfig(
            output=PACKAGE_ROOT.parent.parent / "resources" / "lib" / "generated"
        ),
    )


def get_template_config() -> TemplateConfig:
    """Get Jinja template configuration."""
    vite = get_vite_settings()
    return TemplateConfig(engine=JinjaTemplateEngine(directory=vite.template_dir))
