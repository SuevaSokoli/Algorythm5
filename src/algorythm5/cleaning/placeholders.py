"""Stage 4: clear values that only *look* filled in (n/a, 000000, test@test.com, ...)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import cache

from algorythm5.cleaning.data import load_lines, load_pairs
from algorythm5.cleaning.models import PipelineSettings, Record, StageResult
from algorythm5.cleaning.rules import StageLog

BOOLEAN_FIELDS = frozenset({"opt_out", "consent"})
PHONE_FIELDS = frozenset({"phone", "mobile"})
MIN_REPEATED_LENGTH = 3
MIN_FAKE_PHONE_DIGITS = 5
MIN_SEQUENCE_DIGITS = 6
_ASCENDING = "01234567890123456789"
_DESCENDING = "98765432109876543210"

_FAKE_EMAILS = frozenset(
    {"test@test.com", "a@a.com", "no@email.com", "email@email.com", "none@none.com"}
)
_FAKE_EMAIL_PATTERNS = (
    re.compile(r"^(noemail|no-email|none|nomail)@.*$"),
    re.compile(r"^x@x\..*$"),
    re.compile(r"^.*@example\.[a-z.]+$"),
    re.compile(r"^test@test\..*$"),
)
_SURROUNDING_PUNCTUATION = " .,;:!?\"'()[]{}"


@cache
def _placeholder_set() -> frozenset[str]:
    return frozenset(item.lower() for item in load_lines("placeholders.txt"))


@cache
def _boolean_terms() -> frozenset[str]:
    return frozenset(term.lower() for term, _ in load_pairs("optout_terms.csv"))


def _is_repeated_character(value: str) -> bool:
    return len(value) >= MIN_REPEATED_LENGTH and len(set(value)) == 1


def _is_fake_phone(value: str) -> bool:
    if not re.fullmatch(r"[\d\s+().\-/]+", value):
        return False
    digits = re.sub(r"\D", "", value)
    if len(digits) >= MIN_FAKE_PHONE_DIGITS and len(set(digits)) == 1:
        return True
    return len(digits) >= MIN_SEQUENCE_DIGITS and (digits in _ASCENDING or digits in _DESCENDING)


def is_fake_email(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in _FAKE_EMAILS or any(p.match(lowered) for p in _FAKE_EMAIL_PATTERNS)


def is_placeholder(value: str, field: str) -> bool:
    """True when `value` carries no information for `field`."""
    lowered = value.strip().lower()
    if not lowered:
        return False
    if field in BOOLEAN_FIELDS and lowered in _boolean_terms():
        return False  # yes/no/0/x are real answers in opt-out and consent columns
    stripped = lowered.strip(_SURROUNDING_PUNCTUATION)
    if lowered in _placeholder_set() or (stripped and stripped in _placeholder_set()):
        return True
    if _is_repeated_character(lowered.replace(" ", "")):
        return True
    if field in PHONE_FIELDS and _is_fake_phone(lowered):
        return True
    return field == "email" and is_fake_email(lowered)


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    del settings
    log = StageLog()
    cleaned: list[Record] = []
    for record in records:
        values = dict(record.values)
        for field, value in record.values.items():
            if is_placeholder(value, field):
                values[field] = ""
                log.change(record, field, value, "", "PLC-001")
        extras = dict(record.extras)
        for header, value in record.extras.items():
            if is_placeholder(value, header):
                extras[header] = ""
                log.change(record, header, value, "", "PLC-001")
        cleaned.append(record.with_values(values, extras))
    return log.result(cleaned)
