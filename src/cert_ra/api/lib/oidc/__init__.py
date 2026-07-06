# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""OIDC integration for the multi-tenant SSO flow.

Distinct from the existing ``cert_ra.api.lib.oauth`` module, which is
OAuth2-only via httpx-oauth and used by the legacy
``/o/<provider>/complete/`` registration routes. This package adds
proper OIDC id_token validation (signature, aud, iss, nonce, exp,
email_verified) for the new ``/auth/<provider>/login`` flow that
enforces admin-driven user provisioning.

The two paths coexist in PR-2a; the legacy ``/o/`` routes will be
decommissioned in a later PR once admin provisioning UI is in place.
"""
