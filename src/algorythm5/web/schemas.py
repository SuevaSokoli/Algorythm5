"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


class OutputKind(StrEnum):
    REPORT_XLSX = "report_xlsx"
    CLEAN_CSV = "clean_csv"
    CHANGES_CSV = "changes_csv"
    REVIEW_CSV = "review_csv"


class ReviewSeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


class FromAttributes(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Health(BaseModel):
    status: str
    ruleset_version: str


class RuleOut(BaseModel):
    code: str
    severity: str
    explanation: str


class ColumnOut(FromAttributes):
    file_index: int = 0
    index: int
    header: str
    target_field: str | None
    method: str
    confidence: float
    reason: str = ""
    samples: list[str] = []


class FileCreatedOut(FromAttributes):
    index: int
    filename: str
    file_kind: str
    encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    header_row_index: int
    rows: int
    duplicate_of: int | None
    columns: list[ColumnOut]
    preview: list[dict[str, str]]


class JobCreatedOut(FromAttributes):
    job_id: str
    file_kind: str
    encoding: str | None
    sheet_name: str | None
    header_row_index: int
    columns: list[ColumnOut]
    preview: list[dict[str, str]]
    canonical_fields: list[str]
    files: list[FileCreatedOut]


class FileSummaryOut(FromAttributes):
    index: int
    filename: str
    file_kind: str
    encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    rows_in: int
    duplicate_of: int | None
    counts: dict[str, int]


class JobOut(FromAttributes):
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
    files: list[FileSummaryOut] = []


class MappingColumnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_index: int = Field(default=0, ge=0, le=100)
    index: int = Field(ge=0, le=10_000)
    target_field: str | None = Field(default=None, max_length=64)


class MappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[MappingColumnIn] = Field(min_length=1, max_length=1_000)


class MappingOut(BaseModel):
    columns: list[ColumnOut]


class RunOut(BaseModel):
    status: str


class RuleCountOut(FromAttributes):
    code: str
    count: int
    explanation: str


class SummaryOut(FromAttributes):
    counts: dict[str, int]
    rows_in: int
    rows_out: int
    top_rules: list[RuleCountOut]
    ruleset_version: str


class ChangeOut(FromAttributes):
    record_id: str
    source_row: int
    field: str
    old_value: str
    new_value: str
    rule_code: str
    explanation: str


class ReviewOut(FromAttributes):
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


class ChangePageOut(FromAttributes):
    items: list[ChangeOut]
    page: int
    page_size: int
    total: int


class ReviewPageOut(FromAttributes):
    items: list[ReviewOut]
    page: int
    page_size: int
    total: int
