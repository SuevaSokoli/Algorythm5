"""Stage 1: bytes -> Table of strings (type check, encoding, delimiter, sheet, header row)."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from typing import Literal

import charset_normalizer
from openpyxl import load_workbook

from algorythm5.cleaning.mapping import is_known_header
from algorythm5.cleaning.models import (
    IngestionMeta,
    Issue,
    PipelineError,
    PipelineSettings,
    RawRow,
    Table,
)
from algorythm5.cleaning.rules import get_rule

ZIP_MAGIC = b"PK\x03\x04"
CANDIDATE_ENCODINGS = ("cp1252", "cp1250", "iso8859_16", "latin_1")
DELIMITERS = ",;\t|"
SNIFF_LINES = 50
HEADER_SCAN_ROWS = 20
HEADER_MIN_TEXT_SHARE = 0.5
MAX_EXACT_FLOAT = 2**53
OVERFLOW_COLUMN = "_overflow"
OVERFLOW_SEPARATOR = " | "
TRUNCATION_PREVIEW_CHARS = 100

Grid = list[list[str]]


def _issue(
    code: str,
    *,
    record_id: str | None = None,
    field: str | None = None,
    detail: str | None = None,
    original: str | None = None,
) -> Issue:
    rule = get_rule(code)
    message = f"{rule.explanation}: {detail}" if detail else rule.explanation
    return Issue(record_id, field, code, rule.severity, message, original_value=original)


def detect_kind(data: bytes) -> Literal["csv", "xlsx"]:
    """Decide the type from content, never from the extension alone."""
    if data.startswith(ZIP_MAGIC):
        return "xlsx"
    if b"\x00" in data:
        raise PipelineError("ING-901", "binary content")
    return "csv"


# ---------- CSV ----------


def decode(data: bytes) -> tuple[str, str]:
    """Return (text, encoding). UTF-8 is tried strictly first; then charset-normalizer."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    best = charset_normalizer.from_bytes(data, cp_isolation=list(CANDIDATE_ENCODINGS)).best()
    if best is None:
        raise PipelineError("ING-901", "unknown text encoding")
    return str(best), best.encoding


def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:SNIFF_LINES])
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        return _most_consistent_delimiter(sample.splitlines())


def _most_consistent_delimiter(lines: Sequence[str]) -> str:
    """Delimiter whose per-line count is most often the same (and > 0)."""
    best, best_score = ",", (0, 0)
    for delimiter in DELIMITERS:
        counts = [line.count(delimiter) for line in lines if line.strip()]
        if not counts:
            continue
        mode, frequency = Counter(counts).most_common(1)[0]
        score = (frequency if mode else 0, mode)
        if score > best_score:
            best, best_score = delimiter, score
    return best


def read_csv(data: bytes) -> tuple[Grid, str, str]:
    text, encoding = decode(data)
    delimiter = sniff_delimiter(text)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
        grid = [list(row) for row in reader]
    except csv.Error as exc:  # e.g. a field beyond the csv module's size limit
        raise PipelineError("ING-901", "malformed csv") from exc
    return grid, encoding, delimiter


# ---------- XLSX ----------


def cell_to_text(value: object) -> str:
    """Exact string form of an XLSX cell value (keeps all phone digits, ISO dates)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < MAX_EXACT_FLOAT:
            return str(int(value))
        return repr(value)
    if isinstance(value, datetime):
        if value.time() == time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="seconds")
    return str(value)


def _check_zip(data: bytes, settings: PipelineSettings) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            uncompressed = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise PipelineError("ING-901", "damaged xlsx") from exc
    if "xl/workbook.xml" not in names:
        raise PipelineError("ING-901", "zip file is not an xlsx workbook")
    if uncompressed > settings.max_xlsx_uncompressed_bytes:
        raise PipelineError("ING-902", "workbook expands beyond the size limit")


def read_xlsx(data: bytes, settings: PipelineSettings) -> tuple[Grid, str, list[str]]:
    """First visible sheet with data. Returns (grid, sheet name, all sheets that have data)."""
    _check_zip(data, settings)
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises many unrelated types for corrupt files
        raise PipelineError("ING-901", "workbook could not be opened") from exc
    try:
        chosen: tuple[Grid, str] | None = None
        with_data: list[str] = []
        for sheet in workbook.worksheets:
            if getattr(sheet, "sheet_state", "visible") != "visible":
                continue
            grid = _read_sheet(sheet.iter_rows(values_only=True), settings)
            if any(any(cell.strip() for cell in row) for row in grid):
                with_data.append(sheet.title)
                if chosen is None:
                    chosen = (grid, sheet.title)
        if chosen is None:
            raise PipelineError("ING-901", "workbook has no data")
        return chosen[0], chosen[1], with_data
    finally:
        workbook.close()


def _read_sheet(rows: Iterable[tuple[object, ...]], settings: PipelineSettings) -> Grid:
    grid: Grid = []
    for row in rows:
        grid.append([cell_to_text(v) for v in row])
        if len(grid) > settings.max_rows + HEADER_SCAN_ROWS:
            raise PipelineError("ING-902", f"more than {settings.max_rows} rows")
    return grid


# ---------- shared ----------


def _is_text(cell: str) -> bool:
    stripped = cell.strip()
    return bool(stripped) and not stripped.replace(".", "").replace(",", "").isdigit()


def find_header_row(grid: Grid) -> int:
    """Index of the header row: first early row that is mostly text and names a known column."""
    non_empty = [i for i, row in enumerate(grid) if any(c.strip() for c in row)]
    for index in non_empty[:HEADER_SCAN_ROWS]:
        cells = [c for c in grid[index] if c.strip()]
        text_share = sum(1 for c in cells if _is_text(c)) / max(len(grid[index]), 1)
        if text_share >= HEADER_MIN_TEXT_SHARE and any(is_known_header(c) for c in cells):
            return index
    if not non_empty:
        raise PipelineError("ING-901", "file is empty")
    return non_empty[0]


def unique_headers(raw: Sequence[str]) -> list[str]:
    """Blank headers become column_<n>; repeats get _2, _3 suffixes."""
    seen: Counter[str] = Counter()
    headers: list[str] = []
    for position, header in enumerate(raw, start=1):
        name = " ".join(header.split()) or f"column_{position}"
        seen[name] += 1
        headers.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return headers


def _trim_trailing_empty(headers: list[str], raw_headers: Sequence[str], grid: Grid) -> list[str]:
    """Drop trailing columns that have neither a header nor any data."""
    width = len(headers)
    while width > 0 and not raw_headers[width - 1].strip():
        if any(len(row) >= width and row[width - 1].strip() for row in grid):
            break
        width -= 1
    return headers[:width]


def build_table(
    grid: Grid,
    meta_kind: Literal["csv", "xlsx"],
    settings: PipelineSettings,
    *,
    encoding: str | None = None,
    delimiter: str | None = None,
    sheet_name: str | None = None,
    issues: Sequence[Issue] = (),
) -> Table:
    header_index = find_header_row(grid)
    raw_headers = grid[header_index]
    body = grid[header_index + 1 :]
    headers = _trim_trailing_empty(unique_headers(raw_headers), raw_headers, body)
    if len(headers) > settings.max_columns:
        raise PipelineError("ING-902", f"more than {settings.max_columns} columns")
    found: list[Issue] = list(issues)
    if header_index > 0:
        found.append(_issue("ING-003", detail=f"line {header_index + 1}"))
    rows: list[RawRow] = []
    has_overflow = False
    empty_rows = 0
    for offset, cells in enumerate(body):
        source_row = header_index + offset + 2  # 1-based, as shown in Excel
        if not any(c.strip() for c in cells):
            empty_rows += 1
            continue
        record_id = f"R{len(rows) + 1:05d}"
        fitted, overflow = _fit(cells, len(headers))
        if overflow:
            has_overflow = True
            found.append(_issue("ING-006", record_id=record_id, original=overflow))
        truncated = []
        for header, cell in zip(headers, fitted, strict=True):
            if len(cell) > settings.max_cell_chars:
                preview = cell[:TRUNCATION_PREVIEW_CHARS] + "…"
                found.append(_issue("ING-008", record_id=record_id, field=header, original=preview))
                cell = cell[: settings.max_cell_chars]
            truncated.append(cell)
        rows.append(RawRow(record_id, source_row, (*truncated, overflow)))
    if len(rows) > settings.max_rows:
        raise PipelineError("ING-902", f"more than {settings.max_rows} rows")
    if empty_rows:
        found.append(_issue("ING-004", detail=f"{empty_rows} row(s)"))
    final_headers = (*headers, OVERFLOW_COLUMN) if has_overflow else tuple(headers)
    final_rows = tuple(
        r if has_overflow else RawRow(r.record_id, r.source_row, r.cells[:-1]) for r in rows
    )
    meta = IngestionMeta(meta_kind, encoding, delimiter, sheet_name, header_index)
    return Table(final_headers, final_rows, meta, tuple(found))


def _fit(cells: Sequence[str], width: int) -> tuple[list[str], str]:
    """Pad short rows; move non-empty extra cells to the overflow text."""
    padded = list(cells[:width]) + [""] * max(0, width - len(cells))
    extra = [c for c in cells[width:] if c.strip()]
    return padded, OVERFLOW_SEPARATOR.join(extra)


def ingest(data: bytes, settings: PipelineSettings) -> Table:
    """Parse uploaded bytes into a Table. Raises PipelineError(ING-9xx) for job-level problems."""
    if len(data) > settings.max_upload_bytes:
        raise PipelineError("ING-902", "file is larger than the upload limit")
    if not data.strip():
        raise PipelineError("ING-901", "file is empty")
    kind = detect_kind(data)
    if kind == "xlsx":
        grid, sheet_name, sheets_with_data = read_xlsx(data, settings)
        issues: list[Issue] = []
        if len(sheets_with_data) > 1:
            issues.append(_issue("ING-005", detail=", ".join(sheets_with_data)))
        return build_table(grid, "xlsx", settings, sheet_name=sheet_name, issues=issues)
    grid, encoding, delimiter = read_csv(data)
    if len(grid) > settings.max_rows + HEADER_SCAN_ROWS:
        raise PipelineError("ING-902", f"more than {settings.max_rows} rows")
    issues = [] if encoding in ("utf-8", "utf-8-sig") else [_issue("ING-001", detail=encoding)]
    return build_table(grid, "csv", settings, encoding=encoding, delimiter=delimiter, issues=issues)
