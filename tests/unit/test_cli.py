# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

import pytest
from click.testing import CliRunner


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()
