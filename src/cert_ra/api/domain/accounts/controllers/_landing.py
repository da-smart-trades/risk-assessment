# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Auth-flow landing-page controller.

The OIDC sign-in handlers (and login) stash an ``auth_flow`` key in the
session naming the dead-end page they want to send the user to. This
controller serves those pages from a small, anti-enumeration-friendly
surface — every page renders the same generic content shape so a
forged direct ``GET /auth/wrong-provider`` (without the session flag)
gets routed to ``/auth/login``.

Pages served:

- ``/auth/wrong-provider``        — OIDC resolver said the IdP doesn't
                                    match what the user is enrolled in.
- ``/auth/invitation-required``   — OIDC resolver couldn't find a
                                    pre-provisioned User row.
- ``/auth/idp-config-required``   — IdP token didn't carry
                                    ``email_verified`` or fell into a
                                    similar misconfiguration.
- ``/auth/account-disabled``      — ``user.is_active`` was ``False``.
- ``/auth/no-team``               — Authenticated user has no
                                    ``TeamMember`` rows.
- ``/auth/operator-setup-required`` — Stub for PR-8's operator
                                    bootstrapping flow.
- ``/auth/team-policy``           — Stub for PR-7's enforced-provider
                                    flow.

Auth landing pages; anti-enumeration rules apply.
"""

from __future__ import annotations

from typing import Any

from litestar import Controller, Request, get
from litestar_vite.inertia import InertiaRedirect

from cert_ra.api.lib.schema import NoProps

__all__ = ("LandingController",)


class _Page:
    """Marker for one auth-flow landing page."""

    __slots__ = ("component", "name", "path", "session_flag")

    def __init__(
        self, *, name: str, path: str, component: str, session_flag: str
    ) -> None:
        """Capture the route + Inertia component + session flag triggering it."""
        self.name = name
        self.path = path
        self.component = component
        self.session_flag = session_flag


_PAGES = {
    "wrong_provider": _Page(
        name="auth.wrong-provider",
        path="/auth/wrong-provider/",
        component="auth/wrong-provider",
        session_flag="wrong_provider",
    ),
    "invitation_required": _Page(
        name="auth.invitation-required",
        path="/auth/invitation-required/",
        component="auth/invitation-required",
        session_flag="invitation_required",
    ),
    "idp_config_required": _Page(
        name="auth.idp-config-required",
        path="/auth/idp-config-required/",
        component="auth/idp-config-required",
        session_flag="idp_config_required",
    ),
    "account_disabled": _Page(
        name="auth.account-disabled",
        path="/auth/account-disabled/",
        component="auth/account-disabled",
        session_flag="account_disabled",
    ),
    "no_team": _Page(
        name="auth.no-team",
        path="/auth/no-team/",
        component="auth/no-team",
        session_flag="no_team",
    ),
    "operator_setup_required": _Page(
        name="auth.operator-setup-required",
        path="/auth/operator-setup-required/",
        component="auth/operator-setup-required",
        session_flag="operator_setup_required",
    ),
    "team_policy": _Page(
        name="auth.team-policy",
        path="/auth/team-policy/",
        component="auth/team-policy",
        session_flag="team_policy",
    ),
}


def _consume_auth_flow(request: Request, expected: str) -> dict[str, Any] | None:
    """Pop ``auth_flow`` from session if it matches ``expected``.

    Returns the ``context`` dict the OIDC handler stashed, or ``None``
    if the user landed here without the right session flag (direct-URL
    navigation, replay, etc.).
    """
    flow = request.session.get("auth_flow")
    if not isinstance(flow, dict):
        return None
    if flow.get("page") != expected:
        return None
    context = flow.get("context")
    if not isinstance(context, dict):
        context = {}
    return context


class LandingController(Controller):
    """Auth-flow dead-end pages."""

    include_in_schema = False
    exclude_from_auth = True
    cache = False

    @get(
        component="auth/wrong-provider",
        name="auth.wrong-provider",
        path="/auth/wrong-provider/",
    )
    async def wrong_provider(self, request: Request) -> NoProps | InertiaRedirect:
        """Render the wrong-provider page.

        Body intentionally omits the admin email, team name, and the
        target user's email (design #118).
        """
        if _consume_auth_flow(request, "wrong_provider") is None:
            return InertiaRedirect(request, request.url_for("login"))
        return NoProps()

    @get(
        component="auth/invitation-required",
        name="auth.invitation-required",
        path="/auth/invitation-required/",
    )
    async def invitation_required(self, request: Request) -> NoProps | InertiaRedirect:
        """Render the invitation-required page.

        Anti-enumeration: the body is identical across triggering
        causes (unknown email, personal-account block, etc.) — the
        only signal a recipient gets is "ask your admin" (design #99).
        """
        if _consume_auth_flow(request, "invitation_required") is None:
            return InertiaRedirect(request, request.url_for("login"))
        return NoProps()

    @get(
        component="auth/idp-config-required",
        name="auth.idp-config-required",
        path="/auth/idp-config-required/",
    )
    async def idp_config_required(self, request: Request) -> NoProps | InertiaRedirect:
        """Render the IdP-misconfiguration page."""
        if _consume_auth_flow(request, "idp_config_required") is None:
            return InertiaRedirect(request, request.url_for("login"))
        return NoProps()

    @get(
        component="auth/account-disabled",
        name="auth.account-disabled",
        path="/auth/account-disabled/",
    )
    async def account_disabled(self, request: Request) -> NoProps | InertiaRedirect:
        """Render the account-disabled page."""
        if _consume_auth_flow(request, "account_disabled") is None:
            return InertiaRedirect(request, request.url_for("login"))
        return NoProps()

    @get(
        component="auth/no-team",
        name="auth.no-team",
        path="/auth/no-team/",
    )
    async def no_team(self, request: Request) -> NoProps:  # noqa: ARG002
        """Render the no-team page.

        Unlike the OIDC dead-ends, this page can be reached either
        from the ``NoTeamMiddleware`` redirect or from a direct
        URL — both are valid. The page itself is informational.
        """
        return NoProps()

    @get(
        component="auth/operator-setup-required",
        name="auth.operator-setup-required",
        path="/auth/operator-setup-required/",
    )
    async def operator_setup_required(
        self, request: Request
    ) -> NoProps | InertiaRedirect:
        """Render the operator-setup-required page (PR-8 placeholder)."""
        if _consume_auth_flow(request, "operator_setup_required") is None:
            return InertiaRedirect(request, request.url_for("login"))
        return NoProps()

    @get(
        component="auth/team-policy",
        name="auth.team-policy",
        path="/auth/team-policy/",
    )
    async def team_policy(self, request: Request) -> NoProps | InertiaRedirect:
        """Render the team-policy page (PR-7 placeholder)."""
        if _consume_auth_flow(request, "team_policy") is None:
            return InertiaRedirect(request, request.url_for("login"))
        return NoProps()
