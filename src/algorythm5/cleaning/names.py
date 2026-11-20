"""Stage 5a: names — full-name split, titles, casing (incl. Albanian letters), suspicious names."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache

from algorythm5.cleaning.data import load_lines, load_pairs
from algorythm5.cleaning.models import PipelineSettings, Record, StageResult
from algorythm5.cleaning.rules import StageLog

# Fields that get the casing fix outside the name fields (no particle handling there).
CASED_TEXT_FIELDS = ("city", "street", "company")
MAX_CERTAIN_NAME_TOKENS = 3
MIN_LETTERS_AFTER_MC = 2
_SUFFIXES = {"jr": "Jr.", "sr": "Sr.", "ii": "II", "iii": "III", "iv": "IV"}
_ROMAN_SUFFIXES = frozenset({"ii", "iii", "iv"})
_ALLOWED_SYMBOLS = frozenset(" -'.")
_LOST_CHARACTERS = frozenset("?�")
_INITIAL = re.compile(r"^\w\.?$")


@dataclass(frozen=True, slots=True)
class SplitName:
    first: str
    last: str
    uncertain: bool


@cache
def _titles() -> dict[str, str]:
    return dict(load_pairs("name_titles.csv"))


@cache
def _particles() -> frozenset[str]:
    return frozenset(load_lines("name_particles.txt"))


@cache
def _mac_surnames() -> frozenset[str]:
    return frozenset(load_lines("mac_surnames.txt"))


def _upper_first_letter(text: str) -> str:
    for index, ch in enumerate(text):
        if ch.isalpha():
            return text[:index] + ch.upper() + text[index + 1 :]
    return text


def _capitalize_segment(segment: str) -> str:
    lower = segment.lower()
    letters = re.sub(r"[^\w]", "", lower)
    if len(lower) >= 3 and lower[1] == "'" and lower[0].isalpha():  # O'Brien, D'Angelo
        return lower[0].upper() + "'" + _upper_first_letter(lower[2:])
    if lower.startswith("mc") and len(letters) >= 2 + MIN_LETTERS_AFTER_MC:
        return "Mc" + _upper_first_letter(lower[2:])
    if letters in _mac_surnames():
        return "Mac" + _upper_first_letter(lower[3:])
    # Only the first letter: keeps Albanian digraphs right (XHEMAL -> Xhemal).
    return _upper_first_letter(lower)


def needs_casing(value: str) -> bool:
    """Casing is fixed only for ALL-CAPS or all-lowercase values; mixed case is intentional."""
    return value.isupper() or value.islower()


def fix_case(value: str, *, particles: bool = True) -> str:
    """Title-case an all-caps/all-lowercase name. Mixed-case input is returned unchanged."""
    if not needs_casing(value):
        return value
    words = value.split(" ")
    result: list[str] = []
    for position, word in enumerate(words):
        bare = word.lower().rstrip(".")
        if particles and position > 0 and bare in _particles():
            result.append(word.lower())
        elif position > 0 and bare in _ROMAN_SUFFIXES:
            result.append(word.upper())
        else:
            result.append("-".join(_capitalize_segment(part) for part in word.split("-")))
    return " ".join(result)


def normalize_suffix(value: str) -> str:
    """Write a trailing Jr/Sr/II/III/IV in its canonical form."""
    words = value.split(" ")
    if len(words) < 2:
        return value
    canonical = _SUFFIXES.get(words[-1].lower().rstrip("."))
    return " ".join([*words[:-1], canonical]) if canonical else value


def extract_title(value: str) -> tuple[str | None, str]:
    """Split a leading title off a name: 'Dr. Ana Hoxha' -> ('Dr.', 'Ana Hoxha')."""
    words = value.split(" ")
    if len(words) < 2:
        return None, value
    first = words[0]
    key = first.lower().rstrip(".")
    canonical = _titles().get(key)
    if canonical is None:
        return None, value
    if len(key) == 1 and not first.endswith("."):  # 'Z' alone may be an initial; 'Z.' is a title
        return None, value
    return canonical, " ".join(words[1:])


def canonical_title(value: str) -> str:
    return _titles().get(value.lower().rstrip("."), value)


def split_full_name(full_name: str) -> SplitName:
    """'HOXHA, ANA' -> (ANA, HOXHA); 'Ana Maria Hoxha' -> (Ana, Maria Hoxha)."""
    if "," in full_name:
        last, _, first = full_name.partition(",")
        first, last = first.strip(), last.strip()
        return SplitName(first, last, uncertain=not first or not last)
    tokens = full_name.split()
    if not tokens:
        return SplitName("", "", uncertain=False)
    uncertain = len(tokens) == 1 or len(tokens) > MAX_CERTAIN_NAME_TOKENS
    return SplitName(tokens[0], " ".join(tokens[1:]), uncertain)


def suspicious_reason(value: str) -> str | None:
    """Rule code for a name that needs a human look, if any."""
    if any(ch in _LOST_CHARACTERS for ch in value):
        return "NAM-007"
    if any(not (ch.isalpha() or ch in _ALLOWED_SYMBOLS) for ch in value):
        return "NAM-005"
    return None


def _apply_full_name(record: Record, values: dict[str, str], log: StageLog) -> None:
    full = values.get("full_name", "")
    if not full:
        return
    title, full = extract_title(full)
    if title and not values.get("salutation"):
        values["salutation"] = title
        log.change(record, "salutation", "", title, "NAM-002")
    split = split_full_name(full)
    for field, part in (("first_name", split.first), ("last_name", split.last)):
        if part and not values.get(field):  # separately mapped fields win
            values[field] = part
            log.change(record, field, "", part, "NAM-004")
    if split.uncertain:
        log.issue(
            record,
            "full_name",
            "NAM-008",
            original_value=values["full_name"],
            suggestion=f"First name: {split.first or '-'} / Last name: {split.last or '-'}",
        )


def _apply_titles(record: Record, values: dict[str, str], log: StageLog) -> None:
    salutation = values.get("salutation", "")
    if salutation:
        values["salutation"] = canonical_title(salutation)
        log.change(record, "salutation", salutation, values["salutation"], "NAM-002")
    first = values.get("first_name", "")
    title, rest = extract_title(first)
    if title is None:
        return
    values["first_name"] = rest
    log.change(record, "first_name", first, rest, "NAM-002")
    if not values.get("salutation"):
        values["salutation"] = title
        log.change(record, "salutation", "", title, "NAM-002")


def _apply_casing(record: Record, values: dict[str, str], log: StageLog) -> None:
    for field in ("first_name", "last_name"):
        old = values.get(field, "")
        values[field] = fix_case(old)
        log.change(record, field, old, values[field], "NAM-001")
    last = values.get("last_name", "")
    values["last_name"] = normalize_suffix(last)
    log.change(record, "last_name", last, values["last_name"], "NAM-003")
    for field in CASED_TEXT_FIELDS:
        old = values.get(field, "")
        if old:
            values[field] = fix_case(old, particles=False)
            log.change(record, field, old, values[field], "NAM-001")


def _flag_suspicious(record: Record, values: dict[str, str], log: StageLog) -> None:
    first, last = values.get("first_name", ""), values.get("last_name", "")
    for field, value in (("first_name", first), ("last_name", last)):
        code = suspicious_reason(value) if value else None
        if code:
            log.issue(record, field, code, original_value=value)
    if first and last and first.casefold() == last.casefold():
        log.issue(record, "last_name", "NAM-006", original_value=f"{first} {last}")
    if first and _INITIAL.match(first):
        log.issue(record, "first_name", "NAM-009", original_value=first)


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    del settings
    log = StageLog()
    cleaned: list[Record] = []
    for record in records:
        values = dict(record.values)
        _apply_full_name(record, values, log)
        _apply_titles(record, values, log)
        _apply_casing(record, values, log)
        _flag_suspicious(record, values, log)
        cleaned.append(record.with_values(values))
    return log.result(cleaned)
