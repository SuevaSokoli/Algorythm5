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


@dataclass(frozen=True, slots=True)
class Record:
    """One customer row. `values` holds canonical fields, `extras` passthrough columns."""

    record_id: str
    source_row: int
    values: Mapping[str, str]
    extras: Mapping[str, str] = field(default_factory=dict)
    source_rows: tuple[int, ...] = ()
    merged_into: str | None = None
    status: RowStatus | None = None

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
class PipelineResult:
    table: Table
    mappings: tuple[ColumnMapping, ...]
    records: tuple[Record, ...]
    changes: tuple[Change, ...]
    issues: tuple[Issue, ...]
    settings: PipelineSettings
    ruleset_version: str
