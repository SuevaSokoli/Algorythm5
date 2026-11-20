"""Data access. Parameterized SQL only; column names in dynamic SQL come from whitelists."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import astuple, dataclass, fields

JOB_STATUSES = ("uploaded", "mapped", "processing", "completed", "failed")
REVIEW_SEVERITIES = ("warning", "blocking")


@dataclass(frozen=True, slots=True)
class JobRow:
    id: str
    original_filename: str
    file_kind: str
    stored_upload_path: str
    detected_encoding: str | None
    detected_delimiter: str | None
    sheet_name: str | None
    header_row_index: int | None
    status: str
    error_code: str | None
    settings_json: str
    ruleset_version: str
    rows_in: int | None
    rows_out: int | None
    created_at: str
    started_at: str | None
    completed_at: str | None
    expires_at: str


JOB_COLUMNS = tuple(f.name for f in fields(JobRow))
UPDATABLE_JOB_COLUMNS = frozenset(JOB_COLUMNS) - {"id", "created_at"}


@dataclass(frozen=True, slots=True)
class MappingRow:
    source_index: int
    source_header: str
    target_field: str | None
    method: str
    confidence: float


@dataclass(frozen=True, slots=True)
class RecordRow:
    record_id: str
    source_row: int
    raw_json: str
    clean_json: str
    status: str
    consent_status: str | None
    merged_into: str | None
    source_rows: str


@dataclass(frozen=True, slots=True)
class ChangeRow:
    record_id: str
    source_row: int
    field: str
    old_value: str
    new_value: str
    rule_code: str
    seq: int


@dataclass(frozen=True, slots=True)
class IssueRow:
    record_id: str | None
    field: str | None
    rule_code: str
    severity: str
    message: str
    original_value: str | None
    suggestion: str | None
    related_record_ids: str


@dataclass(frozen=True, slots=True)
class OutputFileRow:
    kind: str
    stored_path: str
    download_name: str
    size_bytes: int
    sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    total: int


def _offset(page: int, page_size: int) -> int:
    return (page - 1) * page_size


class JobRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def insert(self, job: JobRow) -> None:
        placeholders = ", ".join("?" for _ in JOB_COLUMNS)
        self._db.execute(
            f"INSERT INTO jobs ({', '.join(JOB_COLUMNS)}) VALUES ({placeholders})",  # noqa: S608 - whitelisted columns
            astuple(job),
        )

    def get(self, job_id: str) -> JobRow | None:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return JobRow(**{c: row[c] for c in JOB_COLUMNS}) if row else None

    def update(self, job_id: str, values: Mapping[str, object]) -> None:
        self.transition(job_id, JOB_STATUSES, values)

    def transition(
        self, job_id: str, allowed_from: Sequence[str], values: Mapping[str, object]
    ) -> bool:
        """Atomically update a job only if its status is in `allowed_from`."""
        unknown = set(values) - UPDATABLE_JOB_COLUMNS
        if unknown:
            raise ValueError(f"not updatable: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        status_marks = ", ".join("?" for _ in allowed_from)
        cursor = self._db.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ? AND status IN ({status_marks})",  # noqa: S608 - whitelisted columns
            (*values.values(), job_id, *allowed_from),
        )
        return cursor.rowcount == 1

    def delete(self, job_id: str) -> bool:
        return self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,)).rowcount == 1

    def expired_ids(self, now_iso: str) -> list[str]:
        rows = self._db.execute("SELECT id FROM jobs WHERE expires_at < ?", (now_iso,))
        return [row["id"] for row in rows]


class MappingRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def replace(self, job_id: str, mappings: Iterable[MappingRow]) -> None:
        self._db.execute("DELETE FROM column_mappings WHERE job_id = ?", (job_id,))
        self._db.executemany(
            "INSERT INTO column_mappings (job_id, source_index, source_header, target_field, "
            "method, confidence) VALUES (?, ?, ?, ?, ?, ?)",
            [(job_id, *astuple(m)) for m in mappings],
        )

    def list(self, job_id: str) -> list[MappingRow]:
        rows = self._db.execute(
            "SELECT source_index, source_header, target_field, method, confidence "
            "FROM column_mappings WHERE job_id = ? ORDER BY source_index",
            (job_id,),
        )
        return [MappingRow(*row) for row in rows]


class RecordRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def insert_many(self, job_id: str, records: Iterable[RecordRow]) -> None:
        self._db.executemany(
            "INSERT INTO records (job_id, record_id, source_row, raw_json, clean_json, status, "
            "consent_status, merged_into, source_rows) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(job_id, *astuple(r)) for r in records],
        )

    def status_counts(self, job_id: str) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT status, COUNT(*) AS n FROM records WHERE job_id = ? GROUP BY status", (job_id,)
        )
        return {row["status"]: row["n"] for row in rows}

    def get_many(self, job_id: str, record_ids: Iterable[str]) -> dict[str, RecordRow]:
        ids = sorted(set(record_ids))
        if not ids:
            return {}
        marks = ", ".join("?" for _ in ids)
        rows = self._db.execute(
            "SELECT record_id, source_row, raw_json, clean_json, status, consent_status, "  # noqa: S608 - only "?" placeholders are interpolated
            f"merged_into, source_rows FROM records WHERE job_id = ? AND record_id IN ({marks})",
            (job_id, *ids),
        )
        return {row["record_id"]: RecordRow(*row) for row in rows}


class ChangeRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def insert_many(self, job_id: str, changes: Iterable[ChangeRow]) -> None:
        self._db.executemany(
            "INSERT INTO changes (job_id, record_id, source_row, field, old_value, new_value, "
            "rule_code, seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(job_id, *astuple(c)) for c in changes],
        )

    def page(
        self,
        job_id: str,
        page: int,
        page_size: int,
        rule_code: str | None = None,
        field: str | None = None,
    ) -> Page[ChangeRow]:
        where = ["job_id = ?"]
        params: list[object] = [job_id]
        if rule_code:
            where.append("rule_code = ?")
            params.append(rule_code)
        if field:
            where.append("field = ?")
            params.append(field)
        condition = " AND ".join(where)
        total = self._db.execute(
            f"SELECT COUNT(*) FROM changes WHERE {condition}",  # noqa: S608 - fixed fragments
            params,
        ).fetchone()[0]
        rows = self._db.execute(
            "SELECT record_id, source_row, field, old_value, new_value, rule_code, seq "  # noqa: S608 - condition is built from fixed fragments
            f"FROM changes WHERE {condition} ORDER BY seq LIMIT ? OFFSET ?",
            [*params, page_size, _offset(page, page_size)],
        )
        return Page([ChangeRow(*row) for row in rows], total)

    def rule_counts(self, job_id: str) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT rule_code, COUNT(*) FROM changes WHERE job_id = ? GROUP BY rule_code",
            (job_id,),
        )
        return {row[0]: row[1] for row in rows}


class IssueRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def insert_many(self, job_id: str, issues: Iterable[IssueRow]) -> None:
        self._db.executemany(
            "INSERT INTO issues (job_id, record_id, field, rule_code, severity, message, "
            "original_value, suggestion, related_record_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(job_id, *astuple(i)) for i in issues],
        )

    def review_page(
        self,
        job_id: str,
        page: int,
        page_size: int,
        severity: str | None = None,
        rule_code: str | None = None,
    ) -> Page[IssueRow]:
        """Warnings/blocking issues of records that were not merged away (or job-level)."""
        where = [
            "i.job_id = ?",
            "i.severity IN ('warning', 'blocking')",
            "(i.record_id IS NULL OR r.status IS NULL OR r.status <> 'MERGED')",
        ]
        params: list[object] = [job_id]
        if severity:
            where.append("i.severity = ?")
            params.append(severity)
        if rule_code:
            where.append("i.rule_code = ?")
            params.append(rule_code)
        source = (
            "FROM issues i LEFT JOIN records r "
            "ON r.job_id = i.job_id AND r.record_id = i.record_id WHERE " + " AND ".join(where)
        )
        total = self._db.execute(f"SELECT COUNT(*) {source}", params).fetchone()[0]
        rows = self._db.execute(
            "SELECT i.record_id, i.field, i.rule_code, i.severity, i.message, i.original_value, "
            f"i.suggestion, i.related_record_ids {source} "
            "ORDER BY i.record_id, CASE i.severity WHEN 'blocking' THEN 0 ELSE 1 END, i.id "
            "LIMIT ? OFFSET ?",
            [*params, page_size, _offset(page, page_size)],
        )
        return Page([IssueRow(*row) for row in rows], total)

    def rule_counts(self, job_id: str) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT rule_code, COUNT(*) FROM issues WHERE job_id = ? GROUP BY rule_code",
            (job_id,),
        )
        return {row[0]: row[1] for row in rows}


class OutputFileRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection

    def upsert(self, job_id: str, output: OutputFileRow) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO output_files (job_id, kind, stored_path, download_name, "
            "size_bytes, sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, *astuple(output)),
        )

    def get(self, job_id: str, kind: str) -> OutputFileRow | None:
        row = self._db.execute(
            "SELECT kind, stored_path, download_name, size_bytes, sha256, created_at "
            "FROM output_files WHERE job_id = ? AND kind = ?",
            (job_id, kind),
        ).fetchone()
        return OutputFileRow(*row) if row else None

    def list(self, job_id: str) -> list[OutputFileRow]:
        rows = self._db.execute(
            "SELECT kind, stored_path, download_name, size_bytes, sha256, created_at "
            "FROM output_files WHERE job_id = ? ORDER BY kind",
            (job_id,),
        )
        return [OutputFileRow(*row) for row in rows]
