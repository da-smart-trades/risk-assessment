# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

try:
    from ._version import __version__, __version_tuple__, version, version_tuple
except ImportError:
    __version__ = "0.0.0"
    __version_tuple__ = (0, 0, 0)
    version = __version__
    version_tuple = __version_tuple__

__all__ = [
    "__version__",
    "__version_tuple__",
    "version",
    "version_tuple",
]
