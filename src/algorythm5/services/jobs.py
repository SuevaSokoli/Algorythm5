"""Job use cases: upload -> confirm mapping -> run -> results/downloads -> delete."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from algorythm5.cleaning import (
    CANONICAL_FIELDS,
    RULESET_VERSION,
    ColumnMapping,
    InvalidMappingError,
    PipelineError,
    PipelineResult,
    PipelineSettings,
    SourceFile,
    Table,
    build_outputs,
    find_duplicate_sources,
    get_rule,
    has_contact_field,
    ingest,
    propose_mapping,
    run_sources,
    validate_targets,
)
from algorythm5.cleaning.export import format_source_rows
from algorythm5.cleaning.ingestion import detect_kind
from algorythm5.cleaning.status import CLEAN_FILE_STATUSES
from algorythm5.config import Settings
from algorythm5.db.connection import Database
from algorythm5.db.repositories import (
    ChangeRepository,
    ChangeRow,
    IssueRepository,
    IssueRow,
    JobFileRepository,
    JobFileRow,
    JobRepository,
    JobRow,
    MappingRepository,
    MappingRow,
    OutputFileRepository,
    OutputFileRow,
    RecordRepository,
    RecordRow,
)
from algorythm5.services import AppError
from algorythm5.storage.files import FileStorage

logger = logging.getLogger(__name__)

PREVIEW_ROWS = 10
SAMPLE_VALUES = 3
TOP_RULES = 10
MAX_STEM_CHARS = 60
MAX_FILENAME_CHARS = 255
LIST_SEPARATOR = "; "
OUTPUT_FILES = {
    # kind: (extension, download suffix, media type)
    "report_xlsx": (
        "xlsx",
        "report.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    "clean_csv": ("csv", "clean.csv", "text/csv; charset=utf-8"),
    "changes_csv": ("csv", "changes.csv", "text/csv; charset=utf-8"),
    "review_csv": ("csv", "review.csv", "text/csv; charset=utf-8"),
}
# Settings that change cleaning behaviour; snapshotted per job.
SNAPSHOT_SETTINGS = (
    "default_phone_region", "unknown_consent_policy", "required_channel", "date_order",
    "csv_delimiter", "max_rows", "max_columns", "max_cell_chars",
)  # fmt: skip
JOB_ERROR_STATUS = {"ING-901": 415, "ING-902": 413, "MAP-901": 422}

Clock = Callable[[], datetime]
MappingMethod = Literal["auto", "sniffed", "manual"]


def utc_now() -> datetime:
    return datetime.now(UTC)


# ---------- results returned to the web layer ----------


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    index: int
    header: str
    target_field: str | None
    method: str
    confidence: float
    samples: list[str]
    reason: str = ""


@dataclass(frozen=True, slots=True)
class FileCreated:
    index: int
    filename: str
    file_kind: str
    encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    header_row_index: int
    rows: int
    duplicate_of: int | None
    columns: list[ColumnInfo]
    preview: list[dict[str, str]]


@dataclass(frozen=True, slots=True)
class JobCreated:
    """Upload result. The top-level file fields describe the first file (kept for clients
    that upload one file); `files` describes every file."""

    job_id: str
    file_kind: str
    encoding: str | None
    sheet_name: str | None
    header_row_index: int
    columns: list[ColumnInfo]
    preview: list[dict[str, str]]
    canonical_fields: list[str]
    files: list[FileCreated]


@dataclass(frozen=True, slots=True)
class FileSummary:
    index: int
    filename: str
    file_kind: str
    encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    rows_in: int
    duplicate_of: int | None
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class JobInfo:
    job_id: str
    original_filename: str
    status: str
    error_code: str | None
    error_message: str | None
    counts: dict[str, int]
    rows_in: int | None
    rows_out: int | None
    created_at: str
    completed_at: str | None
    expires_at: str
    files: list[FileSummary]


@dataclass(frozen=True, slots=True)
class RuleCount:
    code: str
    count: int
    explanation: str


@dataclass(frozen=True, slots=True)
class Summary:
    counts: dict[str, int]
    rows_in: int
    rows_out: int
    top_rules: list[RuleCount]
    ruleset_version: str


@dataclass(frozen=True, slots=True)
class ChangeItem:
    record_id: str
    source_row: int
    field: str
    old_value: str
    new_value: str
    rule_code: str
    explanation: str


@dataclass(frozen=True, slots=True)
class ReviewItem:
    record_id: str | None
    source_rows: str
    status: str | None
    field: str | None
    rule_code: str
    severity: str
    problem: str
    original_value: str | None
    current_value: str | None
    suggestion: str | None
    related_record_ids: list[str]


@dataclass(frozen=True, slots=True)
class PageResult[T]:
    items: list[T]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True, slots=True)
class Download:
    path: str
    download_name: str
    media_type: str


# ---------- helpers ----------


def display_filename(filename: str) -> str:
    """Base name only, control characters removed, length-capped."""
    base = PureWindowsPath(PurePosixPath(filename).name).name
    cleaned = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C").strip()
    return cleaned[:MAX_FILENAME_CHARS] or "upload"


def unique_labels(names: list[str]) -> list[str]:
    """Display names that tell files apart: a repeated name gets " (2)", " (3)" ..."""
    seen: Counter[str] = Counter()
    labels = []
    for name in names:
        seen[name] += 1
        if seen[name] == 1:
            labels.append(name)
        else:
            path = PurePosixPath(name)
            labels.append(f"{path.stem} ({seen[name]}){path.suffix}")
    return labels


def download_stem(filename: str) -> str:
    stem = PurePosixPath(display_filename(filename)).stem
    safe = re.sub(r"[^\w.-]+", "_", stem, flags=re.ASCII).strip("._")
    return safe[:MAX_STEM_CHARS] or "export"


def _not_found() -> AppError:
    return AppError("JOB_NOT_FOUND", "We couldn't find that job. It may have been deleted.", 404)


def _wrong_state(message: str) -> AppError:
    return AppError("JOB_STATE", message, 409)


def _pipeline_error(error: PipelineError, label: str | None = None) -> AppError:
    status = JOB_ERROR_STATUS.get(error.rule_code, 422)
    message = get_rule(error.rule_code).explanation + "."
    return AppError(error.rule_code, f"{label}: {message}" if label else message, status)


def _file_created(
    index: int,
    label: str,
    table: Table,
    mappings: tuple[ColumnMapping, ...],
    duplicate_of: int | None,
) -> FileCreated:
    columns = [
        ColumnInfo(
            m.index,
            m.header,
            m.target_field,
            m.method,
            m.confidence,
            [r.cells[m.index] for r in table.rows if r.cells[m.index].strip()][:SAMPLE_VALUES],
            m.reason,
        )
        for m in mappings
    ]
    preview = [dict(zip(table.headers, r.cells, strict=True)) for r in table.rows[:PREVIEW_ROWS]]
    meta = table.meta
    return FileCreated(
        index, label, meta.file_kind, meta.encoding, meta.delimiter, meta.sheet_name,
        meta.header_row_index, len(table.rows), duplicate_of, columns, preview,
    )  # fmt: skip


class JobService:
    def __init__(
        self, database: Database, storage: FileStorage, settings: Settings, clock: Clock = utc_now
    ) -> None:
        self._db = database
        self._storage = storage
        self._settings = settings
        self._clock = clock

    # ----- settings -----

    def _snapshot(self) -> dict[str, Any]:
        return {name: getattr(self._settings, name) for name in SNAPSHOT_SETTINGS}

    def _pipeline_settings(self, snapshot: dict[str, Any], today: date) -> PipelineSettings:
        return PipelineSettings(
            today=today,
            max_upload_bytes=self._settings.max_upload_bytes,
            max_xlsx_uncompressed_bytes=self._settings.max_xlsx_uncompressed_bytes,
            **snapshot,
        )

    def _load_job(self, job_id: str) -> JobRow:
        with self._db.transaction() as connection:
            job = JobRepository(connection).get(job_id)
        if job is None:
            raise _not_found()
        return job

    # ----- use cases -----

    def _ingest_upload(
        self, label: str, data: bytes, settings: PipelineSettings
    ) -> tuple[str, Table]:
        """Validate one upload; errors name the file when several were sent."""
        if len(data) > self._settings.max_upload_bytes:
            raise _pipeline_error(PipelineError("ING-902"), label)
        extension = PurePosixPath(label).suffix.lower().lstrip(".")
        if extension not in ("csv", "xlsx"):
            raise AppError("UNSUPPORTED_TYPE", f"{label}: please upload a .csv or .xlsx file.", 415)
        try:
            if detect_kind(data) != extension:
                raise PipelineError("ING-901", "content does not match the file extension")
            return extension, ingest(data, settings)
        except PipelineError as error:
            raise _pipeline_error(error, label) from error

    def create_job(self, uploads: list[tuple[str, bytes]]) -> JobCreated:
        """Validate and ingest one or more uploads, store them, and propose column mappings."""
        if not uploads:
            raise AppError("NO_FILE", "Please choose at least one file.", 400)
        if len(uploads) > self._settings.max_files:
            message = f"Please upload at most {self._settings.max_files} files at once."
            raise AppError("TOO_MANY_FILES", message, 413)
        now = self._clock()
        snapshot = self._snapshot()
        settings = self._pipeline_settings(snapshot, now.date())
        labels = unique_labels([display_filename(name) for name, _ in uploads])
        ingested = [
            self._ingest_upload(label, data, settings)
            for label, (_, data) in zip(labels, uploads, strict=True)
        ]
        tables = [table for _, table in ingested]
        repeats = find_duplicate_sources(tables)
        rows_in = sum(len(t.rows) for i, t in enumerate(tables) if i not in repeats)
        if sum(len(t.rows) for t in tables) > self._settings.max_rows:
            raise _pipeline_error(PipelineError("ING-902"))
        job_id = str(uuid.uuid4())
        file_rows: list[JobFileRow] = []
        mapping_rows: list[MappingRow] = []
        created: list[FileCreated] = []
        for index, (label, (extension, table), (_, data)) in enumerate(
            zip(labels, ingested, uploads, strict=True)
        ):
            stored = self._storage.save_upload(job_id, extension, data, index)
            meta = table.meta
            file_rows.append(
                JobFileRow(index, label, meta.file_kind, stored, meta.encoding, meta.delimiter,
                           meta.sheet_name, meta.header_row_index, len(table.rows),
                           hashlib.sha256(data).hexdigest(), repeats.get(index))
            )  # fmt: skip
            mappings = propose_mapping(table)
            mapping_rows.extend(_mapping_row(m, index) for m in mappings)
            created.append(_file_created(index, label, table, mappings, repeats.get(index)))
        first = file_rows[0]
        job = JobRow(
            id=job_id,
            original_filename=first.original_filename,
            file_kind=first.file_kind,
            stored_upload_path=first.stored_upload_path,
            detected_encoding=first.detected_encoding,
            detected_delimiter=first.detected_delimiter,
            sheet_name=first.sheet_name,
            header_row_index=first.header_row_index,
            status="uploaded",
            error_code=None,
            settings_json=json.dumps(snapshot, sort_keys=True),
            ruleset_version=RULESET_VERSION,
            rows_in=rows_in,
            rows_out=None,
            created_at=now.isoformat(),
            started_at=None,
            completed_at=None,
            expires_at=(now + timedelta(days=self._settings.retention_days)).isoformat(),
        )
        with self._db.transaction() as connection:
            JobRepository(connection).insert(job)
            JobFileRepository(connection).insert_many(job_id, file_rows)
            MappingRepository(connection).replace(job_id, mapping_rows)
        logger.info("job %s created: %d file(s), %d rows", job_id, len(uploads), rows_in)
        head = created[0]
        return JobCreated(
            job_id,
            head.file_kind,
            head.encoding,
            head.sheet_name,
            head.header_row_index,
            head.columns,
            head.preview,
            list(CANONICAL_FIELDS),
            created,
        )

    def get_job(self, job_id: str) -> JobInfo:
        job = self._load_job(job_id)
        with self._db.transaction() as connection:
            records = RecordRepository(connection)
            counts = records.status_counts(job_id)
            by_file = records.status_counts_by_file(job_id)
            job_files = JobFileRepository(connection).list(job_id)
        files = [
            FileSummary(f.file_index, f.original_filename, f.file_kind, f.detected_encoding,
                        f.detected_delimiter, f.sheet_name, f.rows_in, f.duplicate_of,
                        by_file.get(f.original_filename, {}))
            for f in job_files
        ]  # fmt: skip
        message = _error_message(job.error_code)
        return JobInfo(
            job.id,
            job.original_filename,
            job.status,
            job.error_code,
            message,
            counts,
            job.rows_in,
            job.rows_out,
            job.created_at,
            job.completed_at,
            job.expires_at,
            files,
        )

    def update_mapping(
        self, job_id: str, targets: dict[tuple[int, int], str | None]
    ) -> list[MappingRow]:
        """Confirm or edit the mapping, keyed by (file index, column index).

        Columns not mentioned keep their proposed target. Every file that will be cleaned
        (not a repeated upload) needs an email or phone column.
        """
        job = self._load_job(job_id)
        if job.status not in ("uploaded", "mapped"):
            raise _wrong_state("The columns can't be changed after cleaning has started.")
        with self._db.transaction() as connection:
            current = MappingRepository(connection).list(job_id)
            job_files = JobFileRepository(connection).list(job_id)
        known = {(m.file_index, m.source_index) for m in current}
        if set(targets) - known:
            raise AppError(
                "INVALID_MAPPING", "The mapping refers to a column that doesn't exist.", 422
            )
        updated = [
            _apply_target(m, targets[key]) if (key := (m.file_index, m.source_index)) in targets
            else m
            for m in current
        ]  # fmt: skip
        for job_file in job_files:
            if job_file.duplicate_of is not None:
                continue
            file_targets = [m.target_field for m in updated if m.file_index == job_file.file_index]
            try:
                validate_targets(file_targets)
            except InvalidMappingError as error:
                message = f"{job_file.original_filename}: {error}"
                raise AppError("INVALID_MAPPING", message, 422) from error
            if not has_contact_field(file_targets):
                raise _pipeline_error(PipelineError("MAP-901"), job_file.original_filename)
        with self._db.transaction() as connection:
            MappingRepository(connection).replace(job_id, updated)
            JobRepository(connection).update(job_id, {"status": "mapped"})
        return updated

    def start_run(self, job_id: str) -> None:
        self._load_job(job_id)
        with self._db.transaction() as connection:
            started = JobRepository(connection).transition(
                job_id,
                ["mapped"],
                {"status": "processing", "started_at": self._clock().isoformat()},
            )
        if not started:
            raise _wrong_state("Please confirm the columns first, or upload the file again.")

    def execute_run(self, job_id: str) -> None:
        """Background task: clean, export and persist. Never raises; failures mark the job."""
        try:
            self._execute(job_id)
        except PipelineError as error:
            self._fail(job_id, error.rule_code)
        except Exception:
            logger.exception("job %s failed unexpectedly", job_id)
            self._fail(job_id, "INTERNAL")

    def _execute(self, job_id: str) -> None:
        job = self._load_job(job_id)
        started = datetime.fromisoformat(job.started_at or self._clock().isoformat())
        settings = self._pipeline_settings(json.loads(job.settings_json), started.date())
        with self._db.transaction() as connection:
            job_files = JobFileRepository(connection).list(job_id)
            mapping_rows = MappingRepository(connection).list(job_id)
        sources = [
            SourceFile(
                f.original_filename,
                ingest(self._storage.read(f.stored_upload_path), settings),
                tuple(_column_mapping(m) for m in mapping_rows if m.file_index == f.file_index),
            )
            for f in job_files
        ]
        result = run_sources(sources, settings)
        outputs = build_outputs(result).as_dict()
        completed = self._clock()
        stem = f"{download_stem(job.original_filename)}_A5_{completed.astimezone():%Y%m%d-%H%M}"
        files = [
            self._store_output(job_id, kind, data, stem, completed)
            for kind, data in outputs.items()
        ]
        with self._db.transaction() as connection:
            RecordRepository(connection).insert_many(job_id, _record_rows(result))
            ChangeRepository(connection).insert_many(job_id, _change_rows(result))
            IssueRepository(connection).insert_many(job_id, _issue_rows(result))
            for output in files:
                OutputFileRepository(connection).upsert(job_id, output)
            rows_out = sum(1 for r in result.records if r.status in CLEAN_FILE_STATUSES)
            JobRepository(connection).update(
                job_id,
                {
                    "status": "completed",
                    "rows_out": rows_out,
                    "completed_at": completed.isoformat(),
                },
            )
        logger.info("job %s completed: %d in, %d out", job_id, len(result.records), rows_out)

    def _store_output(
        self, job_id: str, kind: str, data: bytes, stem: str, created: datetime
    ) -> OutputFileRow:
        extension, suffix, _ = OUTPUT_FILES[kind]
        stored = self._storage.save_output(job_id, kind, extension, data)
        digest = hashlib.sha256(data).hexdigest()
        return OutputFileRow(
            kind, stored, f"{stem}_{suffix}", len(data), digest, created.isoformat()
        )

    def _fail(self, job_id: str, code: str) -> None:
        try:
            self._storage.delete_job_outputs(job_id)
            with self._db.transaction() as connection:
                JobRepository(connection).update(job_id, {"status": "failed", "error_code": code})
        except Exception:
            logger.exception("job %s could not be marked as failed", job_id)

    def _require_completed(self, job_id: str) -> JobRow:
        job = self._load_job(job_id)
        if job.status != "completed":
            raise _wrong_state("Results are not ready yet.")
        return job

    def summary(self, job_id: str) -> Summary:
        job = self._require_completed(job_id)
        with self._db.transaction() as connection:
            counts = RecordRepository(connection).status_counts(job_id)
            totals = Counter(ChangeRepository(connection).rule_counts(job_id))
            totals.update(IssueRepository(connection).rule_counts(job_id))
        ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:TOP_RULES]
        return Summary(
            counts,
            job.rows_in or 0,
            job.rows_out or 0,
            [RuleCount(code, n, get_rule(code).explanation) for code, n in ranked],
            job.ruleset_version,
        )

    def changes(
        self, job_id: str, page: int, page_size: int, rule_code: str | None, field: str | None
    ) -> PageResult[ChangeItem]:
        self._require_completed(job_id)
        with self._db.transaction() as connection:
            result = ChangeRepository(connection).page(job_id, page, page_size, rule_code, field)
        items = [
            ChangeItem(c.record_id, c.source_row, c.field, c.old_value, c.new_value, c.rule_code,
                       get_rule(c.rule_code).explanation)
            for c in result.items
        ]  # fmt: skip
        return PageResult(items, page, page_size, result.total)

    def review(
        self, job_id: str, page: int, page_size: int, severity: str | None, rule_code: str | None
    ) -> PageResult[ReviewItem]:
        self._require_completed(job_id)
        with self._db.transaction() as connection:
            result = IssueRepository(connection).review_page(
                job_id, page, page_size, severity, rule_code
            )
            records = RecordRepository(connection).get_many(
                job_id, [i.record_id for i in result.items if i.record_id]
            )
        items = [_review_item(issue, records.get(issue.record_id or "")) for issue in result.items]
        return PageResult(items, page, page_size, result.total)

    def download(self, job_id: str, kind: str) -> Download:
        self._require_completed(job_id)
        with self._db.transaction() as connection:
            output = OutputFileRepository(connection).get(job_id, kind)
        if output is None:
            raise AppError("FILE_NOT_FOUND", "That file is not available.", 404)
        return Download(
            str(self._storage.absolute(output.stored_path)),
            output.download_name,
            OUTPUT_FILES[kind][2],
        )

    def delete_job(self, job_id: str) -> None:
        job = self._load_job(job_id)
        if job.status == "processing":
            raise _wrong_state("Please wait until cleaning has finished, then delete the job.")
        self._storage.delete_job_files(job_id)
        with self._db.transaction() as connection:
            JobRepository(connection).delete(job_id)
        logger.info("job %s deleted on request", job_id)


# ---------- row conversions (cleaning models <-> db rows) ----------


def _error_message(code: str | None) -> str | None:
    """Plain-language reason a job failed; generic for unexpected errors."""
    if not code:
        return None
    try:
        return get_rule(code).explanation
    except KeyError:
        return "Something went wrong while cleaning. Please try again."


def _mapping_row(mapping: ColumnMapping, file_index: int = 0) -> MappingRow:
    return MappingRow(
        mapping.index, mapping.header, mapping.target_field, mapping.method, mapping.confidence,
        mapping.reason, file_index,
    )  # fmt: skip


def _column_mapping(row: MappingRow) -> ColumnMapping:
    method: MappingMethod = "manual"
    if row.method == "auto":
        method = "auto"
    elif row.method == "sniffed":
        method = "sniffed"
    return ColumnMapping(
        row.source_index, row.source_header, row.target_field, method, row.confidence, row.reason
    )


def _apply_target(row: MappingRow, target: str | None) -> MappingRow:
    if target == row.target_field:
        return row
    return MappingRow(
        row.source_index, row.source_header, target, "manual", 1.0, "manual", row.file_index
    )


def _record_rows(result: PipelineResult) -> list[RecordRow]:
    raw_by_id = {
        row.record_id: dict(zip(source.table.headers, row.cells, strict=True))
        for source in result.sources
        for row in source.table.rows
    }
    rows = []
    for record in result.records:
        clean = {**record.values, **record.extras}
        rows.append(
            RecordRow(
                record.record_id,
                record.source_row,
                json.dumps(raw_by_id[record.record_id], ensure_ascii=False),
                json.dumps(clean, ensure_ascii=False, sort_keys=True),
                record.status.value if record.status else "OK",
                record.get("consent_status") or None,
                record.merged_into,
                format_source_rows(record, result.is_multi_file),
                record.source_file,
            )
        )
    return rows


def _change_rows(result: PipelineResult) -> list[ChangeRow]:
    return [
        ChangeRow(c.record_id, c.source_row, c.field, c.old_value, c.new_value, c.rule_code, seq,
                  c.source_file)
        for seq, c in enumerate(result.changes, start=1)
    ]  # fmt: skip


def _issue_rows(result: PipelineResult) -> list[IssueRow]:
    return [
        IssueRow(i.record_id, i.field, i.rule_code, i.severity.value, i.message, i.original_value,
                 i.suggestion, LIST_SEPARATOR.join(i.related_record_ids))
        for i in result.issues
    ]  # fmt: skip


def _review_item(issue: IssueRow, record: RecordRow | None) -> ReviewItem:
    current = None
    if record and issue.field:
        current = json.loads(record.clean_json).get(issue.field)
    related = [r for r in issue.related_record_ids.split(LIST_SEPARATOR) if r]
    return ReviewItem(
        issue.record_id,
        record.source_rows if record else "",
        record.status if record else None,
        issue.field,
        issue.rule_code,
        issue.severity,
        issue.message,
        issue.original_value,
        current,
        issue.suggestion,
        related,
    )
