"""Stage 10: PipelineResult -> output file bytes (report.xlsx and CSVs). Deterministic."""

from __future__ import annotations

import csv
import io
import re
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
    "birth_date", "signup_date", "consent_status", "review_flag", "source_rows", "source_file",
)  # fmt: skip
# Mapped input fields that are not in the clean schema are kept after it, like passthrough.
KEPT_INPUT_FIELDS: tuple[str, ...] = ("customer_id", "notes")
REVIEW_COLUMNS: tuple[str, ...] = (
    "record_id", "source_rows", "source_file", "status", "field", "rule_code", "problem",
    "original_value", "current_value", "suggestion", "related_record_ids",
)  # fmt: skip
CHANGE_COLUMNS: tuple[str, ...] = (
    "record_id", "source_file", "source_row", "field", "old_value", "new_value", "rule_code",
    "explanation",
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
FIXED_XLSX_STAMP = b"2000-01-01T00:00:00Z"
_CORE_MODIFIED = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")
SEVERITY_ORDER = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}
SAME_NAME_SHEET = "Same name, different people"

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


def format_source_rows(record: Record, multi_file: bool) -> str:
    """'2; 5' for one file; 'a.csv:2; b.xlsx:5' when the job has several files."""
    if multi_file:
        return LIST_SEPARATOR.join(f"{file}:{row}" for file, row in record.origins())
    return LIST_SEPARATOR.join(str(r) for r in record.source_rows)


def format_source_files(record: Record) -> str:
    files = dict.fromkeys(file for file, _ in record.origins() if file)
    return LIST_SEPARATOR.join(files) or record.source_file


def _record_value(record: Record, column: str, multi_file: bool = False) -> str:
    if column == "record_id":
        return record.record_id
    if column == "review_flag":
        return "yes" if record.status == RowStatus.REVIEW else ""
    if column == "source_rows":
        return format_source_rows(record, multi_file)
    if column == "source_file":
        return format_source_files(record)
    if column in record.values:
        return record.values[column]
    return record.extras.get(column, "")


def record_rows(
    records: Iterable[Record], columns: Sequence[str], multi_file: bool = False
) -> list[Row]:
    return [[_record_value(r, c, multi_file) for c in columns] for r in records]


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
    multi = result.is_multi_file
    for issue, record in review_issues(result):
        current = _record_value(record, issue.field) if record and issue.field else ""
        rows.append([
            issue.record_id or "",
            _record_value(record, "source_rows", multi) if record else "",
            format_source_files(record) if record else "",
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
        [c.record_id, c.source_file, str(c.source_row), c.field, c.old_value, c.new_value,
         c.rule_code, get_rule(c.rule_code).explanation]
        for c in result.changes
    ]  # fmt: skip


# ---------- multi-file reports ----------

FILE_COLUMNS: tuple[str, ...] = (
    "file", "type", "encoding", "delimiter", "sheet", "header_row", "rows_read", "used",
    "ready", "review", "merged", "opted_out", "could_not_use",
)  # fmt: skip
MERGE_COLUMNS: tuple[str, ...] = (
    "kept_record_id", "kept_source", "merged_record_id", "merged_source_file",
    "merged_source_row", "rule_code", "why",
)  # fmt: skip
PERSON_DETAILS: tuple[str, ...] = ("email", "phone", "birth_date", "address")
SAME_NAME_COLUMNS: tuple[str, ...] = (
    "name", "differences",
    "record_id_a", "source_a", *(f"{d}_a" for d in PERSON_DETAILS),
    "record_id_b", "source_b", *(f"{d}_b" for d in PERSON_DETAILS),
)  # fmt: skip
NOT_USABLE_STATUSES = frozenset({RowStatus.REJECTED, RowStatus.BLOCKED})


def file_rows(result: PipelineResult) -> list[Row]:
    """Per-file summary: what was read and what became of its rows."""
    rows: list[Row] = []
    for index, source in enumerate(result.sources):
        meta = source.table.meta
        own = [r for r in result.records if r.source_file == source.label]
        counts = Counter(r.status for r in own)
        repeat = result.duplicate_sources.get(index)
        used = f"no, same as {result.sources[repeat].label}" if repeat is not None else "yes"
        header = "none" if meta.header_row_index < 0 else str(meta.header_row_index + 1)
        rows.append([
            source.label, meta.file_kind, meta.encoding or "", meta.delimiter or "",
            meta.sheet_name or "", header, str(len(source.table.rows)), used,
            str(sum(counts[s] for s in CLEAN_FILE_STATUSES)), str(counts[RowStatus.REVIEW]),
            str(counts[RowStatus.MERGED]), str(counts[RowStatus.SUPPRESSED]),
            str(sum(counts[s] for s in NOT_USABLE_STATUSES)),
        ])  # fmt: skip
    return rows


def merge_rows(result: PipelineResult) -> list[Row]:
    by_id = {r.record_id: r for r in result.records}
    codes = {c.record_id: c.rule_code for c in result.changes if c.field == "merged_into"}
    rows: list[Row] = []
    for record in result.records:
        if not record.merged_into:
            continue
        kept = by_id[record.merged_into]
        code = codes.get(record.record_id, "")
        rows.append([
            kept.record_id, format_source_rows(kept, True), record.record_id,
            record.source_file, str(record.source_row), code,
            get_rule(code).explanation if code else "",
        ])  # fmt: skip
    return rows


def _person_details(record: Record) -> list[str]:
    address = ", ".join(v for v in (record.get("street"), record.get("postal_code"),
                                    record.get("city")) if v)  # fmt: skip
    return [record.get("email"), record.get("phone"), record.get("birth_date"), address]


def same_name_rows(result: PipelineResult) -> list[Row]:
    """One row per pair kept apart as 'same name, different people' (DUP-008)."""
    by_id = {r.record_id: r for r in result.records}
    rows: list[Row] = []
    seen: set[tuple[str, str]] = set()
    for issue in result.issues:
        if issue.rule_code != "DUP-008" or not issue.record_id or not issue.related_record_ids:
            continue
        first, second = sorted((issue.record_id, issue.related_record_ids[0]))
        if (first, second) in seen:
            continue
        seen.add((first, second))
        a, b = by_id[first], by_id[second]
        differences = issue.message.rpartition(": ")[2]
        rows.append([
            issue.original_value or "", differences,
            a.record_id, format_source_rows(a, True), *_person_details(a),
            b.record_id, format_source_rows(b, True), *_person_details(b),
        ])  # fmt: skip
    return rows


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
            content = source.read(info.filename)
            if info.filename == "docProps/core.xml":
                # openpyxl stamps the save time into "modified", whatever was set before.
                content = _CORE_MODIFIED.sub(rb"\g<1>" + FIXED_XLSX_STAMP + rb"\g<2>", content)
            target.writestr(entry, content)
    return output.getvalue()


def build_report(result: PipelineResult) -> bytes:
    columns = [*CLEAN_COLUMNS, *extra_columns(result.records)]
    multi = result.is_multi_file
    by_status = {s: [r for r in result.records if r.status == s] for s in RowStatus}
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.properties.created = FIXED_XLSX_TIME
    workbook.properties.modified = FIXED_XLSX_TIME
    workbook.properties.creator = "Algorythm5"
    _add_sheet(workbook, "Summary", ["Item", "Value"], summary_rows(result))
    _add_sheet(workbook, "Clean", columns, record_rows(clean_records(result), columns, multi))
    _add_sheet(workbook, "Review", REVIEW_COLUMNS, review_rows(result))
    _add_sheet(workbook, "Changes", CHANGE_COLUMNS, change_rows(result))
    _add_sheet(
        workbook,
        "Suppressed",
        columns,
        record_rows(by_status[RowStatus.SUPPRESSED], columns, multi),
    )
    _add_sheet(
        workbook, "Rejected", columns, record_rows(by_status[RowStatus.REJECTED], columns, multi)
    )
    _add_sheet(workbook, "Files", FILE_COLUMNS, file_rows(result))
    _add_sheet(workbook, "Merges", MERGE_COLUMNS, merge_rows(result))
    _add_sheet(workbook, SAME_NAME_SHEET, SAME_NAME_COLUMNS, same_name_rows(result))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return _deterministic_zip(buffer.getvalue())


def build_outputs(result: PipelineResult) -> OutputFiles:
    delimiter = result.settings.csv_delimiter
    columns = [*CLEAN_COLUMNS, *extra_columns(result.records)]
    clean = record_rows(clean_records(result), columns, result.is_multi_file)
    return OutputFiles(
        report_xlsx=build_report(result),
        clean_csv=to_csv(columns, clean, delimiter),
        changes_csv=to_csv(CHANGE_COLUMNS, change_rows(result), delimiter),
        review_csv=to_csv(REVIEW_COLUMNS, review_rows(result), delimiter),
    )
