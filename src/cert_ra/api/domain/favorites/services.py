# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Favorite-metric service — CRUD with favoritable-score validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from advanced_alchemy.exceptions import NotFoundError, RepositoryError
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from advanced_alchemy.service import (
    SQLAlchemyAsyncRepositoryService,
    schema_dump,
)
from sqlalchemy import select

from cert_ra.db.models import ManualMetric, MarketConfig, UserFavoriteMetric
from cert_ra.types import MetricCategory

if TYPE_CHECKING:
    from advanced_alchemy.service import ModelDictT

__all__ = ("UserFavoriteMetricService",)

# Manual-metric categories whose published, shared rows may be favorited.
# PROTOCOL_SCORE is the protocol/market PD card; TOKEN_SCORE is the token PD
# card the token-metrics seeder computes. Both store the display PD in a
# ``SUMMARY`` sub-category row.
_FAVORITABLE_SCORE_CATEGORIES = frozenset(
    {MetricCategory.PROTOCOL_SCORE, MetricCategory.TOKEN_SCORE}
)


class UserFavoriteMetricService(SQLAlchemyAsyncRepositoryService[UserFavoriteMetric]):
    """CRUD service for per-user favorite metrics."""

    class Repo(SQLAlchemyAsyncRepository[UserFavoriteMetric]):
        """UserFavoriteMetric SQLAlchemy Repository."""

        model_type = UserFavoriteMetric

    repository_type = Repo

    async def to_model_on_create(
        self, data: ModelDictT[UserFavoriteMetric]
    ) -> ModelDictT[UserFavoriteMetric]:
        """Validate the create payload before it hits the database.

        Enforces:
          - ``dashboard_id`` was injected by the controller (the favorite is
            pinned to a specific dashboard, whose ownership the controller has
            already verified).
          - Exactly one of ``metric_type`` / ``manual_metric_id`` /
            ``market_config_id`` is set (mirrors
            ``ck_user_favorite_metric_target_xor`` so we get a clean
            error instead of an IntegrityError).
          - If ``manual_metric_id`` is set, the referenced row is a
            **shared** (``team_id IS NULL``) published score metric
            (``PROTOCOL_SCORE`` or ``TOKEN_SCORE``). Team-owned score
            metrics are not favoritable.
          - If ``market_config_id`` is set, the referenced protocol
            row exists and is ``enabled = true``, and the per-market
            identity fields ``favorite_chain_id`` / ``favorite_market_id_hex``
            / ``favorite_label`` are populated. The displayed value is
            the latest ``MarketScore.final_pd`` for the specific market,
            resolved at read time by the favorites resolver.

        Raises:
            RepositoryError: If any precondition fails. Surfaces as a 400 to
                the client.
        """
        data = schema_dump(data)
        if "dashboard_id" not in data:
            msg = "dashboard_id must be set by the controller."
            raise RepositoryError(msg)

        has_metric_type = data.get("metric_type") is not None
        has_manual_id = data.get("manual_metric_id") is not None
        has_market_id = data.get("market_config_id") is not None
        targets_set = sum((has_metric_type, has_manual_id, has_market_id))
        if targets_set != 1:
            msg = (
                "Favorite must set exactly one of metric_type, "
                "manual_metric_id, or market_config_id."
            )
            raise RepositoryError(msg)

        if has_manual_id:
            await self._require_shared_favoritable_score(data["manual_metric_id"])
        if has_market_id:
            self._require_market_identity_fields(data)
            await self._require_enabled_market(data["market_config_id"])
        return data

    async def _require_shared_favoritable_score(self, manual_metric_id: object) -> None:
        """Fail unless the referenced metric is a published shared score row.

        Three invariants are checked in one SELECT:
          - ``category`` in :data:`_FAVORITABLE_SCORE_CATEGORIES`
            (``PROTOCOL_SCORE`` or ``TOKEN_SCORE``)
          - ``team_id IS NULL`` (shared / operator-published scope)
          - ``is_published`` (drafts are not favoritable)

        Args:
            manual_metric_id: UUID of the target manual metric.

        Raises:
            NotFoundError: If the row doesn't exist.
            RepositoryError: If the category is wrong, the metric is
                team-owned, or the metric is still a draft.
        """
        stmt = select(
            ManualMetric.category, ManualMetric.team_id, ManualMetric.is_published
        ).where(ManualMetric.id == manual_metric_id)
        result = await self.repository.session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            msg = f"Manual metric {manual_metric_id} does not exist."
            raise NotFoundError(msg)
        category, team_id, is_published = row
        if category not in _FAVORITABLE_SCORE_CATEGORIES:
            msg = (
                "Only PROTOCOL_SCORE and TOKEN_SCORE manual metrics can be "
                f"favorited (got {category.value})."
            )
            raise RepositoryError(msg)
        if team_id is not None:
            msg = (
                "Only shared (operator-published) score manual metrics "
                "can be favorited."
            )
            raise RepositoryError(msg)
        if not is_published:
            msg = "This manual metric is still a draft. Publish it before favoriting."
            raise RepositoryError(msg)

    @staticmethod
    def _require_market_identity_fields(data: dict) -> None:
        """Reject market favorites that don't carry the per-market identity trio.

        ``market_config_id`` names the protocol; the favorite must also
        carry ``favorite_chain_id`` / ``favorite_market_id_hex`` /
        ``favorite_label`` so the row pins a single market and the UI
        can render the label without re-running yarn.
        """
        missing = [
            field
            for field in (
                "favorite_chain_id",
                "favorite_market_id_hex",
                "favorite_label",
            )
            if data.get(field) in (None, "")
        ]
        if missing:
            msg = (
                "Market favorite requires favorite_chain_id, "
                "favorite_market_id_hex, and favorite_label (missing: "
                f"{', '.join(missing)})."
            )
            raise RepositoryError(msg)

    async def _require_enabled_market(self, market_config_id: object) -> None:
        """Fail unless the referenced protocol row exists and is enabled.

        Disabled protocols are excluded so that a favorite never points
        at a market the scorer is no longer producing fresh PDs for.
        The favorite's displayed value comes from ``MarketScore`` at
        read time; an absent MarketScore is rendered as "no data yet"
        rather than blocked here, so a brand-new market can be
        favorited the instant the protocol is enabled.

        Args:
            market_config_id: UUID of the target protocol row.

        Raises:
            NotFoundError: If the row doesn't exist.
            RepositoryError: If the protocol exists but is disabled.
        """
        stmt = select(MarketConfig.enabled).where(MarketConfig.id == market_config_id)
        result = await self.repository.session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            msg = f"Market config {market_config_id} does not exist."
            raise NotFoundError(msg)
        (enabled,) = row
        if not enabled:
            msg = (
                "This protocol is disabled. Re-enable it from the admin "
                "panel before favoriting one of its markets."
            )
            raise RepositoryError(msg)
