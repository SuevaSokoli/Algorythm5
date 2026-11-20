"""Stage 10: PipelineResult -> output file bytes (report.xlsx and CSVs). Deterministic."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from algorythm5.cleaning.models import Issue, PipelineResult, Record, RowStatus, Severity
from algorythm5.cleaning.phones import E164_LIST
from algorythm5.cleaning.rules import get_rule
from algorythm5.cleaning.status import CLEAN_FILE_STATUSES, is_review_item

CLEAN_COLUMNS: tuple[str, ...] = (
    "record_id", "salutation", "first_name", "last_name", "email", "email_other", "phone",
    "phone_extension", "phone_other", "company", "street", "postal_code", "city", "country",
    "birth_date", "signup_date", "consent_status", "review_flag", "source_rows",
)  # fmt: skip
# Mapped input fields that are not in the clean schema are kept after it, like passthrough.
KEPT_INPUT_FIELDS: tuple[str, ...] = ("customer_id", "notes")
REVIEW_COLUMNS: tuple[str, ...] = (
    "record_id", "source_rows", "status", "field", "rule_code", "problem", "original_value",
    "current_value", "suggestion", "related_record_ids",
)  # fmt: skip
CHANGE_COLUMNS: tuple[str, ...] = (
    "record_id", "source_row", "field", "old_value", "new_value", "rule_code", "explanation",
)  # fmt: skip
TEXT_COLUMNS = frozenset(
    {"record_id", "phone", "phone_other", "phone_extension", "postal_code", "customer_id",
     "source_rows", "source_row"}
)  # fmt: skip
PHONE_COLUMNS = frozenset({"phone", "phone_other"})
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
MAX_COLUMN_WIDTH = 50
MIN_COLUMN_WIDTH = 8
TOP_RULES = 10
LIST_SEPARATOR = "; "
# Fixed timestamps keep the workbook byte-identical between runs.
FIXED_XLSX_TIME = datetime(2000, 1, 1)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
SEVERITY_ORDER = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}

Row = list[str]


@dataclass(frozen=True, slots=True)
class OutputFiles:
    report_xlsx: bytes
    clean_csv: bytes
    changes_csv: bytes
    review_csv: bytes

    def as_dict(self) -> dict[str, bytes]:
        return asdict(self)


def guard_cell(column: str, value: str) -> str:
    """Neutralise spreadsheet formulas; validated E.164 phone lists are left untouched."""
    if not value.startswith(FORMULA_TRIGGERS):
        return value
    if column in PHONE_COLUMNS and E164_LIST.match(value):
        return value
    return "'" + value


def extra_columns(records: Sequence[Record]) -> list[str]:
    """Kept input fields and passthrough headers, in first-seen order."""
    columns: dict[str, None] = {}
    for field in KEPT_INPUT_FIELDS:
        if any(r.get(field) for r in records):
            columns[field] = None
    for record in records:
        columns.update(dict.fromkeys(record.extras))
    return list(columns)


def _record_value(record: Record, column: str) -> str:
    if column == "record_id":
        return record.record_id
    if column == "review_flag":
        return "yes" if record.status == RowStatus.REVIEW else ""
    if column == "source_rows":
        return LIST_SEPARATOR.join(str(r) for r in record.source_rows)
    if column in record.values:
        return record.values[column]
    return record.extras.get(column, "")


def record_rows(records: Iterable[Record], columns: Sequence[str]) -> list[Row]:
    return [[_record_value(r, c) for c in columns] for r in records]


def clean_records(result: PipelineResult) -> list[Record]:
    return [r for r in result.records if r.status in CLEAN_FILE_STATUSES]


def review_issues(result: PipelineResult) -> list[tuple[Issue, Record | None]]:
    by_id = {r.record_id: r for r in result.records}
    items = []
    for issue in result.issues:
        record = by_id.get(issue.record_id or "")
        if is_review_item(issue, record.status if record else None):
            items.append((issue, record))
    return sorted(
        items, key=lambda pair: (pair[0].record_id or "", SEVERITY_ORDER[pair[0].severity])
    )


def review_rows(result: PipelineResult) -> list[Row]:
    rows: list[Row] = []
    for issue, record in review_issues(result):
        current = _record_value(record, issue.field) if record and issue.field else ""
        rows.append([
            issue.record_id or "",
            _record_value(record, "source_rows") if record else "",
            record.status.value if record and record.status else "",
            issue.field or "",
            issue.rule_code,
            issue.message,
            issue.original_value or "",
            current,
            issue.suggestion or "",
            LIST_SEPARATOR.join(issue.related_record_ids),
        ])  # fmt: skip
    return rows


def change_rows(result: PipelineResult) -> list[Row]:
    return [
        [c.record_id, str(c.source_row), c.field, c.old_value, c.new_value, c.rule_code,
         get_rule(c.rule_code).explanation]
        for c in result.changes
    ]  # fmt: skip


def status_counts(result: PipelineResult) -> dict[str, int]:
    counts = Counter(r.status.value for r in result.records if r.status)
    return {status.value: counts.get(status.value, 0) for status in RowStatus}


def top_rules(result: PipelineResult, limit: int = TOP_RULES) -> list[tuple[str, int]]:
    counts = Counter(c.rule_code for c in result.changes)
    counts.update(i.rule_code for i in result.issues)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


def summary_rows(result: PipelineResult) -> list[Row]:
    counts = status_counts(result)
    rows: list[Row] = [["Rows received", str(len(result.records))]]
    rows.append(["Rows in clean file", str(len(clean_records(result)))])
    rows.extend([f"Status {status}", str(count)] for status, count in counts.items())
    rows.append(["", ""])
    rows.append(["Most frequent rules", ""])
    rows.extend(
        [code, f"{count} — {get_rule(code).explanation}"] for code, count in top_rules(result)
    )
    rows.append(["", ""])
    rows.append(["Ruleset version", result.ruleset_version])
    settings = result.settings
    for name in ("default_phone_region", "unknown_consent_policy", "required_channel",
                 "date_order", "csv_delimiter"):  # fmt: skip
        rows.append([f"Setting {name}", str(getattr(settings, name))])
    return rows


def to_csv(columns: Sequence[str], rows: Iterable[Row], delimiter: str) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([guard_cell(c, v) for c, v in zip(columns, row, strict=True)])
    return buffer.getvalue().encode("utf-8-sig")  # BOM so Excel shows ë and ç


def _add_sheet(workbook: Workbook, title: str, columns: Sequence[str], rows: Sequence[Row]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.append(list(columns))
    for row in rows:
        sheet.append([guard_cell(c, v) for c, v in zip(columns, row, strict=True)])
    bold = Font(bold=True)
    for cell in sheet[1]:
        cell.font = bold
    sheet.freeze_panes = "A2"
    if rows:
        sheet.auto_filter.ref = sheet.dimensions
    for index, column in enumerate(columns, start=1):
        letter = get_column_letter(index)
        longest = max([len(column), *(len(r[index - 1]) for r in rows)])
        sheet.column_dimensions[letter].width = max(
            MIN_COLUMN_WIDTH, min(MAX_COLUMN_WIDTH, longest + 2)
        )
        if column in TEXT_COLUMNS:
            for cell in sheet[letter][1:]:
                cell.number_format = "@"


def _deterministic_zip(data: bytes) -> bytes:
    """Rewrite the xlsx archive with fixed entry timestamps."""
    source = zipfile.ZipFile(io.BytesIO(data))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(entry, source.read(info.filename))
    return output.getvalue()


def build_report(result: PipelineResult) -> bytes:
    columns = [*CLEAN_COLUMNS, *extra_columns(result.records)]
    by_status = {s: [r for r in result.records if r.status == s] for s in RowStatus}
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.created = FIXED_XLSX_TIME
    workbook.properties.modified = FIXED_XLSX_TIME
    workbook.properties.creator = "Algorythm5"
    _add_sheet(workbook, "Summary", ["Item", "Value"], summary_rows(result))
    _add_sheet(workbook, "Clean", columns, record_rows(clean_records(result), columns))
    _add_sheet(workbook, "Review", REVIEW_COLUMNS, review_rows(result))
    _add_sheet(workbook, "Changes", CHANGE_COLUMNS, change_rows(result))
    _add_sheet(
        workbook, "Suppressed", columns, record_rows(by_status[RowStatus.SUPPRESSED], columns)
    )
    _add_sheet(workbook, "Rejected", columns, record_rows(by_status[RowStatus.REJECTED], columns))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _deterministic_zip(buffer.getvalue())


def build_outputs(result: PipelineResult) -> OutputFiles:
    delimiter = result.settings.csv_delimiter
    columns = [*CLEAN_COLUMNS, *extra_columns(result.records)]
    return OutputFiles(
        report_xlsx=build_report(result),
        clean_csv=to_csv(columns, record_rows(clean_records(result), columns), delimiter),
        changes_csv=to_csv(CHANGE_COLUMNS, change_rows(result), delimiter),
        review_csv=to_csv(REVIEW_COLUMNS, review_rows(result), delimiter),
    )
