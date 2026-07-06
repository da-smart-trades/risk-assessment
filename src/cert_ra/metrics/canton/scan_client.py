# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Async client for the Splice Scan API (Canton Global Synchronizer).

The Scan API is the public data surface of the Canton Network. Each Super
Validator hosts a redundant Scan instance; the configured ``scan_urls`` are
tried in order as fallbacks (see :class:`cert_ra.settings.canton.CantonSettings`).

Endpoint paths and response shapes follow the published Splice OpenAPI
specifications (``hyperledger-labs/splice``). Parsing is intentionally
defensive — callers extract only the fields they need and tolerate missing
keys — so that minor upstream shape drift degrades gracefully rather than
hard-failing the whole snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, cast

import httpx

from cert_ra.settings.canton import get_canton_settings

if TYPE_CHECKING:
    from collections.abc import Mapping


class CantonScanError(RuntimeError):
    """Raised when every configured Scan URL fails for a request."""


class CantonScanClient:
    """Thin async wrapper over a set of redundant SV Scan endpoints.

    Use as an async context manager so the underlying ``httpx.AsyncClient`` is
    closed deterministically::

        async with CantonScanClient() as scan:
            dso = await scan.get_dso()
    """

    def __init__(self) -> None:
        """Snapshot the configured Scan URLs, timeout, and optional auth token."""
        settings = get_canton_settings()
        self._urls = [u.rstrip("/") for u in settings.scan_urls]
        self._timeout = settings.request_timeout_seconds
        token = settings.api_token
        self._headers: dict[str, str] = (
            {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
        )
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        """Open the underlying HTTP client."""
        self._client = httpx.AsyncClient(timeout=self._timeout, headers=self._headers)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:  # pragma: no cover - defensive
            msg = "CantonScanClient must be used as an async context manager"
            raise RuntimeError(msg)
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:  # noqa: ANN401 - decoded JSON body, shape varies per endpoint
        """Try each Scan URL in order, returning the first successful JSON body.

        Raises:
            CantonScanError: When no configured URL is reachable, or none is
                configured at all.
        """
        if not self._urls:
            msg = "canton scan: no scan URLs configured (set CERT_RA_CANTON_SCAN_URLS)"
            raise CantonScanError(msg)

        last_error: Exception | None = None
        for base in self._urls:
            url = f"{base}{path}"
            try:
                resp = await self._http.request(method, url, json=json, params=params)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - fall through to next URL
                last_error = exc

        msg = f"canton scan: all scan URLs failed for {method} {path}: {last_error}"
        raise CantonScanError(msg)

    # -- Operations / connectivity -----------------------------------------

    async def get_dso(self) -> dict[str, Any]:
        """``GET /v0/dso`` — DSO snapshot (voting threshold, SV nodes, rounds)."""
        return cast("dict[str, Any]", await self._request("GET", "/v0/dso"))

    async def get_scans(self) -> dict[str, Any]:
        """``GET /v0/scans`` — approved SV Scans grouped by synchronizer."""
        return cast("dict[str, Any]", await self._request("GET", "/v0/scans"))

    async def get_dso_sequencers(self) -> dict[str, Any]:
        """``GET /v0/dso-sequencers`` — SV sequencers grouped by synchronizer."""
        return cast("dict[str, Any]", await self._request("GET", "/v0/dso-sequencers"))

    async def get_validator_licenses(
        self, *, page_size: int, after: str | None = None
    ) -> dict[str, Any]:
        """``GET /v0/admin/validator/licenses`` — one page of approved validators."""
        params: dict[str, Any] = {"page_size": page_size}
        if after is not None:
            params["after"] = after
        return cast(
            "dict[str, Any]",
            await self._request("GET", "/v0/admin/validator/licenses", params=params),
        )

    # -- Current state / activity ------------------------------------------

    async def get_open_and_issuing_mining_rounds(self) -> dict[str, Any]:
        """``POST /v0/open-and-issuing-mining-rounds`` — currently open/issuing rounds.

        The endpoint is a cache-aware poll: passing empty ``cached_*`` id lists
        returns the full current set (we don't keep a client-side cache).
        ``open_mining_rounds`` / ``issuing_mining_rounds`` come back as maps
        keyed by contract id, not arrays.
        """
        return cast(
            "dict[str, Any]",
            await self._request(
                "POST",
                "/v0/open-and-issuing-mining-rounds",
                json={
                    "cached_open_mining_round_contract_ids": [],
                    "cached_issuing_round_contract_ids": [],
                },
            ),
        )

    async def get_acs_snapshot_timestamp(
        self, *, before: str, migration_id: int
    ) -> dict[str, Any]:
        """``GET /v0/state/acs/snapshot-timestamp`` — most recent ACS snapshot at/before ``before``.

        ``before`` must be a ``Z``-suffixed UTC timestamp (a ``+00:00`` offset
        is mangled in the query string). Returns ``{"record_time": "..."}``.
        """
        return cast(
            "dict[str, Any]",
            await self._request(
                "GET",
                "/v0/state/acs/snapshot-timestamp",
                params={"before": before, "migration_id": migration_id},
            ),
        )

    async def get_updates(
        self, *, after_record_time: str, after_migration_id: int, page_size: int
    ) -> Any:  # noqa: ANN401 - decoded JSON body, shape varies per endpoint
        """``POST /v2/updates`` — bulk update (transaction) stream after a cursor.

        The ``after`` cursor requires BOTH an exclusive record-time lower bound
        and the migration id it belongs to; the stream returns updates forward
        in record-time order.
        """
        return await self._request(
            "POST",
            "/v2/updates",
            json={
                "page_size": page_size,
                "after": {
                    "after_record_time": after_record_time,
                    "after_migration_id": after_migration_id,
                },
            },
        )
