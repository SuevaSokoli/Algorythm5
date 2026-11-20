"""Stage 2: map source columns to canonical fields, then build records from the table.

Column order never matters: every column is matched by its header (exact, numbered or fuzzy
synonym) and checked against its values. Values win when the header is missing or wrong.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from functools import cache

from rapidfuzz import fuzz, process

from algorythm5.cleaning.data import load_lines, load_pairs
from algorythm5.cleaning.dates import parse_date
from algorythm5.cleaning.models import ColumnMapping, Record, StageResult, Table
from algorythm5.cleaning.rules import StageLog
from algorythm5.cleaning.text import fold, normalize_header

CANONICAL_FIELDS: tuple[str, ...] = (
    "customer_id", "salutation", "first_name", "last_name", "full_name", "email", "phone",
    "mobile", "phone_extension", "company", "street", "postal_code", "city", "country",
    "birth_date", "signup_date", "opt_out", "consent", "notes",
)  # fmt: skip
# Fields that may receive several source columns (combined in column order).
MULTI_SOURCE_FIELDS = frozenset({"email", "phone", "mobile"})
CONTACT_FIELDS = frozenset({"email", "phone", "mobile"})
NAME_FIELDS = frozenset({"first_name", "last_name", "full_name"})
CONFIDENCE_EXACT = 1.0
CONFIDENCE_FUZZY = 0.8
CONFIDENCE_SNIFFED = 0.6
FUZZY_HEADER_THRESHOLD = 90
SNIFF_SAMPLE_SIZE = 200
SNIFF_MIN_SHARE = 0.8
# A header is "wrong" when under 20 % of its values fit it and another kind clearly dominates.
MISMATCH_MAX_OWN_SHARE = 0.2
MIN_SAMPLE_FOR_MISMATCH = 3
# Name columns: most values are 1-4 words of letters and enough of them are known first names.
KNOWN_FIRST_NAME_SHARE = 0.3
MULTI_WORD_SHARE = 0.6
LAST_NAME_MIN_DISTINCT_SHARE = 0.5
MAX_NAME_WORDS = 4
MAX_NAME_CHARS = 40
MIN_NAME_LETTERS = 2
COMBINE_SEPARATOR = "; "

# Reasons shown to the user next to each proposed target (texts live in the UI strings).
REASON_SYNONYM = "synonym"
REASON_NUMBERED = "numbered"
REASON_FUZZY = "fuzzy"
REASON_CONTENT = "content"
REASON_HEADER_MISMATCH = "header_mismatch"
REASON_MERGED_NAMES = "merged_names"
REASON_DUPLICATE = "duplicate_column"
REASON_UNMATCHED = "unmatched"
REASON_MANUAL = "manual"
REASONS = (
    REASON_SYNONYM, REASON_NUMBERED, REASON_FUZZY, REASON_CONTENT, REASON_HEADER_MISMATCH,
    REASON_MERGED_NAMES, REASON_DUPLICATE, REASON_UNMATCHED, REASON_MANUAL,
)  # fmt: skip

KIND_EMAIL, KIND_DATE, KIND_PHONE, KIND_NAME = "email", "date", "phone", "name"
# Value kind each field expects; used to spot headers that contradict their values.
EXPECTED_KIND = {
    "email": KIND_EMAIL,
    "phone": KIND_PHONE,
    "mobile": KIND_PHONE,
    "birth_date": KIND_DATE,
    "signup_date": KIND_DATE,
    "first_name": KIND_NAME,
    "last_name": KIND_NAME,
    "full_name": KIND_NAME,
}
# Kinds that are recognised reliably enough to overrule a header.
DECISIVE_KINDS = (KIND_EMAIL, KIND_DATE, KIND_PHONE)

_TRAILING_NUMBER = re.compile(r"\s*\d+$")
_EMAIL_LIKE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_LIKE = re.compile(r"^[\d\s+()./\-]+$")
_NAME_LIKE = re.compile(r"^[^\W\d_]+(?:[ ,'â€™\-.]+[^\W\d_]+)*\.?$")
_MIN_PHONE_DIGITS, _MAX_PHONE_DIGITS = 6, 15


class InvalidMappingError(ValueError):
    """A user-supplied mapping is malformed (unknown field, duplicate single field, bad index)."""


@cache
def _synonyms() -> dict[str, str]:
    return {normalize_header(syn): field for syn, field in load_pairs("column_synonyms.csv")}


@cache
def _first_names() -> frozenset[str]:
    return frozenset(load_lines("first_names.txt"))


def is_known_header(header: str) -> bool:
    return normalize_header(header) in _synonyms()


# Sniffing asks "is this a date at all", not "is it plausible", so "today" is far away.
_SNIFF_TODAY = date(2100, 1, 1)


def _share(values: Sequence[str], predicate: Callable[[str], bool]) -> float:
    return sum(1 for v in values if predicate(v)) / len(values) if values else 0.0


def looks_like_email(value: str) -> bool:
    return bool(_EMAIL_LIKE.match(value))


def looks_like_date(value: str) -> bool:
    return parse_date(value, "DMY", _SNIFF_TODAY) is not None


def looks_like_phone(value: str) -> bool:
    digits = len(re.sub(r"\D", "", value))
    return bool(_PHONE_LIKE.match(value)) and _MIN_PHONE_DIGITS <= digits <= _MAX_PHONE_DIGITS


def looks_like_name(value: str) -> bool:
    words = value.replace(",", " ").split()
    return (
        len(value) <= MAX_NAME_CHARS
        and 1 <= len(words) <= MAX_NAME_WORDS
        and bool(_NAME_LIKE.match(value))
        and sum(ch.isalpha() for ch in value) >= MIN_NAME_LETTERS
    )


def _first_name_token(value: str) -> str:
    """The token that should be a first name: 'Hoxha, Ana' -> ana; 'Ana Hoxha' -> ana."""
    if "," in value:
        value = value.partition(",")[2]
    words = value.split()
    return fold(words[0]).rstrip(".") if words else ""


def _known_first_name_share(values: Sequence[str]) -> float:
    return _share(values, lambda v: _first_name_token(v) in _first_names())


def looks_like_data_row(cells: Sequence[str]) -> bool:
    """True when a row holds customer values (emails, phones, dates), i.e. it is no header."""
    return any(
        looks_like_email(c.strip()) or looks_like_phone(c.strip()) for c in cells if c.strip()
    )


def _sample(table: Table, index: int) -> list[str]:
    values = (row.cells[index].strip() for row in table.rows)
    return [v for v in values if v][:SNIFF_SAMPLE_SIZE]


def _kind_shares(sample: Sequence[str]) -> dict[str, float]:
    return {
        KIND_EMAIL: _share(sample, looks_like_email),
        KIND_DATE: _share(sample, looks_like_date),
        KIND_PHONE: _share(sample, looks_like_phone),
        KIND_NAME: _share(sample, looks_like_name),
    }


def _decisive_kind(shares: dict[str, float]) -> str | None:
    return next((k for k in DECISIVE_KINDS if shares[k] >= SNIFF_MIN_SHARE), None)


def _multi_word_share(sample: Sequence[str]) -> float:
    return _share(sample, lambda v: len(v.replace(",", " ").split()) > 1)


@dataclass(slots=True)
class _Proposal:
    field: str | None = None
    method: str = "auto"
    confidence: float = 0.0
    reason: str = REASON_UNMATCHED


class _Proposer:
    """Builds a proposal per column; single-value fields are given out at most once."""

    def __init__(self, table: Table) -> None:
        self.table = table
        self.proposals = [_Proposal() for _ in table.headers]
        self.samples = [_sample(table, i) for i in range(len(table.headers))]
        self.shares = [_kind_shares(s) for s in self.samples]

    def taken(self) -> set[str]:
        return {p.field for p in self.proposals if p.field}

    def assign(self, index: int, field: str, method: str, confidence: float, reason: str) -> bool:
        if field in self.taken() and field not in MULTI_SOURCE_FIELDS:
            if self.proposals[index].field is None:
                self.proposals[index].reason = REASON_DUPLICATE
            return False
        self.proposals[index] = _Proposal(field, method, confidence, reason)
        return True

    def unmatched(self) -> list[int]:
        return [
            i
            for i, p in enumerate(self.proposals)
            if p.field is None and p.reason != REASON_DUPLICATE
        ]

    # ----- passes -----

    def by_header(self) -> None:
        keys = [normalize_header(h) for h in self.table.headers]
        synonyms = _synonyms()
        for index, key in enumerate(keys):
            if key in synonyms:
                self.assign(index, synonyms[key], "auto", CONFIDENCE_EXACT, REASON_SYNONYM)
        for index, key in enumerate(keys):
            numbered = _TRAILING_NUMBER.sub("", key)  # "Email 2", "Tel2" -> email, tel
            if self.proposals[index].field is None and numbered != key and numbered in synonyms:
                field = synonyms[numbered]
                self.assign(index, field, "auto", CONFIDENCE_FUZZY, REASON_NUMBERED)
        for index, key in enumerate(keys):
            if self.proposals[index].field is not None or not key:
                continue
            match = process.extractOne(
                key, list(synonyms), scorer=fuzz.ratio, score_cutoff=FUZZY_HEADER_THRESHOLD
            )
            if match:
                self.assign(index, synonyms[match[0]], "auto", CONFIDENCE_FUZZY, REASON_FUZZY)

    def check_headers_against_values(self) -> None:
        """Re-map columns whose values clearly contradict their header (e.g. emails under 'Tel')."""
        for index, proposal in enumerate(self.proposals):
            expected = EXPECTED_KIND.get(proposal.field or "")
            sample, shares = self.samples[index], self.shares[index]
            if expected is None or len(sample) < MIN_SAMPLE_FOR_MISMATCH:
                continue
            actual = _decisive_kind(shares)
            if actual is None or actual == expected or shares[expected] >= MISMATCH_MAX_OWN_SHARE:
                continue
            self.proposals[index] = _Proposal()
            field = self._field_for_kind(actual)
            if field:
                self.assign(index, field, "sniffed", CONFIDENCE_SNIFFED, REASON_HEADER_MISMATCH)

    def split_merged_names(self) -> None:
        """A 'first name' column holding 'Ana Hoxha' with no last-name column is a full name."""
        taken = self.taken()
        if {"last_name", "full_name"} & taken:
            return
        for index, proposal in enumerate(self.proposals):
            sample = self.samples[index]
            if (
                proposal.field == "first_name"
                and sample
                and _multi_word_share(sample) >= MULTI_WORD_SHARE
            ):
                self.proposals[index] = _Proposal(
                    "full_name", "sniffed", CONFIDENCE_SNIFFED, REASON_MERGED_NAMES
                )

    def by_values(self) -> None:
        for index in self.unmatched():
            field = self._field_for_kind(_decisive_kind(self.shares[index]))
            if field:
                self.assign(index, field, "sniffed", CONFIDENCE_SNIFFED, REASON_CONTENT)
        self._names_by_values()

    def _field_for_kind(self, kind: str | None) -> str | None:
        taken = self.taken()
        if kind == KIND_EMAIL:
            return "email"
        if kind == KIND_PHONE:
            return "phone"
        if kind == KIND_DATE:
            return next((f for f in ("signup_date", "birth_date") if f not in taken), None)
        return None

    def _is_name_column(self, index: int) -> bool:
        return bool(self.samples[index]) and self.shares[index][KIND_NAME] >= SNIFF_MIN_SHARE

    def _names_by_values(self) -> None:
        if NAME_FIELDS & self.taken():
            return self._last_name_next_to_first()
        for index in self.unmatched():
            sample = self.samples[index]
            if not self._is_name_column(index):
                continue
            if _known_first_name_share(sample) < KNOWN_FIRST_NAME_SHARE:
                continue
            field = "full_name" if _multi_word_share(sample) >= MULTI_WORD_SHARE else "first_name"
            self.assign(index, field, "sniffed", CONFIDENCE_SNIFFED, REASON_CONTENT)
            break
        return self._last_name_next_to_first()

    def _last_name_next_to_first(self) -> None:
        """Last names cannot be recognised alone; take the name column beside the first names."""
        taken = self.taken()
        if "first_name" not in taken or {"last_name", "full_name"} & taken:
            return
        first = next(i for i, p in enumerate(self.proposals) if p.field == "first_name")
        for index in (first + 1, first - 1):
            if 0 <= index < len(self.proposals) and index in self.unmatched():
                sample = self.samples[index]
                distinct = len({fold(v) for v in sample}) / len(sample) if sample else 0.0
                if self._is_name_column(index) and distinct >= LAST_NAME_MIN_DISTINCT_SHARE:
                    self.assign(index, "last_name", "sniffed", CONFIDENCE_SNIFFED, REASON_CONTENT)
                    return

    def result(self) -> tuple[ColumnMapping, ...]:
        return tuple(
            ColumnMapping(
                index,
                header,
                p.field,
                "sniffed" if p.method == "sniffed" else "auto",
                p.confidence,
                p.reason,
            )
            for index, (header, p) in enumerate(
                zip(self.table.headers, self.proposals, strict=True)
            )
        )


def propose_mapping(table: Table) -> tuple[ColumnMapping, ...]:
    """Suggest a target per column: header synonyms first, then checked against the values."""
    proposer = _Proposer(table)
    proposer.by_header()
    proposer.check_headers_against_values()
    proposer.split_merged_names()
    proposer.by_values()
    return proposer.result()


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


def _log_mapping(mapping: ColumnMapping, log: StageLog) -> None:
    if mapping.target_field is None:
        code = "MAP-004" if mapping.reason == REASON_DUPLICATE else "MAP-002"
        log.issue(None, mapping.header, code)
    elif mapping.reason in (REASON_HEADER_MISMATCH, REASON_MERGED_NAMES):
        log.issue(None, mapping.header, "MAP-003", detail=mapping.target_field)
    elif mapping.method != "manual":
        log.issue(None, mapping.header, "MAP-001", detail=mapping.target_field)


def build_records(
    table: Table, mappings: Sequence[ColumnMapping], source_file: str = ""
) -> StageResult:
    """Create one Record per row. Mobile values come before phone values (mobile first)."""
    log = StageLog()
    for mapping in mappings:
        _log_mapping(mapping, log)
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
            Record(
                row.record_id,
                row.source_row,
                values,
                extras,
                source_rows=(row.source_row,),
                source_file=source_file,
                source_files=(source_file,),
            )
        )
    return log.result(records)
