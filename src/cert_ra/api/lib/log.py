# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

import logging
import re
import sys
from inspect import isawaitable
from typing import TYPE_CHECKING

import structlog
from litestar.data_extractors import ConnectionDataExtractor, ResponseDataExtractor
from litestar.enums import ScopeType
from litestar.exceptions import (
    HTTPException,
    NotAuthorizedException,
    NotFoundException,
    PermissionDeniedException,
)
from litestar.logging.config import (
    LoggingConfig,
    StructLoggingConfig,
    default_logger_factory,
)
from litestar.middleware.logging import LoggingMiddlewareConfig
from litestar.plugins.structlog import StructlogConfig
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR
from litestar.utils.empty import value_or_default
from litestar.utils.scope.state import ScopeState
from structlog.contextvars import bind_contextvars

from cert_ra.log import (  # noqa: F401
    EventFilter,
    is_tty,
    stdlib_json_serializer,
    stdlib_logger_processors,
    structlog_json_serializer,
    structlog_processors,
)
from cert_ra.settings.api import get_log_settings

from .exceptions import ApplicationError

_log_settings = get_log_settings()

if TYPE_CHECKING:
    from typing import Any, Literal

    from litestar.connection import Request
    from litestar.types.asgi_types import ASGIApp, Message, Receive, Scope, Send

LOGGER = structlog.getLogger()

HTTP_RESPONSE_START: Literal["http.response.start"] = "http.response.start"
HTTP_RESPONSE_BODY: Literal["http.response.body"] = "http.response.body"
REQUEST_BODY_FIELD: Literal["body"] = "body"


# This is so that it shows up properly in the litestar ui.  instead of reading `middleware_factory`, we use something that make sense.
def StructlogMiddleware(app: ASGIApp) -> ASGIApp:  # noqa: N802
    """Middleware to ensure that every request has a clean structlog context.

    Args:
        app: The previous ASGI app in the call chain.

    Returns:
        A new ASGI app that cleans the structlog contextvars.
    """

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        """Clean up structlog contextvars.

        Args:
            scope: ASGI connection scope.
            receive: ASGI receive handler.
            send: ASGI send handler.
        """
        structlog.contextvars.clear_contextvars()
        await app(scope, receive, send)

    return middleware


async def after_exception_hook_handler(exc: Exception, _scope: Scope) -> None:
    """Binds `exc_info` key with exception instance as value to structlog context vars.

    This must be a coroutine so that it is not wrapped in a thread where we'll lose context.

    Args:
        exc: the exception that was raised.
        _scope: scope of the request
    """
    if isinstance(exc, ApplicationError):
        return
    if (
        isinstance(exc, HTTPException)
        and exc.status_code < HTTP_500_INTERNAL_SERVER_ERROR
    ):
        return
    bind_contextvars(exc_info=sys.exc_info())


class BeforeSendHandler:
    """Extraction of request and response data from connection scope."""

    __slots__ = (
        "do_log_request",
        "do_log_response",
        "exclude_paths",
        "include_compressed_body",
        "logger",
        "request_extractor",
        "response_extractor",
    )

    def __init__(self) -> None:
        """Configure the handler."""
        self.exclude_paths = re.compile(_log_settings.exclude_paths)
        self.do_log_request = bool(_log_settings.request_fields)
        self.do_log_response = bool(_log_settings.response_fields)
        self.include_compressed_body = _log_settings.include_compressed_body
        self.request_extractor = ConnectionDataExtractor(
            extract_body="body" in _log_settings.request_fields,
            extract_client="client" in _log_settings.request_fields,
            extract_content_type="content_type" in _log_settings.request_fields,
            extract_cookies="cookies" in _log_settings.request_fields,
            extract_headers="headers" in _log_settings.request_fields,
            extract_method="method" in _log_settings.request_fields,
            extract_path="path" in _log_settings.request_fields,
            extract_path_params="path_params" in _log_settings.request_fields,
            extract_query="query" in _log_settings.request_fields,
            extract_scheme="scheme" in _log_settings.request_fields,
            obfuscate_cookies=_log_settings.obfuscate_cookies,
            obfuscate_headers=_log_settings.obfuscate_headers,
            parse_body=False,
            parse_query=False,
        )
        self.response_extractor = ResponseDataExtractor(
            extract_body="body" in _log_settings.response_fields,
            extract_headers="headers" in _log_settings.response_fields,
            extract_status_code="status_code" in _log_settings.response_fields,
            obfuscate_cookies=_log_settings.obfuscate_cookies,
            obfuscate_headers=_log_settings.obfuscate_headers,
        )

    async def __call__(self, message: Message, scope: Scope) -> None:
        """Receives ASGI response messages and scope, and logs per configuration.

        Args:
            message: ASGI response event.
            scope: ASGI connection scope.
        """
        if scope["type"] == ScopeType.HTTP and self.exclude_paths.findall(
            scope["path"]
        ):
            return

        if message["type"] == HTTP_RESPONSE_START:
            scope["state"]["log_level"] = (
                logging.ERROR
                if message["status"] >= HTTP_500_INTERNAL_SERVER_ERROR
                else logging.INFO
            )
            scope["state"][HTTP_RESPONSE_START] = message
        # ignore intermediate content of streaming responses for now.
        elif message["type"] == HTTP_RESPONSE_BODY and message["more_body"] is False:
            scope["state"][HTTP_RESPONSE_BODY] = message
            try:
                if self.do_log_request:
                    await self.log_request(scope)
                if self.do_log_response:
                    await self.log_response(scope)
                await LOGGER.alog(
                    scope["state"]["log_level"],
                    f"{scope['method'] if scope['type'] == ScopeType.HTTP else scope['type']} {scope['path']}",
                )
            # RuntimeError: Expected ASGI message 'http.response.body', but got 'http.response.start'.
            except Exception as e:  # noqa: BLE001  # pylint: disable=broad-except
                # just in-case something in the context causes the error
                structlog.contextvars.clear_contextvars()
                await LOGGER.aerror(
                    "Error in logging before-send handler!",
                    reason=f"{type(e).__name__}{e.args}",
                )

    async def log_request(self, scope: Scope) -> None:
        """Handle extracting the request data and logging the message.

        Args:
            scope: The ASGI connection scope.
        """
        extracted_data = await self.extract_request_data(
            request=scope["app"].request_class(scope)
        )
        structlog.contextvars.bind_contextvars(**extracted_data)

    async def log_response(self, scope: Scope) -> None:
        """Handle extracting the response data and logging the message.

        Args:
            scope: The ASGI connection scope.
        """
        extracted_data = self.extract_response_data(scope=scope)
        structlog.contextvars.bind_contextvars(**extracted_data)

    async def extract_request_data(
        self, request: Request[Any, Any, Any]
    ) -> dict[str, Any]:
        """Create a dictionary of values for the log.

        Args:
            request: A [Request][litestar.connection.request.Request] instance.

        Raises:
            RuntimeError: If an error occurs while reading non-body request fields.

        Returns:
            An OrderedDict.
        """
        data: dict[str, Any] = {}
        extracted_data = self.request_extractor(connection=request)
        missing = object()
        for key in _log_settings.request_fields:
            value = extracted_data.get(key, missing)
            if value is missing:  # pragma: no cover
                continue
            if isawaitable(value):
                # Prevent Litestar from raising a RuntimeError
                # when trying to read an empty request body.
                try:
                    value = await value
                except RuntimeError:
                    if key != REQUEST_BODY_FIELD:
                        raise  # pragma: no cover
                    value = None
            data[key] = value
        return data

    def extract_response_data(self, scope: Scope) -> dict[str, Any]:
        """Extract data from the response.

        Args:
            scope: The ASGI connection scope.

        Returns:
            An OrderedDict.
        """
        data: dict[str, Any] = {}
        extracted_data = self.response_extractor(
            messages=(
                scope["state"][HTTP_RESPONSE_START],
                scope["state"][HTTP_RESPONSE_BODY],
            ),
        )
        missing = object()
        connection_state = ScopeState.from_scope(scope)
        response_body_compressed = value_or_default(
            connection_state.response_compressed,
            False,  # noqa: FBT003
        )
        for key in _log_settings.response_fields:
            value = extracted_data.get(key, missing)
            if (
                key == "body"
                and response_body_compressed
                and not self.include_compressed_body
            ):
                continue
            if value is missing:  # pragma: no cover
                continue
            data[key] = value
        return data


def get_log_config() -> StructlogConfig:
    """Create the complete Litestar StructlogConfig from log settings."""
    log = get_log_settings()
    as_json = not is_tty()
    disable_stack_trace: set[Any] = {
        404,
        401,
        403,
        NotFoundException,
        NotAuthorizedException,
        PermissionDeniedException,
    }
    return StructlogConfig(
        enable_middleware_logging=False,
        structlog_logging_config=StructLoggingConfig(
            log_exceptions="always",
            processors=structlog_processors(as_json=as_json),
            logger_factory=default_logger_factory(as_json=as_json),
            disable_stack_trace=disable_stack_trace,
            standard_lib_logging_config=LoggingConfig(
                log_exceptions="always",
                disable_existing_loggers=True,
                disable_stack_trace=disable_stack_trace,
                root={
                    "level": logging.getLevelName(log.level),
                    "handlers": ["queue_listener"],
                },
                formatters={
                    "standard": {
                        "()": structlog.stdlib.ProcessorFormatter,
                        "processors": stdlib_logger_processors(as_json=as_json),
                    },
                },
                loggers={
                    "sqlalchemy.engine": {
                        "propagate": False,
                        "level": log.sqlalchemy_level,
                        "handlers": ["queue_listener"],
                    },
                    "sqlalchemy.engine.Engine": {
                        "propagate": False,
                        "level": log.sqlalchemy_level,
                        "handlers": ["queue_listener"],
                    },
                    "sqlalchemy.pool": {
                        "propagate": False,
                        "level": log.sqlalchemy_level,
                        "handlers": ["queue_listener"],
                    },
                    "urllib3": {
                        "propagate": False,
                        "level": log.sqlalchemy_level,
                        "handlers": ["queue_listener"],
                    },
                    "_granian": {
                        "propagate": False,
                        "level": log.granian_error_level,
                        "handlers": ["queue_listener"],
                    },
                    "granian.server": {
                        "propagate": False,
                        "level": log.granian_error_level,
                        "handlers": ["queue_listener"],
                    },
                    "granian.access": {
                        "propagate": False,
                        "level": log.granian_access_level,
                        "handlers": ["queue_listener"],
                    },
                    "uvicorn.error": {
                        "propagate": False,
                        "level": log.uvicorn_error_level,
                        "handlers": ["queue_listener"],
                    },
                    "uvicorn.access": {
                        "propagate": False,
                        "level": log.uvicorn_access_level,
                        "handlers": ["queue_listener"],
                    },
                    "httpx": {
                        "propagate": False,
                        "level": logging.WARNING,
                        "handlers": ["queue_listener"],
                    },
                    "httpcore": {
                        "propagate": False,
                        "level": logging.WARNING,
                        "handlers": ["queue_listener"],
                    },
                },
            ),
        ),
        middleware_logging_config=LoggingMiddlewareConfig(
            request_log_fields=log.request_fields,
            response_log_fields=log.response_fields,
        ),
    )
