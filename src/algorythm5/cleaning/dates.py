"""Stage 5d: dates — many input formats to ISO, column-level day/month inference, plausibility."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from algorythm5.cleaning.models import PipelineSettings, Record, Severity, StageResult
from algorythm5.cleaning.rules import StageLog
from algorythm5.cleaning.text import fold

DATE_FIELDS = ("birth_date", "signup_date")
EXCEL_EPOCH = date(1899, 12, 30)
MAX_EXCEL_SERIAL = 60_000
EARLIEST_PLAUSIBLE = date(1900, 1, 1)
MIN_AGE_YEARS = 13
MAX_AGE_YEARS = 120
MONTHS_PER_YEAR = 12
EXCEL_SENTINELS = frozenset({"1900-01-00", "00/01/1900", "0/1/1900", "1899-12-30", "1899-12-31"})

Order = Literal["DMY", "MDY"]

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "janar": 1, "shkurt": 2, "mars": 3, "prill": 4, "maj": 5, "qershor": 6, "korrik": 7,
    "gusht": 8, "shtator": 9, "tetor": 10, "nentor": 11, "dhjetor": 12,
    "shk": 2, "pri": 4, "qer": 6, "kor": 7, "gus": 8, "sht": 9, "tet": 10, "nen": 11,
    "dhj": 12,
}  # fmt: skip

_TIME = r"(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"
_ISO = re.compile(rf"^(\d{{4}})[-/.](\d{{1,2}})[-/.](\d{{1,2}}){_TIME}$")
_NUMERIC = re.compile(rf"^(\d{{1,2}})[./-](\d{{1,2}})[./-](\d{{2}}|\d{{4}}){_TIME}$")
_DAY_MONTH_NAME = re.compile(r"^(\d{1,2})[\s.-]+([^\W\d_]+)\.?[\s.,-]+(\d{2}|\d{4})$")
_MONTH_NAME_DAY = re.compile(r"^([^\W\d_]+)\.?\s+(\d{1,2}),?\s+(\d{4})$")
_SERIAL = re.compile(r"^(\d{1,5})(?:\.0+)?$")


@dataclass(frozen=True, slots=True)
class ParsedDate:
    value: date
    is_serial: bool = False
    ambiguous: bool = False


def _year(text: str, today: date, *, birth: bool) -> int:
    if len(text) == 4:
        return int(text)
    two = int(text)
    century = 2000 if two <= today.year % 100 else 1900
    year = century + two
    if birth and year > today.year:
        year -= 100
    return year


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def column_order_evidence(values: Sequence[str]) -> set[Order]:
    """Orders proven by unambiguous numeric dates in a column (e.g. 25/03 proves DMY)."""
    evidence: set[Order] = set()
    for value in values:
        match = _NUMERIC.match(value.strip())
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > MONTHS_PER_YEAR >= second:
            evidence.add("DMY")
        elif second > MONTHS_PER_YEAR >= first:
            evidence.add("MDY")
    return evidence


def parse_date(value: str, order: Order, today: date, *, birth: bool = False) -> ParsedDate | None:
    """Parse one value; None when it is not a recognizable date."""
    text = value.strip()
    if match := _ISO.match(text):
        parsed = _make(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return ParsedDate(parsed) if parsed else None
    if match := _NUMERIC.match(text):
        first, second = int(match.group(1)), int(match.group(2))
        year = _year(match.group(3), today, birth=birth)
        ambiguous = first <= MONTHS_PER_YEAR and second <= MONTHS_PER_YEAR and first != second
        day, month = (first, second) if order == "DMY" else (second, first)
        if first > MONTHS_PER_YEAR:
            day, month = first, second
        elif second > MONTHS_PER_YEAR:
            day, month = second, first
        parsed = _make(year, month, day)
        return ParsedDate(parsed, ambiguous=ambiguous) if parsed else None
    if match := _DAY_MONTH_NAME.match(text):
        month_number = _MONTHS.get(fold(match.group(2)))
        year = _year(match.group(3), today, birth=birth)
        parsed = _make(year, month_number, int(match.group(1))) if month_number else None
        return ParsedDate(parsed) if parsed else None
    if match := _MONTH_NAME_DAY.match(text):
        month_number = _MONTHS.get(fold(match.group(1)))
        parsed = (
            _make(int(match.group(3)), month_number, int(match.group(2))) if month_number else None
        )
        return ParsedDate(parsed) if parsed else None
    if (match := _SERIAL.match(text)) and _is_excel_serial(int(match.group(1)), today):
        return ParsedDate(EXCEL_EPOCH + timedelta(days=int(match.group(1))), is_serial=True)
    return None


def _is_excel_serial(number: int, today: date) -> bool:
    """A bare 4-digit year (e.g. 1985) is not treated as an Excel serial number."""
    looks_like_year = EARLIEST_PLAUSIBLE.year <= number <= today.year
    return 1 <= number <= MAX_EXCEL_SERIAL and not looks_like_year


def implausibility(parsed: date, today: date, *, birth: bool) -> str | None:
    """Reason a parsed date is implausible, if any."""
    if parsed > today:
        return "in the future"
    if parsed < EARLIEST_PLAUSIBLE:
        return "before 1900"
    if birth:
        age = today.year - parsed.year - ((today.month, today.day) < (parsed.month, parsed.day))
        if age < MIN_AGE_YEARS or age > MAX_AGE_YEARS:
            return f"age would be {age}"
    return None


def _resolve_order(values: Sequence[str], default: Order) -> tuple[Order, Severity | None]:
    """Column order, plus the DAT-003 severity for ambiguous values (None = proven order)."""
    evidence = column_order_evidence(values)
    if len(evidence) == 1:
        return next(iter(evidence)), None
    return default, Severity.WARNING if evidence else Severity.INFO


def _clean_field(
    records: Sequence[Record], field: str, settings: PipelineSettings, log: StageLog
) -> list[Record]:
    birth = field == "birth_date"
    order, ambiguity = _resolve_order([r.get(field) for r in records], settings.date_order)
    cleaned: list[Record] = []
    for record in records:
        old = record.get(field)
        if not old:
            cleaned.append(record)
            continue
        values = dict(record.values)
        if old.strip() in EXCEL_SENTINELS:
            values[field] = ""
            log.change(record, field, old, "", "DAT-005")
            log.issue(record, field, "DAT-005", original_value=old, detail="Excel empty-date value")
            cleaned.append(record.with_values(values))
            continue
        parsed = parse_date(old, order, settings.today, birth=birth)
        if parsed is None:
            log.issue(record, field, "DAT-004", original_value=old)
            cleaned.append(record)
            continue
        values[field] = parsed.value.isoformat()
        log.change(record, field, old, values[field], "DAT-002" if parsed.is_serial else "DAT-001")
        if parsed.ambiguous and ambiguity is not None:
            log.issue(
                record,
                field,
                "DAT-003",
                severity=ambiguity,
                original_value=old,
                detail=f"read as {order}",
            )
        reason = implausibility(parsed.value, settings.today, birth=birth)
        if reason:
            log.issue(record, field, "DAT-005", original_value=old, detail=reason)
        cleaned.append(record.with_values(values))
    return cleaned


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    log = StageLog()
    current = list(records)
    for field in DATE_FIELDS:
        current = _clean_field(current, field, settings, log)
    return log.result(current)
