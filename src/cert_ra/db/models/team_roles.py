# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from enum import StrEnum


class TeamRoles(StrEnum):
    """Valid values for team roles.

    Roles follow Jetstream conventions:
    - ADMIN: Full permissions (create, read, update, delete)
    - EDITOR: Limited permissions (read, create, update)
    - MEMBER: Basic read-only access

    Note: Team ownership is tracked separately via is_owner on TeamMember.

    The two ``operator_*`` roles apply only to members of the operator
    team (``Team.is_operator``) and gate cross-customer power (PR-8,
    Operator team hardening — Control 2):
    - OPERATOR_SUPPORT: read-only access to customer data + tooling.
    - OPERATOR_TENANT_ADMIN: support plus cross-customer write actions
      (provision, revert, role change, enforced_provider).
    """

    ADMIN = "admin"
    EDITOR = "editor"
    MEMBER = "member"
    OPERATOR_SUPPORT = "operator_support"
    OPERATOR_TENANT_ADMIN = "operator_tenant_admin"
