# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

import subprocess
from functools import cache
from pathlib import Path
from uuid import UUID

from advanced_alchemy.extensions.litestar.providers import create_service_dependencies
from advanced_alchemy.filters import CollectionFilter, OrderBy
from litestar import Controller, Request, get
from litestar.exceptions import NotFoundException
from litestar.response import File
from litestar_vite.inertia import InertiaRedirect

from cert_ra.api.domain.dashboards.schemas import DashboardPage, DashboardSummary
from cert_ra.api.domain.dashboards.services import DashboardService
from cert_ra.api.domain.favorites.resolver import resolve_favorites
from cert_ra.api.domain.favorites.services import UserFavoriteMetricService
from cert_ra.api.lib.schema import (
    AboutPage,
    ChainListPage,
    ChainShowPage,
    NoProps,
    ProtocolListPage,
    ProtocolShowPage,
    TokenListPage,
    TokenShowPage,
)
from cert_ra.api.lib.team_context import current_team_id_from_session
from cert_ra.db.models import (
    Dashboard,
    User,
)
from cert_ra.settings.api import get_app_settings
from cert_ra.types import ChainType, ProtocolType, TokenType

PUBLIC_ROOT = Path(__file__).parent / "public"


@cache
def _resolve_commit_sha() -> str:
    """The commit the running build was built from.

    Prefers ``AppSettings.commit_sha`` (set at build/deploy time); falls back
    to a live ``git`` lookup for local checkouts, or ``"unknown"``.
    """
    configured = get_app_settings().commit_sha.strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            cwd=str(Path(__file__).resolve().parents[5]),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unknown"


def _current_team_id(request: Request) -> UUID | None:
    """Read the viewer's effective team from session.

    The switcher selection, or the user's default team when none was
    chosen (set by ``current_user_from_session``). ``None`` means no
    team is active — market cards then show the stored global PD rather
    than a team-weighted recomputation.
    """
    return current_team_id_from_session(request.session)


def _select_current_dashboard(
    dashboards: list[Dashboard], board: UUID | None, user_id: UUID
) -> Dashboard:
    """Pick which dashboard to render from the visible set.

    Preference order: the explicitly requested ``board`` (if visible), then the
    user's own default page, then the first visible page. ``dashboards`` is
    assumed non-empty.
    """
    if board is not None:
        for dash in dashboards:
            if dash.id == board:
                return dash
    for dash in dashboards:
        if dash.owner_id == user_id and dash.is_default:
            return dash
    return dashboards[0]


class WebController(Controller):
    """Web Controller."""

    include_in_schema = False
    dependencies = {  # noqa: RUF012
        **create_service_dependencies(DashboardService, key="dashboards_service"),
        **create_service_dependencies(
            UserFavoriteMetricService, key="favorites_service"
        ),
    }
    signature_namespace = {  # noqa: RUF012
        "DashboardService": DashboardService,
        "UserFavoriteMetricService": UserFavoriteMetricService,
    }

    @get(path="/", name="home", exclude_from_auth=True)
    async def home(self, request: Request) -> InertiaRedirect:
        """Serve site root.

        Returns:
            Redirect to dashboard if authenticated, otherwise redirect to landing.
        """
        if request.session.get("user_id", False):
            return InertiaRedirect(request, request.url_for("dashboard"))
        return InertiaRedirect(request, request.url_for("landing"))

    @get(component="landing", path="/landing/", name="landing", exclude_from_auth=True)
    async def landing(self) -> NoProps:
        """Serve landing page.

        Returns:
            Empty page props.
        """
        return NoProps()

    @get(component="dashboard", path="/dashboard/", name="dashboard")
    async def dashboard(
        self,
        request: Request,
        dashboards_service: DashboardService,
        favorites_service: UserFavoriteMetricService,
        current_user: User,
        board: UUID | None = None,
    ) -> DashboardPage:
        """Serve dashboard home — a selected saved home page's favorites grid.

        Resolves the dashboards visible to the user (their own plus any shared
        into their teams), picks the one identified by ``?board=`` (falling back
        to their default, then the first visible page), and renders its pinned
        favorites. Auto metrics without a registered source render with
        ``value=None`` (the page shows ``—``). ``can_edit`` is true only when the
        user owns the selected page.

        Returns:
            Page props: the current page, the picker list, resolved favorites,
            and the edit permission.
        """
        team_ids = [m.team_id for m in current_user.teams]
        dashboards = await dashboards_service.list_for_user(
            user_id=current_user.id, team_ids=team_ids
        )
        if not dashboards:
            dashboards = [await dashboards_service.ensure_default(current_user.id)]
        current = _select_current_dashboard(dashboards, board, current_user.id)
        favorites = await favorites_service.list(
            CollectionFilter("dashboard_id", [current.id]),
            OrderBy(field_name="position"),
        )
        resolved = await resolve_favorites(
            favorites_service.repository.session,
            favorites,
            team_id=_current_team_id(request),
        )
        return DashboardPage(
            current=DashboardSummary.from_model(
                current, current_user_id=current_user.id
            ),
            dashboards=[
                DashboardSummary.from_model(d, current_user_id=current_user.id)
                for d in dashboards
            ],
            favorites=resolved,
            can_edit=current.owner_id == current_user.id,
        )

    @get(component="about", path="/about/", name="about")
    async def about(self) -> AboutPage:
        """Serve about page.

        Returns:
            The released build's commit sha.
        """
        return AboutPage(commit_sha=_resolve_commit_sha())

    @get(
        component="legal/privacy-policy",
        path="/privacy-policy/",
        name="privacy-policy",
        exclude_from_auth=True,
    )
    async def privacy_policy(self) -> NoProps:
        """Serve privacy policy page.

        Returns:
            Empty page props.
        """
        return NoProps()

    @get(
        component="legal/terms-of-service",
        path="/terms-of-service/",
        name="terms-of-service",
        exclude_from_auth=True,
    )
    async def terms_of_service(self) -> NoProps:
        """Serve terms of service page.

        Returns:
            Empty page props.
        """
        return NoProps()

    @get(
        path="/favicon.ico",
        name="favicon",
        exclude_from_auth=True,
        include_in_schema=False,
        sync_to_thread=False,
    )
    def favicon(self) -> File:
        """Serve favicon.

        Returns:
            Favicon file response.
        """
        return File(path=PUBLIC_ROOT / "favicon.ico")

    @get(
        path="/favicon.png",
        name="favicon-png",
        exclude_from_auth=True,
        include_in_schema=False,
        sync_to_thread=False,
    )
    def favicon_png(self) -> File:
        """Serve PNG favicon.

        Returns:
            Favicon PNG file response.
        """
        return File(path=PUBLIC_ROOT / "favicon.png", media_type="image/png")

    @get(
        path="/certora-logo.svg",
        name="certora-logo",
        exclude_from_auth=True,
        include_in_schema=False,
        sync_to_thread=False,
    )
    def certora_logo(self) -> File:
        """Serve Certora logo.

        Returns:
            SVG logo file response.
        """
        return File(path=PUBLIC_ROOT / "certora-logo.svg", media_type="image/svg+xml")

    @get(
        path="/certora-logo-with-text-white.svg",
        name="certora-logo-with-text-white",
        exclude_from_auth=True,
        include_in_schema=False,
        sync_to_thread=False,
    )
    def certora_logo_with_text_white(self) -> File:
        """Serve Certora logo with white text (dark mode).

        Returns:
            SVG logo file response.
        """
        return File(
            path=PUBLIC_ROOT / "certora-logo-with-text-white.svg",
            media_type="image/svg+xml",
        )

    @get(
        path="/certora-logo-with-text-black.svg",
        name="certora-logo-with-text-black",
        exclude_from_auth=True,
        include_in_schema=False,
        sync_to_thread=False,
    )
    def certora_logo_with_text_black(self) -> File:
        """Serve Certora logo with black text (light mode).

        Returns:
            SVG logo file response.
        """
        return File(
            path=PUBLIC_ROOT / "certora-logo-with-text-black.svg",
            media_type="image/svg+xml",
        )


class ChainsController(Controller):
    """Blockchain landing pages."""

    path = "/chains"
    include_in_schema = False

    @get(component="chain/list", path="/", name="chains.list")
    async def list_chains(self) -> ChainListPage:
        """Serve chains list page.

        Returns:
            All available chains.
        """
        return ChainListPage(chains=[c.value for c in ChainType])

    @get(component="chain/show", path="/{chain_name:str}/", name="chains.show")
    async def show_chain(self, chain_name: ChainType) -> ChainShowPage:
        """Serve individual chain landing page.

        Returns:
            Chain name for display.
        """
        chain = ChainType.get_chain_type(chain_name)
        if chain is None:
            msg = f"Chain {chain_name!r} not found"
            raise NotFoundException(msg)
        return ChainShowPage(chain=chain.value)


# Tokens with operator-published risk scores (one JSON fixture each under
# ``db/fixtures/tokens/``). Only these surface in the tokens list / sidebar
# dropdown; tokens that have flow metrics but no risk score (USDC, USDT0,
# aUSDC, cUSDC, wstETH) remain reachable by direct URL so favorites and
# bookmarks keep working, but are not advertised in navigation.
_SCORED_TOKENS: tuple[TokenType, ...] = (
    TokenType.UNI,
    TokenType.AAVE,
    TokenType.USDE,
    TokenType.WETH,
    TokenType.LINK,
    TokenType.STETH,
    TokenType.CBBTC,
)


class TokensController(Controller):
    """Token landing pages."""

    path = "/tokens"
    include_in_schema = False

    @get(component="token/list", path="/", name="tokens.list")
    async def list_tokens(self) -> TokenListPage:
        """Serve tokens list page.

        Returns:
            Tokens that have an operator-published risk score fixture.
        """
        return TokenListPage(tokens=[t.value for t in _SCORED_TOKENS])

    @get(component="token/show", path="/{token_name:str}/", name="tokens.show")
    async def show_token(self, token_name: TokenType) -> TokenShowPage:
        """Serve individual token landing page.

        Returns:
            Token name for display.
        """
        token = TokenType.get_token_type(token_name)
        if token is None:
            msg = f"Token {token_name!r} not found"
            raise NotFoundException(msg)
        return TokenShowPage(token=token.value)


class ProtocolsController(Controller):
    """Protocol landing pages."""

    path = "/protocols"
    include_in_schema = False

    @get(component="protocol/list", path="/", name="protocols.list")
    async def list_protocols(self) -> ProtocolListPage:
        """Serve protocols list page.

        Returns:
            All available protocols.
        """
        return ProtocolListPage(protocols=[p.value for p in ProtocolType])

    @get(component="protocol/show", path="/{protocol_name:str}/", name="protocols.show")
    async def show_protocol(self, protocol_name: ProtocolType) -> ProtocolShowPage:
        """Serve individual protocol landing page.

        Returns:
            Protocol name for display.
        """
        protocol = ProtocolType.get_protocol_type(protocol_name)
        if protocol is None:
            msg = f"Protocol {protocol_name!r} not found"
            raise NotFoundException(msg)
        return ProtocolShowPage(protocol=protocol.value)


# MarketsController moved to ``cert_ra.api.domain.markets.controllers`` —
# the new ``MarketController`` is backed by the dynamic ``market_config``
# table rather than the static ``MarketType`` enum. URLs changed from
# ``/markets/{market_name}`` to
# ``/markets/{protocol}/{chain_id}/{market_id_hex}/``.
