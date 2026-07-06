# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog
from advanced_alchemy.exceptions import RepositoryError
from advanced_alchemy.repository import SQLAlchemyAsyncSlugRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    is_dict,
    is_dict_with_field,
    is_dict_without_field,
    schema_dump,
)
from advanced_alchemy.utils.text import slugify
from sqlalchemy import select
from uuid_utils import uuid7

from cert_ra.db.models import Team, TeamMember, TeamRoles
from cert_ra.db.models.tag import Tag
from cert_ra.db.models.user import User

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)

if TYPE_CHECKING:
    from uuid import UUID

    from advanced_alchemy.service import ModelDictT

__all__ = ("TeamService",)

logger = structlog.get_logger()


class TeamService(SQLAlchemyAsyncRepositoryService[Team]):
    """Team Service."""

    class Repo(SQLAlchemyAsyncSlugRepository[Team]):
        """Team SQLAlchemy Repository."""

        model_type = Team

    repository_type = Repo
    match_fields = ["name"]  # noqa: RUF012

    async def to_model_on_create(self, data: ModelDictT[Team]) -> ModelDictT[Team]:
        """Transform data before creating a team with slug, owner, and tags.

        Returns:
            Transformed team data with slug, owner member, and tags populated.
        """
        return await self._populate_with_owner_and_tags(
            await self._populate_slug(self._normalize_domain(schema_dump(data))),
            "create",
        )

    async def to_model_on_update(self, data: ModelDictT[Team]) -> ModelDictT[Team]:
        """Transform data before updating a team with slug and tags if provided.

        Returns:
            Transformed team data with slug and tags updated.
        """
        data = schema_dump(data)
        if is_dict(data):
            data.pop("domain", None)
        return await self._populate_with_owner_and_tags(
            await self._populate_slug(data), "update"
        )

    @staticmethod
    def _normalize_domain(data: ModelDictT[Team]) -> ModelDictT[Team]:
        """Lowercase, strip a leading ``@``, and validate the team domain.

        Returns:
            Team data with a normalized ``domain`` value (or ``None``).

        Raises:
            RepositoryError: If the domain is not a valid hostname.
        """
        if not is_dict(data) or "domain" not in data:
            return data
        raw = data["domain"]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            data["domain"] = None
            return data
        normalized = str(raw).strip().lower().lstrip("@")
        if not _DOMAIN_RE.match(normalized):
            msg = f"'{raw}' is not a valid domain."
            raise RepositoryError(msg)
        data["domain"] = normalized
        return data

    async def _populate_slug(self, data: ModelDictT[Team]) -> ModelDictT[Team]:
        """Auto-generate slug from name if not provided.

        Returns:
            Team data with auto-generated slug if name provided without slug.
        """
        if is_dict_without_field(data, "slug") and is_dict_with_field(data, "name"):
            data["slug"] = await self.repository.get_available_slug(data["name"])
        return data

    async def _populate_with_owner_and_tags(
        self, data: ModelDictT[Team], operation: str
    ) -> ModelDictT[Team]:
        """Handle owner and tags assignment.

        Returns:
            Team data with owner member and tags populated.

        Raises:
            RepositoryError: If owner_id is not provided on create.
        """
        if not is_dict(data):
            return data

        owner_id: UUID | None = data.pop("owner_id", None)
        owner: User | None = data.pop("owner", None)
        input_tags: list[str] | None = data.pop("tags", None)

        if operation == "create":
            if "id" not in data:
                data["id"] = uuid7()
            if owner_id is None and owner is None:
                msg = "'owner_id' is required to create a team."
                raise RepositoryError(msg)

        data = await super().to_model(data)

        if operation == "create":
            data.members.append(
                TeamMember(user=owner, role=TeamRoles.ADMIN, is_owner=True)
                if owner
                else TeamMember(user_id=owner_id, role=TeamRoles.ADMIN, is_owner=True),
            )

        if input_tags is not None:
            existing = {tag.name for tag in data.tags}
            for tag in [t for t in data.tags if t.name not in input_tags]:
                data.tags.remove(tag)
            data.tags.extend(
                [
                    await Tag.as_unique_async(
                        self.repository.session, name=name, slug=slugify(name)
                    )
                    for name in input_tags
                    if name not in existing
                ]
            )

        return data

    async def ensure_operator_team(
        self, name: str, domain: str, enforced_provider: str | None = None
    ) -> Team | None:
        """Ensure exactly one operator team exists; create it if missing.

        The first available superuser is used as the owner. If no superuser
        exists yet (e.g. a freshly initialized database), team creation is
        skipped so the next startup can retry.

        Args:
            name: Display name of the operator team.
            domain: Email domain that restricts membership.
            enforced_provider: If set, pins the operator team to this OIDC
                provider at creation time (PR-8, Control 1). Inert until
                the ``enforced_provider`` feature flag is on.

        Returns:
            The existing or newly created operator team, or ``None`` if no
            superuser was available to act as owner.
        """
        existing = await self.get_one_or_none(is_operator=True)
        if existing is not None:
            return existing

        owner_result = await self.repository.session.execute(
            select(User)
            .where(User.is_superuser.is_(True))
            .order_by(User.created_at)
            .limit(1)
        )
        owner = owner_result.scalar_one_or_none()
        if owner is None:
            await logger.awarning(
                "operator_team.skipped_no_superuser",
                message=(
                    "No superuser found; skipping operator team creation. "
                    "Create a superuser and restart to provision the operator team."
                ),
            )
            return None

        team = await self.create(
            {
                "name": name,
                "domain": domain,
                "is_operator": True,
                "owner": owner,
                "enforced_provider": enforced_provider,
            },
            auto_commit=True,
        )
        await logger.ainfo(
            "operator_team.created",
            team_id=str(team.id),
            name=team.name,
            domain=team.domain,
        )
        return team

    @staticmethod
    def can_view_all(user: User) -> bool:
        """Check if user can view all teams.

        Returns:
            True if user is superuser, False otherwise.
        """
        return bool(
            user.is_superuser
            or any(
                assigned_role.role.name == "Superuser" for assigned_role in user.roles
            )
        )
