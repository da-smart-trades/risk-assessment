# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

pytestmark = pytest.mark.anyio

_USER_ID = UUID("5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2")


def _make_service() -> object:
    """Return a SecurityReportService with a mocked session."""
    from cert_ra.api.domain.security_reports.services import SecurityReportService

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    return SecurityReportService(session=mock_session)


async def test_to_model_on_create_requires_uploaded_by() -> None:
    """to_model_on_create raises ValueError when uploaded_by is missing."""
    from cert_ra.api.domain.security_reports.services import SecurityReportService

    service = _make_service()
    assert isinstance(service, SecurityReportService)
    with pytest.raises(ValueError, match="uploaded_by"):
        await service.to_model_on_create({"name": "Report", "description": "Desc"})


async def test_to_model_on_create_passes_with_uploaded_by() -> None:
    """to_model_on_create returns the payload with uploaded_by present."""
    from cert_ra.api.domain.security_reports.services import SecurityReportService

    service = _make_service()
    assert isinstance(service, SecurityReportService)
    payload = {
        "name": "Report",
        "description": "Desc",
        "uploaded_by": _USER_ID,
        "file": MagicMock(),
    }
    result = await service.to_model_on_create(payload)
    assert isinstance(result, dict)
    assert result["uploaded_by"] == _USER_ID
    assert result["name"] == "Report"


async def test_do_upload_rejects_non_pdf() -> None:
    """_do_upload raises ValidationException for non-PDF content types."""
    from litestar.exceptions import ValidationException

    from cert_ra.api.domain.security_reports.controllers import _do_upload

    mock_service = MagicMock()
    mock_user = MagicMock()
    mock_user.id = _USER_ID

    mock_file = MagicMock()
    mock_file.content_type = "image/jpeg"
    mock_file.read = AsyncMock(return_value=b"not a pdf")

    with pytest.raises(ValidationException, match="PDF"):
        await _do_upload(mock_service, mock_user, "Report", "Desc", mock_file)


async def test_do_upload_rejects_oversized_file() -> None:
    """_do_upload raises ValidationException when file exceeds max_report_size."""
    from litestar.exceptions import ValidationException

    from cert_ra.api.domain.security_reports.controllers import _do_upload
    from cert_ra.settings.db import get_storage_settings

    mock_service = MagicMock()
    mock_user = MagicMock()
    mock_user.id = _USER_ID

    oversized = b"x" * (get_storage_settings().max_report_size + 1)
    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.read = AsyncMock(return_value=oversized)

    with pytest.raises(ValidationException, match="large"):
        await _do_upload(mock_service, mock_user, "Report", "Desc", mock_file)


async def test_do_upload_creates_record() -> None:
    """_do_upload creates a SecurityReport with correct fields."""
    from cert_ra.api.domain.security_reports.controllers import _do_upload

    mock_service = MagicMock()
    mock_service.create = AsyncMock(return_value=MagicMock())
    mock_user = MagicMock()
    mock_user.id = _USER_ID

    mock_file = MagicMock()
    mock_file.content_type = "application/pdf"
    mock_file.filename = "report.pdf"
    mock_file.read = AsyncMock(return_value=b"%PDF-1.4 test content")

    with patch(
        "cert_ra.api.domain.security_reports.controllers.uuid7",
        return_value="test-uuid",
    ):
        await _do_upload(
            mock_service, mock_user, "My Report", "A description", mock_file
        )

    mock_service.create.assert_awaited_once()
    payload = mock_service.create.call_args[0][0]
    assert payload["name"] == "My Report"
    assert payload["description"] == "A description"
    assert payload["uploaded_by"] == _USER_ID
    assert payload["file"]._raw_backend == "reports"  # noqa: SLF001
