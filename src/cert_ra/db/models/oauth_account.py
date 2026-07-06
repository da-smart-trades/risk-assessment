# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from advanced_alchemy.base import UUIDAuditBase
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from .user import User


class UserOauthAccount(UUIDAuditBase):
    """User Oauth Account.

    Two unique constraints enforced by the OIDC SSO design:

    - (oauth_name, account_id): the canonical identity key. The same
      provider-stable subject can only belong to one user.
    - (user_id): each user has AT MOST ONE linked OIDC provider. To
      switch providers (e.g., Google→Microsoft), the existing row is
      deleted and a new one inserted in a single transaction.
    """

    __tablename__ = "user_account_oauth"
    __table_args__ = (
        UniqueConstraint("oauth_name", "account_id", name="uq_oauth_provider_subject"),
        UniqueConstraint("user_id", name="uq_oauth_user_singleton"),
        {"comment": "Registered OAUTH2 Accounts for Users"},
    )
    __pii_columns__ = {"oauth_name", "account_email", "account_id"}  # noqa: RUF012

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="cascade"),
        nullable=False,
    )
    oauth_name: Mapped[str] = mapped_column(
        String(length=100), index=True, nullable=False
    )
    access_token: Mapped[str] = mapped_column(String(length=1024), nullable=False)
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(
        String(length=1024), nullable=True
    )
    account_id: Mapped[str] = mapped_column(
        String(length=320), index=True, nullable=False
    )
    account_email: Mapped[str] = mapped_column(String(length=320), nullable=False)
    scopes: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
        comment="OAuth scopes granted by the provider",
    )

    # -----------
    # ORM Relationships
    # ------------
    user_name: AssociationProxy[str] = association_proxy("user", "name")
    user_email: AssociationProxy[str] = association_proxy("user", "email")
    user: Mapped[User] = relationship(
        back_populates="oauth_accounts",
        viewonly=True,
        innerjoin=True,
        lazy="joined",
    )
