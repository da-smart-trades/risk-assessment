# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""Security report controllers — JSON API and Inertia page controllers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlsplit
from uuid import UUID

from advanced_alchemy.extensions.litestar.providers import (
    FilterConfig,
    create_service_dependencies,
)
from advanced_alchemy.filters import LimitOffset
from advanced_alchemy.service import (
    OffsetPagination as _OffsetPagination,
)
from advanced_alchemy.types import FileObject
from litestar import Controller, Request, delete, get, post
from litestar.datastructures import UploadFile  # noqa: TC002
from litestar.enums import RequestEncodingType
from litestar.exceptions import ValidationException
from litestar.params import Body, Dependency, Parameter
from litestar.response import Response
from litestar_vite.inertia import InertiaRedirect, flash
from uuid_utils import uuid7

from cert_ra.api.domain.accounts.guards import requires_active_user
from cert_ra.api.domain.security_reports.schemas import (
    SecurityReport,
    SecurityReportListPage,
)
from cert_ra.api.domain.security_reports.services import SecurityReportService
from cert_ra.api.domain.teams.guards import requires_operator_editor
from cert_ra.db.models import (
    SecurityReport as SecurityReportModel,  # noqa: TC001
    User,  # noqa: TC001
)
from cert_ra.settings.db import get_storage_settings

if TYPE_CHECKING:
    from advanced_alchemy.filters import FilterTypes
    from advanced_alchemy.service import OffsetPagination

__all__ = ("SecurityReportApiController", "SecurityReportPageController")

_BASE_FILTERS: FilterConfig = {
    "id_filter": UUID,
    "created_at": True,
    "updated_at": True,
    "sort_field": "created_at",
    "sort_order": "desc",
    "pagination_type": "limit_offset",
    "pagination_size": 50,
}


@dataclass
class ReportUploadForm:
    """Multipart form for uploading a security report."""

    name: str
    description: str
    file: UploadFile


def _to_response_schema(report: SecurityReportModel) -> SecurityReport:
    """Convert a model row to the response schema."""
    uploader = report.uploader
    uploader_name = uploader.name or uploader.email if uploader is not None else None
    return SecurityReport(
        id=report.id,
        name=report.name,
        description=report.description,
        file_url=f"/api/security-reports/{report.id}/download",
        uploaded_by=report.uploaded_by,
        uploader_name=uploader_name,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


async def _do_upload(
    service: SecurityReportService,
    current_user: User,
    name: str,
    description: str,
    upload_file: UploadFile,
) -> SecurityReportModel:
    """Validate, store, and persist a PDF upload.

    Args:
        service: SecurityReportService instance.
        current_user: Authenticated user performing the upload.
        name: Report name.
        description: Report description.
        upload_file: The uploaded file.

    Returns:
        Newly created SecurityReport model.

    Raises:
        ValidationException: If the file type or size is invalid.
    """
    storage_settings = get_storage_settings()
    content_type = upload_file.content_type or "application/octet-stream"

    if content_type not in storage_settings.allowed_report_types:
        msg = "Only PDF files are accepted."
        raise ValidationException(detail=msg)

    content = await upload_file.read()

    if len(content) > storage_settings.max_report_size:
        max_mb = storage_settings.max_report_size // (1024 * 1024)
        msg = f"File too large. Maximum size: {max_mb} MB."
        raise ValidationException(detail=msg)

    filename = f"reports/{uuid7()}.pdf"
    file_obj = FileObject(
        backend="reports",
        filename=filename,
        content_type="application/pdf",
        content=content,
    )
    # Persist the bytes to storage explicitly. The advanced-alchemy Litestar
    # plugin (1.9.x) overrides ``create_session_maker`` without wiring up the
    # FileObject save listener, so relying on save-on-commit silently drops
    # the file and every later download fails with FileNotFound.
    await file_obj.save_async()

    return await service.create(
        {
            "name": name,
            "description": description,
            "file": file_obj,
            "uploaded_by": current_user.id,
        }
    )


class SecurityReportApiController(Controller):
    """Security report JSON API."""

    path = "/api/security-reports"
    tags = ["Security Reports"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        SecurityReportService,
        key="security_report_service",
        filters=_BASE_FILTERS,
    )
    signature_namespace = {  # noqa: RUF012
        "SecurityReportService": SecurityReportService,
    }

    @get(
        operation_id="ListSecurityReports",
        name="security_reports:list",
        summary="List security reports",
        path="/",
    )
    async def list_security_reports(
        self,
        security_report_service: SecurityReportService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> OffsetPagination[SecurityReport]:
        """List all security reports, newest first.

        Returns:
            Paginated list of security reports.
        """
        results, total = await security_report_service.list_and_count(*filters)
        lo = next((f for f in filters if isinstance(f, LimitOffset)), None)
        return _OffsetPagination(
            items=[_to_response_schema(r) for r in results],
            total=total,
            limit=lo.limit if lo else 50,
            offset=lo.offset if lo else 0,
        )

    @get(
        operation_id="GetSecurityReport",
        name="security_reports:get",
        summary="Get a security report",
        path="/{report_id:uuid}",
    )
    async def get_security_report(
        self,
        security_report_service: SecurityReportService,
        report_id: Annotated[
            UUID,
            Parameter(
                title="Report ID", description="The security report to retrieve."
            ),
        ],
    ) -> SecurityReport:
        """Get one security report by ID.

        Returns:
            The requested security report.
        """
        db_obj = await security_report_service.get(report_id)
        return _to_response_schema(db_obj)

    @post(
        operation_id="UploadSecurityReport",
        name="security_reports:upload",
        summary="Upload a security report",
        guards=[requires_operator_editor],
        path="/",
        status_code=201,
    )
    async def upload_security_report(
        self,
        security_report_service: SecurityReportService,
        current_user: User,
        data: Annotated[
            ReportUploadForm, Body(media_type=RequestEncodingType.MULTI_PART)
        ],
    ) -> SecurityReport:
        """Upload a new PDF security report.

        Returns:
            The newly created security report.
        """
        db_obj = await _do_upload(
            security_report_service,
            current_user,
            name=data.name,
            description=data.description,
            upload_file=data.file,
        )
        return _to_response_schema(db_obj)

    @get(
        operation_id="DownloadSecurityReport",
        name="security_reports:download",
        summary="Download a security report PDF",
        path="/{report_id:uuid}/download",
        media_type="application/pdf",
    )
    async def download_security_report(
        self,
        security_report_service: SecurityReportService,
        report_id: Annotated[
            UUID,
            Parameter(
                title="Report ID", description="The security report to download."
            ),
        ],
    ) -> Response[bytes]:
        """Stream the PDF for a security report.

        Returns:
            Raw PDF bytes with appropriate Content-Disposition header.
        """
        db_obj = await security_report_service.get(report_id)
        content = await db_obj.file.get_content_async()
        safe_name = db_obj.name.replace('"', "").replace("\\", "")
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}.pdf"',
                "Content-Length": str(len(content)),
            },
        )


class SecurityReportPageController(Controller):
    """Security report Inertia pages."""

    tags = ["Security Reports"]  # noqa: RUF012
    guards = [requires_active_user]  # noqa: RUF012
    dependencies = create_service_dependencies(
        SecurityReportService,
        key="security_report_service",
        filters=_BASE_FILTERS,
    )
    signature_namespace = {  # noqa: RUF012
        "SecurityReportService": SecurityReportService,
    }

    @get(
        component="security-reports/list",
        name="security_reports.list",
        operation_id="SecurityReportsListPage",
        path="/security-reports",
    )
    async def list_page(
        self,
        security_report_service: SecurityReportService,
        current_user: User,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> SecurityReportListPage:
        """Public list page — all authenticated users.

        Returns:
            Page props with reports list and operator editor flag.
        """
        results, total = await security_report_service.list_and_count(*filters)
        return SecurityReportListPage(
            items=[_to_response_schema(r) for r in results],
            total=total,
            is_operator_editor=current_user.is_operator_editor,
        )

    @get(
        component="security-reports/admin/list",
        name="security_reports.admin.list",
        operation_id="SecurityReportsAdminListPage",
        guards=[requires_operator_editor],
        path="/security-reports/admin",
    )
    async def admin_list_page(
        self,
        security_report_service: SecurityReportService,
        filters: Annotated[list[FilterTypes], Dependency(skip_validation=True)],
    ) -> SecurityReportListPage:
        """Operator admin list — upload and delete actions.

        Returns:
            Page props with flat items list.
        """
        results, total = await security_report_service.list_and_count(*filters)
        return SecurityReportListPage(
            items=[_to_response_schema(r) for r in results],
            total=total,
            is_operator_editor=True,
        )

    @get(
        component="security-reports/admin/upload",
        name="security_reports.admin.upload_page",
        operation_id="SecurityReportsAdminUploadPage",
        guards=[requires_operator_editor],
        path="/security-reports/admin/upload",
    )
    async def admin_upload_page(self) -> SecurityReportListPage:
        """Show the upload-report page.

        Returns:
            Empty page props for the upload form.
        """
        return SecurityReportListPage(items=[], total=0, is_operator_editor=True)

    @post(
        name="security_reports.admin.upload",
        operation_id="SecurityReportsAdminUpload",
        guards=[requires_operator_editor],
        path="/security-reports/admin",
        status_code=303,
    )
    async def admin_upload(
        self,
        request: Request,
        security_report_service: SecurityReportService,
        current_user: User,
        data: Annotated[
            ReportUploadForm, Body(media_type=RequestEncodingType.MULTI_PART)
        ],
    ) -> InertiaRedirect:
        """Upload a new security report (Inertia form submit).

        Returns:
            Redirect to admin list with flash confirmation.
        """
        db_obj = await _do_upload(
            security_report_service,
            current_user,
            name=data.name,
            description=data.description,
            upload_file=data.file,
        )
        flash(
            request,
            f'Uploaded security report "{db_obj.name}".',
            category="info",
        )
        return InertiaRedirect(request, request.url_for("security_reports.admin.list"))

    @delete(
        name="security_reports.admin.delete",
        operation_id="SecurityReportsAdminDelete",
        guards=[requires_operator_editor],
        path="/security-reports/admin/{report_id:uuid}",
        status_code=303,
    )
    async def admin_delete(
        self,
        request: Request,
        security_report_service: SecurityReportService,
        report_id: Annotated[
            UUID,
            Parameter(title="Report ID", description="The security report to delete."),
        ],
    ) -> InertiaRedirect:
        """Delete a security report record (file left in storage).

        Operators can delete from either the public reports list or the
        operator admin list, so return them to whichever page they were on.

        Returns:
            Redirect to the originating list with flash confirmation.
        """
        db_obj = await security_report_service.delete(report_id)
        flash(
            request,
            f'Deleted security report "{db_obj.name}".',
            category="info",
        )
        referer_path = urlsplit(request.headers.get("referer", "")).path
        target = (
            "security_reports.list"
            if referer_path.rstrip("/") == "/security-reports"
            else "security_reports.admin.list"
        )
        return InertiaRedirect(request, request.url_for(target))
