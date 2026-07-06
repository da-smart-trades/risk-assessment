# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import logging
from functools import cache
from typing import Any

from litestar.serialization import decode_json, encode_json
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from cert_ra.settings.db import get_db_settings


def _clear_sqlalchemy_default_handlers() -> None:
    """Clear default StreamHandlers that SQLAlchemy adds to its loggers.

    SQLAlchemy adds a StreamHandler(sys.stdout) when echo=True on first log.
    We want to use only our structlog queue handlers, so we remove any
    plain StreamHandlers. This preserves our QueueHandler from structlog config.
    """
    for logger_name in (
        "sqlalchemy.engine.Engine",
        "sqlalchemy.engine",
        "sqlalchemy.pool",
    ):
        logger = logging.getLogger(logger_name)
        # Remove only basic StreamHandlers, not QueueHandlers or other custom handlers
        handlers_to_remove = [
            h for h in logger.handlers if type(h) is logging.StreamHandler
        ]
        for handler in handlers_to_remove:
            logger.removeHandler(handler)


@cache
def create_sqlalchemy_engine() -> AsyncEngine:
    """Create SQLAlchemy async engine based on database URL.

    Args:
        settings: Database settings containing URL and pool configuration.

    Returns:
        Configured AsyncEngine instance.
    """
    settings = get_db_settings()
    url = settings.url.replace("postgresql://", "postgresql+asyncpg://")
    # `ssl=False` disables TLS; an SSLContext requires it. Passing the
    # param unconditionally keeps asyncpg from silently negotiating
    # "prefer" when ssl_mode=disable.
    connect_args: dict[str, Any] = {"ssl": settings.build_ssl_param()}
    engine = create_async_engine(
        url=url,
        future=True,
        json_serializer=encode_json,
        json_deserializer=decode_json,
        echo=settings.echo,
        echo_pool=settings.echo_pool,
        max_overflow=settings.pool_max_overflow,
        pool_size=settings.pool_size,
        pool_timeout=settings.pool_timeout,
        pool_recycle=settings.pool_recycle,
        pool_pre_ping=settings.pool_pre_ping,
        pool_use_lifo=True,  # use lifo to reduce the number of idle connections
        poolclass=NullPool if settings.pool_disabled else None,
        connect_args=connect_args,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _sqla_on_connect(  # pragma: no cover # pyright: ignore[reportUnusedFunction]
        dbapi_connection: Any,  # noqa: ANN401
        _: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Custom handler for SQLAlchemy 'connect' event to set asyncpg type codecs for JSON/JSONB.

        Using msgspec for serialization of the json column values means that the
        output is binary, not `str` like `json.dumps` would output.
        SQLAlchemy expects that the json serializer returns `str` and calls `.encode()` on the value to
        turn it to bytes before writing to the JSONB column. I'd need to either wrap `serialization.to_json` to
        return a `str` so that SQLAlchemy could then convert it to binary, or do the following, which
        changes the behaviour of the dialect to expect a binary value from the serializer.
        See Also https://github.com/sqlalchemy/sqlalchemy/blob/14bfbadfdf9260a1c40f63b31641b27fe9de12a0/lib/sqlalchemy/dialects/postgresql/asyncpg.py#L934
        """

        def encoder(bin_value: bytes) -> bytes:
            # bin_value is already JSON-serialized by SQLAlchemy's json_serializer
            # Just add the JSONB binary prefix, don't re-encode
            return b"\x01" + bin_value

        def decoder(bin_value: bytes) -> Any:  # noqa: ANN401
            # the byte is the \x01 prefix for jsonb used by PostgreSQL.
            # asyncpg returns it when format='binary'
            return decode_json(bin_value[1:])

        dbapi_connection.await_(
            dbapi_connection.driver_connection.set_type_codec(
                "jsonb",
                encoder=encoder,
                decoder=decoder,
                schema="pg_catalog",
                format="binary",
            ),
        )
        dbapi_connection.await_(
            dbapi_connection.driver_connection.set_type_codec(
                "json",
                encoder=encoder,
                decoder=decoder,
                schema="pg_catalog",
                format="binary",
            ),
        )

    # Clear any handlers SQLAlchemy added (when echo=True) so only structlog handlers are used
    _clear_sqlalchemy_default_handlers()

    return engine
