# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from advanced_alchemy.types.file_object import storages
from advanced_alchemy.types.file_object.backends.obstore import ObstoreBackend

from cert_ra.api.domain.accounts.services import UserService
from cert_ra.api.domain.teams.services import TeamMemberService, TeamService
from cert_ra.db.models import SecurityReport, TeamRoles

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _register_storage_backends(tmp_path: Path) -> None:
    """Register avatars and reports storage backends backed by a temp directory."""
    for key in ("avatars", "reports"):
        if not storages.is_registered(key):
            storages.register_backend(ObstoreBackend(key=key, fs=f"file://{tmp_path}/"))


USER_ID = UUID("5ef29f3c-3560-4d15-ba6b-a2e5c721e4d2")
SUPERUSER_ID = UUID("97108ac1-ffcb-411d-8b1e-d9183399f63b")

_MINIMAL_PDF = b"%PDF-1.4 1 0 obj<</Type/Catalog>>endobj\n%%EOF"


async def _add_user_to_operator_team(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    member_email: str,
    role: TeamRoles = TeamRoles.MEMBER,
) -> None:
    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        await team_service.ensure_operator_team(name="Op", domain="example.com")

    async with sessionmaker() as session:
        team_service = TeamService(session=session)
        user_service = UserService(session=session)
        member_service = TeamMemberService(session=session)
        operator_team = await team_service.get_one(is_operator=True)
        member_user = await user_service.get_one(email=member_email)
        await member_service.create(
            {
                "team_id": operator_team.id,
                "user_id": member_user.id,
                "role": role,
                "is_owner": False,
            }
        )
        await member_service.repository.session.commit()


async def _seed_report(
    sessionmaker: async_sessionmaker[AsyncSession],
    **overrides: Any,  # noqa: ANN401
) -> SecurityReport:
    """Insert one security report directly into the DB."""
    from advanced_alchemy.types import FileObject

    file_obj = FileObject(
        backend="reports",
        filename="reports/test-report.pdf",
        content_type="application/pdf",
        size=len(b"%PDF-1.4 test"),
    )

    payload: dict[str, Any] = {
        "name": "Test Report",
        "description": "A test security report",
        "file": file_obj,
        "uploaded_by": USER_ID,
    }
    payload.update(overrides)
    async with sessionmaker() as session:
        report = SecurityReport(**payload)
        session.add(report)
        await session.commit()
        await session.refresh(report)
        return report


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


async def test_list_reports_anonymous_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/security-reports")
    assert response.status_code in (401, 403)


async def test_list_reports_authenticated(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_report(sessionmaker, name="Alpha")
    await _seed_report(sessionmaker, name="Beta")

    response = await client.get("/api/security-reports", headers=user_token_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    names = {item["name"] for item in body["items"]}
    assert names == {"Alpha", "Beta"}


async def test_get_report_authenticated(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    report = await _seed_report(sessionmaker, name="Fetch Me")

    response = await client.get(
        f"/api/security-reports/{report.id}", headers=user_token_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Fetch Me"
    assert body["fileUrl"] == f"/api/security-reports/{report.id}/download"


async def test_get_report_anonymous_rejected(client: AsyncClient) -> None:
    response = await client.get(
        "/api/security-reports/00000000-0000-0000-0000-000000000001"
    )
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Upload — access control
# ---------------------------------------------------------------------------


async def test_upload_anonymous_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/security-reports",
        files={"file": ("report.pdf", BytesIO(_MINIMAL_PDF), "application/pdf")},
        data={"name": "X", "description": "Y"},
    )
    assert response.status_code in (401, 403)


async def test_upload_plain_user_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
) -> None:
    headers = {**user_token_headers, "Content-Type": None}  # let httpx set multipart
    headers.pop("Content-Type", None)
    response = await client.post(
        "/api/security-reports",
        files={"file": ("report.pdf", BytesIO(_MINIMAL_PDF), "application/pdf")},
        data={"name": "X", "description": "Y"},
        headers={k: v for k, v in user_token_headers.items() if k != "Content-Type"},
    )
    assert response.status_code == 403


async def test_upload_operator_member_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.MEMBER
    )
    response = await client.post(
        "/api/security-reports",
        files={"file": ("report.pdf", BytesIO(_MINIMAL_PDF), "application/pdf")},
        data={"name": "X", "description": "Y"},
        headers={k: v for k, v in user_token_headers.items() if k != "Content-Type"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Upload — validation
# ---------------------------------------------------------------------------


async def test_upload_non_pdf_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    response = await client.post(
        "/api/security-reports",
        files={"file": ("image.png", BytesIO(b"PNG..."), "image/png")},
        data={"name": "X", "description": "Y"},
        headers={k: v for k, v in user_token_headers.items() if k != "Content-Type"},
    )
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


async def test_download_anonymous_rejected(
    client: AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    report = await _seed_report(sessionmaker)
    response = await client.get(f"/api/security-reports/{report.id}/download")
    assert response.status_code in (401, 403)


async def test_upload_persists_bytes_so_download_round_trips(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Upload must write bytes to storage so the download can read them back.

    Regression: the advanced-alchemy Litestar plugin overrides
    ``create_session_maker`` without wiring up the FileObject save-on-commit
    listener, so ``_do_upload`` must persist the bytes explicitly. Without that,
    the DB row is created but storage stays empty and every download fails with
    ``FileNotFound``. Exercised at the handler/service level because the MFA
    enrollment trap 303s authenticated HTTP API requests in the test harness.
    """
    from litestar.datastructures import UploadFile

    from cert_ra.api.domain.security_reports.controllers import _do_upload
    from cert_ra.api.domain.security_reports.services import SecurityReportService
    from cert_ra.db.models import User

    async with sessionmaker() as session:
        user = await session.get(User, USER_ID)
        assert user is not None
        service = SecurityReportService(session=session)
        upload = UploadFile(content_type="application/pdf", filename="r.pdf")
        await upload.write(_MINIMAL_PDF)
        await upload.seek(0)
        report = await _do_upload(
            service, user, name="RoundTrip", description="d", upload_file=upload
        )
        await service.repository.session.commit()
        report_id = report.id
        # Bytes were actually persisted (listener-independent): size is populated.
        assert report.file.size == len(_MINIMAL_PDF)

    # The download handler reads the bytes straight back from storage.
    async with sessionmaker() as session:
        service = SecurityReportService(session=session)
        db_obj = await service.get(report_id)
        content = await db_obj.file.get_content_async()
        assert content == _MINIMAL_PDF


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_plain_user_rejected(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    report = await _seed_report(sessionmaker, name="Protected")
    response = await client.delete(
        f"/security-reports/admin/{report.id}",
        headers=user_token_headers,
    )
    assert response.status_code in (307, 403)


async def test_delete_editor_succeeds(
    client: AsyncClient,
    user_token_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    report = await _seed_report(sessionmaker, name="ToDelete")

    response = await client.delete(
        f"/security-reports/admin/{report.id}",
        headers={k: v for k, v in user_token_headers.items() if k != "Content-Type"},
        follow_redirects=False,
    )
    # 303 redirect to admin list
    assert response.status_code == 303

    # Verify record is gone
    fetch = await client.get(
        f"/api/security-reports/{report.id}", headers=user_token_headers
    )
    assert fetch.status_code != 200


# ---------------------------------------------------------------------------
# Inertia pages
# ---------------------------------------------------------------------------


async def test_inertia_list_page_authenticated(
    client: AsyncClient,
    user_inertia_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_report(sessionmaker, name="Visible")

    response = await client.get("/security-reports", headers=user_inertia_headers)
    assert response.status_code == 200
    content = response.json()["props"]["content"]
    assert content["total"] >= 1
    assert content["isOperatorEditor"] is False


async def test_inertia_admin_list_requires_editor(
    client: AsyncClient,
    user_token_headers: dict[str, str],
) -> None:
    response = await client.get("/security-reports/admin", headers=user_token_headers)
    assert response.status_code in (307, 403)


async def test_inertia_admin_list_succeeds_for_editor(
    client: AsyncClient,
    user_inertia_headers: dict[str, str],
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _add_user_to_operator_team(
        sessionmaker, member_email="user@example.com", role=TeamRoles.EDITOR
    )
    await _seed_report(sessionmaker, name="Admin Visible")

    response = await client.get("/security-reports/admin", headers=user_inertia_headers)
    assert response.status_code == 200
    content = response.json()["props"]["content"]
    assert content["total"] >= 1
    assert content["isOperatorEditor"] is True
