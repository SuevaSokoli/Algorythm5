from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import UUID4

from algorythm5.services import AppError
from algorythm5.services.jobs import JobService
from algorythm5.web.schemas import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ChangePageOut,
    ColumnOut,
    JobCreatedOut,
    JobOut,
    MappingIn,
    MappingOut,
    OutputKind,
    ReviewPageOut,
    ReviewSeverity,
    RunOut,
    SummaryOut,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def get_service(request: Request) -> JobService:
    service: JobService = request.app.state.job_service
    return service


Service = Annotated[JobService, Depends(get_service)]
Page = Annotated[int, Query(ge=1, le=100_000)]
PageSize = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
RuleCode = Annotated[str | None, Query(pattern=r"^[A-Z]{3}-\d{3}$")]
FieldName = Annotated[str | None, Query(max_length=200)]


@router.post("", status_code=201)
def create_job(
    request: Request,
    service: Service,
    files: Annotated[list[UploadFile] | None, File()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> JobCreatedOut:
    """Upload one file (`file`) or several (`files`); they become one job."""
    settings = request.app.state.settings
    limit: int = settings.max_upload_bytes
    chosen = [*(files or []), *([file] if file else [])]
    if len(chosen) > settings.max_files:
        message = f"Please upload at most {settings.max_files} files at once."
        raise AppError("TOO_MANY_FILES", message, 413)
    uploads: list[tuple[str, bytes]] = []
    for upload in chosen:
        data = upload.file.read(limit + 1)
        name = upload.filename or ""
        if len(data) > limit:
            message = "The file is too large (size, rows or columns)."
            raise AppError("ING-902", f"{name}: {message}" if name else message, 413)
        uploads.append((name, data))
    return JobCreatedOut.model_validate(service.create_job(uploads))


@router.get("/{job_id}")
def get_job(job_id: UUID4, service: Service) -> JobOut:
    return JobOut.model_validate(service.get_job(str(job_id)))


@router.put("/{job_id}/mapping")
def update_mapping(job_id: UUID4, body: MappingIn, service: Service) -> MappingOut:
    targets = {(c.file_index, c.index): c.target_field for c in body.columns}
    rows = service.update_mapping(str(job_id), targets)
    return MappingOut(
        columns=[
            ColumnOut(
                file_index=r.file_index,
                index=r.source_index,
                header=r.source_header,
                target_field=r.target_field,
                method=r.method,
                confidence=r.confidence,
                reason=r.reason,
            )
            for r in rows
        ]
    )


@router.post("/{job_id}/run", status_code=202)
def run_job(job_id: UUID4, service: Service, background: BackgroundTasks) -> RunOut:
    service.start_run(str(job_id))
    background.add_task(service.execute_run, str(job_id))
    return RunOut(status="processing")


@router.get("/{job_id}/summary")
def get_summary(job_id: UUID4, service: Service) -> SummaryOut:
    return SummaryOut.model_validate(service.summary(str(job_id)))


@router.get("/{job_id}/changes")
def get_changes(
    job_id: UUID4,
    service: Service,
    page: Page = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    rule_code: RuleCode = None,
    field: FieldName = None,
) -> ChangePageOut:
    result = service.changes(str(job_id), page, page_size, rule_code, field)
    return ChangePageOut.model_validate(result)


@router.get("/{job_id}/review")
def get_review(
    job_id: UUID4,
    service: Service,
    page: Page = 1,
    page_size: PageSize = DEFAULT_PAGE_SIZE,
    severity: ReviewSeverity | None = None,
    rule_code: RuleCode = None,
) -> ReviewPageOut:
    level = severity.value if severity else None
    result = service.review(str(job_id), page, page_size, level, rule_code)
    return ReviewPageOut.model_validate(result)


@router.get("/{job_id}/files/{kind}")
def download_file(job_id: UUID4, kind: OutputKind, service: Service) -> FileResponse:
    download = service.download(str(job_id), kind.value)
    return FileResponse(
        download.path, media_type=download.media_type, filename=download.download_name
    )


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: UUID4, service: Service) -> Response:
    service.delete_job(str(job_id))
    return Response(status_code=204)
