# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from typing import Any

import msgspec


class BaseStruct(msgspec.Struct):
    def to_dict(self) -> dict[str, Any]:
        """Convert struct to dictionary.

        Returns:
            Dictionary representation of struct fields.
        """
        return {
            f: getattr(self, f)
            for f in self.__struct_fields__
            if getattr(self, f, None) != msgspec.UNSET
        }


class CamelizedBaseStruct(BaseStruct, rename="camel"):
    """Camelized Base Struct."""


class Message(CamelizedBaseStruct):
    message: str


class NoProps(CamelizedBaseStruct):
    """Empty page props for Inertia pages with no data."""


class VerifyEmailPage(CamelizedBaseStruct):
    """Page props for email verification page."""

    status: str | None = None


class AboutPage(CamelizedBaseStruct):
    """Page props for the about page."""

    commit_sha: str


class ChainListPage(CamelizedBaseStruct):
    """Page props for the chains list page."""

    chains: list[str]


class ChainShowPage(CamelizedBaseStruct):
    """Page props for an individual chain landing page."""

    chain: str


class TokenListPage(CamelizedBaseStruct):
    """Page props for the tokens list page."""

    tokens: list[str]


class TokenShowPage(CamelizedBaseStruct):
    """Page props for an individual token landing page."""

    token: str


class MarketListPage(CamelizedBaseStruct):
    """Page props for the markets list page."""

    markets: list[str]


class MarketShowPage(CamelizedBaseStruct):
    """Page props for an individual market landing page."""

    market: str


class ProtocolListPage(CamelizedBaseStruct):
    """Page props for the protocols list page."""

    protocols: list[str]


class ProtocolShowPage(CamelizedBaseStruct):
    """Page props for an individual protocol landing page."""

    protocol: str
