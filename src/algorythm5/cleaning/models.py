"""Value objects shared by every cleaning stage. No I/O, no framework imports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Literal


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class RowStatus(StrEnum):
    OK = "OK"
    FIXED = "FIXED"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"
    REJECTED = "REJECTED"
    SUPPRESSED = "SUPPRESSED"
    MERGED = "MERGED"


class ConsentStatus(StrEnum):
    SUBSCRIBED = "subscribed"
    OPTED_OUT = "opted_out"
    UNKNOWN = "unknown"


class PipelineError(Exception):
    """A job-level problem (rule codes x9xx) that stops the whole run."""

    def __init__(self, rule_code: str, detail: str = "") -> None:
        super().__init__(f"{rule_code}: {detail}" if detail else rule_code)
        self.rule_code = rule_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PipelineSettings:
    """Everything a run depends on; injected so results are deterministic."""

    today: date
    default_phone_region: str = "AL"
    unknown_consent_policy: Literal["include", "review", "exclude"] = "include"
    required_channel: Literal["email", "any"] = "email"
    date_order: Literal["DMY", "MDY"] = "DMY"
    csv_delimiter: str = ","
    max_upload_bytes: int = 10 * 1024 * 1024
    max_rows: int = 50_000
    max_columns: int = 100
    max_cell_chars: int = 1_000
    max_xlsx_uncompressed_bytes: int = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Change:
    record_id: str
    source_row: int
    field: str
    old_value: str
    new_value: str
    rule_code: str
    source_file: str = ""


@dataclass(frozen=True, slots=True)
class Issue:
    record_id: str | None
    field: str | None
    rule_code: str
    severity: Severity
    message: str
    original_value: str | None = None
    suggestion: str | None = None
    related_record_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Step:
    """One value transformation made by a field cleaner, before it is bound to a record."""

    rule_code: str
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class Flag:
    """One problem found by a field cleaner, before it is bound to a record."""

    rule_code: str
    severity: Severity | None = None
    original_value: str | None = None
    suggestion: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RawRow:
    record_id: str
    source_row: int
    cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IngestionMeta:
    file_kind: Literal["csv", "xlsx"]
    encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    header_row_index: int


@dataclass(frozen=True, slots=True)
class Table:
    """Ingested file: headers plus string cells, with ingestion-level findings."""

    headers: tuple[str, ...]
    rows: tuple[RawRow, ...]
    meta: IngestionMeta
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    index: int
    header: str
    target_field: str | None
    method: Literal["auto", "sniffed", "manual"]
    confidence: float
    # Why this target was chosen (see mapping.REASONS); shown to the user in the UI.
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Record:
    """One customer row. `values` holds canonical fields, `extras` passthrough columns.

    `source_file`/`source_row` say where the row came from. A merged survivor lists every
    contributing row in `source_rows`, with the matching file names in `source_files`.
    """

    record_id: str
    source_row: int
    values: Mapping[str, str]
    extras: Mapping[str, str] = field(default_factory=dict)
    source_rows: tuple[int, ...] = ()
    merged_into: str | None = None
    status: RowStatus | None = None
    source_file: str = ""
    source_files: tuple[str, ...] = ()

    def origins(self) -> tuple[tuple[str, int], ...]:
        """(file, row) pairs of every contributing row."""
        files = self.source_files or (self.source_file,) * len(self.source_rows)
        return tuple(zip(files, self.source_rows, strict=True))

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def with_values(
        self, values: Mapping[str, str], extras: Mapping[str, str] | None = None
    ) -> Record:
        new_extras = dict(self.extras) if extras is None else dict(extras)
        return replace(self, values=dict(values), extras=new_extras)


@dataclass(frozen=True, slots=True)
class StageResult:
    records: tuple[Record, ...]
    changes: tuple[Change, ...] = ()
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One uploaded file of a job: display label, ingested table and confirmed mapping."""

    label: str
    table: Table
    mappings: tuple[ColumnMapping, ...]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    sources: tuple[SourceFile, ...]
    records: tuple[Record, ...]
    changes: tuple[Change, ...]
    issues: tuple[Issue, ...]
    settings: PipelineSettings
    ruleset_version: str
    # Index of each ignored source -> index of the identical source it repeats.
    duplicate_sources: Mapping[int, int] = field(default_factory=dict)

    @property
    def is_multi_file(self) -> bool:
        return len(self.sources) > 1
