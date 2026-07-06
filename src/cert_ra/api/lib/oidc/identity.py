# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""OIDC identity extraction — security-critical.

The single responsibility here is: turn an authorization-code grant
into a validated ``ExtractedIdentity`` (provider, subject, verified
email, name) or raise ``IdentityError``.

Validation rules (per the OIDC SSO design):

- All OIDC id_tokens MUST carry ``email_verified=true``. We do NOT
  fall back to ``preferred_username`` for Microsoft Entra (the
  ``preferred_username`` claim is tenant-admin-controlled and unsafe
  across tenants — closes the cross-tenant impersonation hole).
- Google tokens additionally MUST carry the ``hd`` claim (workspace
  domain). Personal ``@gmail.com`` accounts are refused.
- GitHub is OAuth2 only — fetch ``/user`` + ``/user/emails`` and use
  the primary verified email.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from authlib.jose import jwt
from authlib.jose.errors import JoseError

from cert_ra.api.lib.oidc.providers import (
    Provider,
    _http,
    get_discovery,
    get_jwks,
)

if TYPE_CHECKING:
    from cert_ra.api.lib.oidc.providers import ProviderConfig


class IdentityError(Exception):
    """Raised when an OIDC token / OAuth profile cannot be validated.

    Carries a structured ``reason`` for logs; controllers render a
    generic page (or routes to ``/auth/idp-config-required`` for the
    specific ``no_verified_email`` cause) so the user cannot
    distinguish causes from the response shape.
    """

    def __init__(self, reason: str) -> None:
        """Initialize with a structured reason string."""
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True, frozen=True)
class ExtractedIdentity:
    """A validated identity extracted from an OIDC / OAuth flow.

    Every field has been verified — ``email`` is the IdP-asserted
    ``email_verified=true`` value (or GitHub's verified primary),
    lowercased. ``subject`` is the provider-stable user ID
    (``sub`` for OIDC, ``id`` for GitHub).

    Anti-cross-tenant note: ``email`` is NEVER read from Microsoft's
    ``preferred_username`` claim because that claim is tenant-admin-
    controlled and is not trustworthy across tenants. See design.md
    for the threat model.
    """

    provider: Provider
    subject: str
    email: str
    name: str | None


async def extract_identity(
    provider: Provider,
    cfg: ProviderConfig,
    token: dict,
    *,
    expected_nonce: str,
    client_id: str,
) -> ExtractedIdentity:
    """Validate a token + return the ExtractedIdentity, or raise.

    Args:
        provider: Which IdP we just authenticated against.
        cfg: The provider's config (used for discovery / endpoints).
        token: The OAuth2 token dict from the code exchange. For OIDC
            providers, ``token["id_token"]`` must be present and
            validatable.
        expected_nonce: The nonce stashed in the session at /login
            time. Pinned in the ``claims_options["nonce"]`` so a
            replayed id_token from a different login attempt is
            rejected.
        client_id: This relying party's client ID. Pinned in
            ``claims_options["aud"]`` so tokens minted for any OTHER
            OAuth client at the same IdP are rejected.

    Returns:
        ExtractedIdentity if validation succeeds.

    Raises:
        IdentityError: On any validation failure. The ``reason``
            attribute carries structured detail (``"missing_id_token"``,
            ``"no_verified_email"``, ``"personal_account_blocked"``,
            etc.).
    """
    if provider == Provider.GITHUB:
        return await _extract_github(cfg, token)
    return await _extract_oidc(
        provider, cfg, token, expected_nonce=expected_nonce, client_id=client_id
    )


async def _extract_oidc(
    provider: Provider,
    cfg: ProviderConfig,
    token: dict,
    *,
    expected_nonce: str,
    client_id: str,
) -> ExtractedIdentity:
    id_token = token.get("id_token")
    if not id_token:
        raise IdentityError("missing_id_token")
    if cfg.discovery_url is None:
        raise IdentityError("no_discovery_url")

    discovery = await get_discovery(cfg.discovery_url)
    jwks = await get_jwks(discovery["jwks_uri"])

    claims_options = {
        "iss": {
            "essential": True,
            "values": _expected_issuers(provider, discovery),
        },
        "aud": {"essential": True, "value": client_id},
        "exp": {"essential": True},
        "nonce": {"essential": True, "value": expected_nonce},
    }
    try:
        claims = jwt.decode(id_token, jwks, claims_options=claims_options)  # pyright: ignore[reportArgumentType]
        claims.validate(now=int(datetime.now(UTC).timestamp()), leeway=30)
    except JoseError:
        # Signing-key rotation? Force-refresh JWKS once and retry.
        jwks = await get_jwks(discovery["jwks_uri"], force_refresh=True)
        try:
            claims = jwt.decode(id_token, jwks, claims_options=claims_options)  # pyright: ignore[reportArgumentType]
            claims.validate(now=int(datetime.now(UTC).timestamp()), leeway=30)
        except JoseError as exc:
            raise IdentityError("token_validation_failed") from exc

    # email_verified MUST be present and true. preferred_username is
    # NOT a fallback — that claim is tenant-admin-controlled in Entra
    # and is unsafe across tenants. Routing to
    # /auth/idp-config-required is owned by the controller catching
    # `IdentityError(reason="no_verified_email")`.
    if not claims.get("email_verified"):
        raise IdentityError("no_verified_email")
    email = (claims.get("email") or "").lower()
    if not email or "@" not in email:
        raise IdentityError("no_email_claim")

    if provider == Provider.GOOGLE and not claims.get("hd"):
        # Personal @gmail.com accounts have no `hd` claim. Workspace
        # accounts do. This is a policy decision (B2B product) — not
        # the cross-tenant security boundary, which is handled by the
        # aud + iss + email_verified checks above.
        raise IdentityError("personal_account_blocked")

    return ExtractedIdentity(
        provider=provider,
        subject=str(claims["sub"]),
        email=email,
        name=claims.get("name"),
    )


def _expected_issuers(provider: Provider, discovery: dict) -> list[str]:
    """The set of valid ``iss`` claim values for ``provider``.

    Microsoft Entra's discovery document returns
    ``iss = "https://login.microsoftonline.com/{tenantid}/v2.0"``
    with the literal ``{tenantid}`` placeholder; Authlib substitutes
    the actual GUID from the token at validation time when the
    placeholder is in the allowed list.
    """
    if provider == Provider.MICROSOFT:
        return ["https://login.microsoftonline.com/{tenantid}/v2.0"]
    return [discovery["issuer"]]


async def _extract_github(cfg: ProviderConfig, token: dict) -> ExtractedIdentity:
    """GitHub OAuth2 → identity.

    No id_token, no signature verification. Trust is anchored at TLS
    to ``api.github.com``. Two requests:
      - ``/user`` for ``id`` and ``name``
      - ``/user/emails`` to find the verified primary email
    """
    access_token = token.get("access_token")
    if not access_token:
        raise IdentityError("missing_access_token")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }
    user_response = await _http().get("https://api.github.com/user", headers=headers)
    emails_response = await _http().get(
        "https://api.github.com/user/emails", headers=headers
    )
    user_response.raise_for_status()
    emails_response.raise_for_status()
    user = user_response.json()
    emails = emails_response.json()

    primary = next(
        (entry for entry in emails if entry.get("primary") and entry.get("verified")),
        None,
    )
    if primary is None:
        raise IdentityError("no_verified_email")

    email = primary["email"].lower()
    return ExtractedIdentity(
        provider=Provider.GITHUB,
        subject=str(user["id"]),
        email=email,
        name=user.get("name") or user.get("login"),
    )
