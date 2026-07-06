# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""OIDC provider configurations + discovery / JWKS caching.

Three providers supported:

- **Google Workspace** — real OIDC with discovery URL. Requires the
  ``hd`` claim (Workspace domain) — personal Gmail accounts are
  refused at identity-extraction time.
- **Microsoft Entra (Work / School)** — real OIDC with the
  ``/organizations/v2.0`` authority. Multi-tenant app registration;
  the ``tid`` claim identifies the user's tenant. Personal MS accounts
  refused at the authority level.
- **GitHub** — OAuth2 only (no id_token). The verified primary email
  from ``/user/emails`` is the trust root.

Discovery documents and JWKS are cached per-process. Force-refresh on
signature failure is handled in ``identity.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache

import httpx

from cert_ra.settings.api import get_app_settings


class Provider(StrEnum):
    """Identifier for a supported OIDC provider."""

    GOOGLE = "google"
    MICROSOFT = "microsoft"
    GITHUB = "github"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Static configuration for one OIDC provider.

    Attributes:
        client_id: OAuth2 / OIDC client identifier.
        client_secret: OAuth2 / OIDC client secret.
        discovery_url: OIDC discovery document URL. ``None`` for
            providers that aren't OIDC (GitHub) — in that case the
            authorize / token / userinfo endpoints are hardcoded.
        authorize_endpoint: Authorization endpoint (used when
            ``discovery_url`` is None).
        token_endpoint: Token endpoint (used when ``discovery_url`` is
            None).
        userinfo_endpoint: Userinfo / profile endpoint (used by the
            GitHub path).
        scopes: Scopes to request in the authorize URL.
        extra_authorize_params: Additional query params for the
            authorize URL (e.g., ``prompt``, ``access_type``).
    """

    client_id: str
    client_secret: str
    discovery_url: str | None
    authorize_endpoint: str | None
    token_endpoint: str | None
    userinfo_endpoint: str | None
    scopes: tuple[str, ...]
    extra_authorize_params: dict[str, str] = field(default_factory=dict)


def load_provider_configs() -> dict[Provider, ProviderConfig]:
    """Build the per-provider config map from the current AppSettings.

    Credentials live on AppSettings (alongside the legacy
    ``github_oauth2_client_id`` and ``google_oauth2_client_id`` fields
    used by the OAuth2-only registration path). Missing credentials
    leave the provider's config in place but with empty strings — the
    controllers gate on ``app_settings.<provider>_oauth_enabled``
    before mounting the routes.
    """
    settings = get_app_settings()
    return {
        Provider.GOOGLE: ProviderConfig(
            client_id=settings.google_oauth2_client_id,
            client_secret=settings.google_oauth2_client_secret,
            discovery_url=(
                "https://accounts.google.com/.well-known/openid-configuration"
            ),
            authorize_endpoint=None,
            token_endpoint=None,
            userinfo_endpoint=None,
            scopes=("openid", "email", "profile"),
            extra_authorize_params={
                "access_type": "online",
                "prompt": "select_account",
            },
        ),
        Provider.MICROSOFT: ProviderConfig(
            client_id=settings.microsoft_oauth2_client_id,
            client_secret=settings.microsoft_oauth2_client_secret,
            # 'organizations' = Work / School only, not personal MS accounts.
            discovery_url=(
                "https://login.microsoftonline.com/organizations/v2.0/"
                ".well-known/openid-configuration"
            ),
            authorize_endpoint=None,
            token_endpoint=None,
            userinfo_endpoint=None,
            scopes=("openid", "email", "profile"),
            extra_authorize_params={"prompt": "select_account"},
        ),
        Provider.GITHUB: ProviderConfig(
            client_id=settings.github_oauth2_client_id,
            client_secret=settings.github_oauth2_client_secret,
            # GitHub is OAuth2, not OIDC.
            discovery_url=None,
            authorize_endpoint="https://github.com/login/oauth/authorize",
            token_endpoint="https://github.com/login/oauth/access_token",  # noqa: S106 — URL, not a password
            userinfo_endpoint="https://api.github.com/user",
            scopes=("read:user", "user:email"),
            extra_authorize_params={},
        ),
    }


@lru_cache(maxsize=1)
def _http() -> httpx.AsyncClient:
    """Per-process shared async HTTP client for discovery + JWKS."""
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))


# Discovery + JWKS caches keyed by URL.
_discovery_cache: dict[str, dict] = {}
_jwks_cache: dict[str, dict] = {}


async def get_discovery(url: str) -> dict:
    """Fetch and cache the OIDC discovery document at ``url``.

    Per-process cache; never invalidated automatically (the discovery
    document changes very rarely). Restart the process to pick up
    issuer or endpoint changes.
    """
    if url not in _discovery_cache:
        response = await _http().get(url)
        response.raise_for_status()
        _discovery_cache[url] = response.json()
    return _discovery_cache[url]


async def get_end_session_url(
    provider_value: str, post_logout_redirect_uri: str
) -> str | None:
    """Build the RP-initiated logout URL for ``provider_value``, or None.

    Returns the provider's ``end_session_endpoint`` (from its discovery
    document) with ``post_logout_redirect_uri`` + ``client_id`` appended,
    so logout also terminates the IdP session. Returns ``None`` when the
    provider has no discovery document (GitHub), exposes no
    ``end_session_endpoint`` (e.g. Google), isn't configured, or the
    discovery fetch fails — callers fall back to a local-only logout.
    """
    from urllib.parse import urlencode

    try:
        prov = Provider(provider_value)
    except ValueError:
        return None
    cfg = load_provider_configs().get(prov)
    if cfg is None or not cfg.discovery_url or not cfg.client_id:
        return None
    try:
        discovery = await get_discovery(cfg.discovery_url)
    except httpx.HTTPError:
        return None
    endpoint = discovery.get("end_session_endpoint")
    if not endpoint:
        return None
    params = urlencode(
        {
            "post_logout_redirect_uri": post_logout_redirect_uri,
            "client_id": cfg.client_id,
        }
    )
    return f"{endpoint}?{params}"


async def get_jwks(jwks_uri: str, *, force_refresh: bool = False) -> dict:
    """Fetch and cache the JWKS document at ``jwks_uri``.

    Pass ``force_refresh=True`` after a signature validation failure
    to handle key rotation. The caller (``identity.py``) retries
    validation once with a refreshed JWKS before giving up.
    """
    if force_refresh or jwks_uri not in _jwks_cache:
        response = await _http().get(jwks_uri)
        response.raise_for_status()
        _jwks_cache[jwks_uri] = response.json()
    return _jwks_cache[jwks_uri]
