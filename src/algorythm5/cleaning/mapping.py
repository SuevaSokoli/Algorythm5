"""Stage 2: map source columns to canonical fields, then build records from the table."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from datetime import date
from functools import cache

from rapidfuzz import fuzz, process

from algorythm5.cleaning.data import load_pairs
from algorythm5.cleaning.dates import parse_date
from algorythm5.cleaning.models import ColumnMapping, Record, StageResult, Table
from algorythm5.cleaning.rules import StageLog
from algorythm5.cleaning.text import normalize_header

CANONICAL_FIELDS: tuple[str, ...] = (
    "customer_id", "salutation", "first_name", "last_name", "full_name", "email", "phone",
    "mobile", "phone_extension", "company", "street", "postal_code", "city", "country",
    "birth_date", "signup_date", "opt_out", "consent", "notes",
)  # fmt: skip
# Fields that may receive several source columns (combined in column order).
MULTI_SOURCE_FIELDS = frozenset({"email", "phone", "mobile"})
CONTACT_FIELDS = frozenset({"email", "phone", "mobile"})
CONFIDENCE_EXACT = 1.0
CONFIDENCE_FUZZY = 0.8
CONFIDENCE_SNIFFED = 0.6
FUZZY_HEADER_THRESHOLD = 90
SNIFF_SAMPLE_SIZE = 200
SNIFF_MIN_SHARE = 0.8
COMBINE_SEPARATOR = "; "

_TRAILING_NUMBER = re.compile(r"\s*\d+$")
_EMAIL_LIKE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_LIKE = re.compile(r"^[\d\s+()./\-]+$")
_MIN_PHONE_DIGITS, _MAX_PHONE_DIGITS = 6, 15


class InvalidMappingError(ValueError):
    """A user-supplied mapping is malformed (unknown field, duplicate single field, bad index)."""


@cache
def _synonyms() -> dict[str, str]:
    return {normalize_header(syn): field for syn, field in load_pairs("column_synonyms.csv")}


def is_known_header(header: str) -> bool:
    return normalize_header(header) in _synonyms()


# Sniffing asks "is this a date at all", not "is it plausible", so "today" is far away.
_SNIFF_TODAY = date(2100, 1, 1)


def _share(values: Sequence[str], predicate: Callable[[str], bool]) -> float:
    return sum(1 for v in values if predicate(v)) / len(values)


def _looks_like_email(value: str) -> bool:
    return bool(_EMAIL_LIKE.match(value))


def _looks_like_date(value: str) -> bool:
    return parse_date(value, "DMY", _SNIFF_TODAY) is not None


def _looks_like_phone(value: str) -> bool:
    digits = len(re.sub(r"\D", "", value))
    return bool(_PHONE_LIKE.match(value)) and _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS


def _sniff(values: Sequence[str], taken: set[str]) -> str | None:
    """Guess a field from cell contents (≥ 80 % of a sample must match)."""
    sample = [v.strip() for v in values if v.strip()][:SNIFF_SAMPLE_SIZE]
    if not sample:
        return None
    if _share(sample, _looks_like_email) >= SNIFF_MIN_SHARE:
        return "email"
    if _share(sample, _looks_like_date) >= SNIFF_MIN_SHARE:
        return next((f for f in ("signup_date", "birth_date") if f not in taken), None)
    if _share(sample, _looks_like_phone) >= SNIFF_MIN_SHARE:
        return "phone"
    return None


def propose_mapping(table: Table) -> tuple[ColumnMapping, ...]:
    """Suggest a target field per column: exact synonym, then fuzzy synonym, then sniffing."""
    headers = table.headers
    targets: list[tuple[str | None, str, float]] = [(None, "auto", 0.0)] * len(headers)
    taken: set[str] = set()

    def assign(index: int, field: str, method: str, confidence: float) -> None:
        if field in taken and field not in MULTI_SOURCE_FIELDS:
            return
        targets[index] = (field, method, confidence)
        taken.add(field)

    normalized = [normalize_header(h) for h in headers]
    for index, key in enumerate(normalized):
        if key in _synonyms():
            assign(index, _synonyms()[key], "auto", CONFIDENCE_EXACT)
    for index, key in enumerate(normalized):
        numbered = _TRAILING_NUMBER.sub("", key)  # "Email 2", "Tel2" -> email, tel
        if targets[index][0] is None and numbered != key and numbered in _synonyms():
            assign(index, _synonyms()[numbered], "auto", CONFIDENCE_FUZZY)
    for index, key in enumerate(normalized):
        if targets[index][0] is None and key:
            match = process.extractOne(
                key, list(_synonyms()), scorer=fuzz.ratio, score_cutoff=FUZZY_HEADER_THRESHOLD
            )
            if match:
                assign(index, _synonyms()[match[0]], "auto", CONFIDENCE_FUZZY)
    for index in range(len(headers)):
        if targets[index][0] is None:
            column = [row.cells[index] for row in table.rows]
            field = _sniff(column, taken)
            if field:
                assign(index, field, "sniffed", CONFIDENCE_SNIFFED)
    return tuple(
        ColumnMapping(index, header, field, "sniffed" if method == "sniffed" else "auto", conf)
        for index, (header, (field, method, conf)) in enumerate(zip(headers, targets, strict=True))
    )


def validate_targets(targets: Iterable[str | None]) -> None:
    """Raise InvalidMappingError for unknown or doubly-used single fields."""
    seen: set[str] = set()
    for target in targets:
        if target is None:
            continue
        if target not in CANONICAL_FIELDS:
            raise InvalidMappingError(f"Unknown field: {target}")
        if target in seen and target not in MULTI_SOURCE_FIELDS:
            raise InvalidMappingError(f"Field used twice: {target}")
        seen.add(target)


def has_contact_field(targets: Iterable[str | None]) -> bool:
    return any(t in CONTACT_FIELDS for t in targets)


def _passthrough_name(header: str) -> str:
    """Passthrough columns never collide with canonical/output field names."""
    return f"{header} (extra)" if header in CANONICAL_FIELDS else header


def build_records(table: Table, mappings: Sequence[ColumnMapping]) -> StageResult:
    """Create one Record per row. Mobile values come before phone values (mobile first)."""
    log = StageLog()
    for mapping in mappings:
        if mapping.target_field is None:
            log.issue(None, mapping.header, "MAP-002")
        elif mapping.method != "manual":
            log.issue(None, mapping.header, "MAP-001", detail=f"{mapping.target_field}")
    records: list[Record] = []
    for row in table.rows:
        collected: dict[str, list[str]] = {}
        extras: dict[str, str] = {}
        for mapping in mappings:
            cell = row.cells[mapping.index]
            if mapping.target_field is None:
                extras[_passthrough_name(mapping.header)] = cell
            else:
                collected.setdefault(mapping.target_field, []).append(cell)
        values = {
            f: COMBINE_SEPARATOR.join(v for v in cells if v.strip())
            for f, cells in collected.items()
        }
        mobile = values.pop("mobile", "")
        phone = values.get("phone", "")
        values["phone"] = COMBINE_SEPARATOR.join(v for v in (mobile, phone) if v)
        records.append(
            Record(row.record_id, row.source_row, values, extras, source_rows=(row.source_row,))
        )
    return log.result(records)
