"""Stage 3: Unicode/whitespace normalization of every cell, plus shared folding helpers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

import ftfy

from algorythm5.cleaning.models import PipelineSettings, Record, StageResult, Step
from algorythm5.cleaning.rules import StageLog

NAME_FIELDS = frozenset({"first_name", "last_name", "full_name", "salutation"})
# Fields that may hold several values; line breaks become a "; " separator there.
MULTI_VALUE_FIELDS = frozenset({"email", "phone", "mobile"})

# Zero-width space/non-joiner/joiner, BOM, word joiner, soft hyphen.
_INVISIBLE = frozenset(chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060, 0x00AD))
# No-break, figure and narrow no-break spaces.
_SPACE_LIKE = frozenset(chr(c) for c in (0x00A0, 0x2007, 0x202F))
# Typographic apostrophes and accents typed instead of an apostrophe.
_APOSTROPHES = str.maketrans(dict.fromkeys((0x2019, 0x2018, 0x00B4, 0x0060, 0x02BC), "'"))
_LINE_BREAKS = re.compile(r"\s*[\r\n]+\s*")
_SPACES = re.compile(r"[ \t]+")


def fold(value: str) -> str:
    """Case- and accent-insensitive comparison key (e.g. 'Çela' -> 'cela'). Never for output."""
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split())


def normalize_header(header: str) -> str:
    """Header comparison key: folded, punctuation and underscores turned into spaces."""
    return " ".join(re.sub(r"[\W_]+", " ", fold(header)).split())


def repair_mojibake(value: str) -> str:
    """Fix double-encoded text (e.g. 'Ã«' -> 'ë') and nothing else."""
    if value.isascii():
        return value
    return ftfy.fix_text(
        value,
        unescape_html=False,
        remove_terminal_escapes=False,
        fix_latin_ligatures=False,
        fix_character_width=False,
        uncurl_quotes=False,
        fix_line_breaks=False,
        remove_control_chars=False,
        normalization=None,
        explain=False,
    )


def _remove_invisible(value: str) -> str:
    chars: list[str] = []
    for ch in value:
        if ch in _INVISIBLE:
            continue
        if ch in _SPACE_LIKE:
            chars.append(" ")
        elif ch in "\r\n\t" or unicodedata.category(ch) != "Cc":
            chars.append(ch)
    return "".join(chars)


def _tidy_whitespace(value: str, multi_value: bool) -> str:
    separator = "; " if multi_value else " "
    value = _LINE_BREAKS.sub(separator, value.strip())
    return _SPACES.sub(" ", value).strip()


def normalize_value(value: str, field: str) -> tuple[str, tuple[Step, ...]]:
    """Apply the text rules in order; return the new value and the steps that changed it."""
    steps: list[Step] = []

    def record(code: str, new: str) -> None:
        nonlocal value
        if new != value:
            steps.append(Step(code, value, new))
            value = new

    record("TXT-001", repair_mojibake(value))
    value = unicodedata.normalize("NFC", value)  # silent: only the normal form differs
    record("TXT-002", _remove_invisible(value))
    record("TXT-003", _tidy_whitespace(value, field in MULTI_VALUE_FIELDS))
    if field in NAME_FIELDS:
        record("TXT-004", value.translate(_APOSTROPHES))
    return value, tuple(steps)


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    """Normalize every canonical and passthrough cell."""
    del settings  # stage contract; text rules have no settings
    log = StageLog()
    cleaned: list[Record] = []
    for record in records:
        values = dict(record.values)
        for field, old in record.values.items():
            values[field], steps = normalize_value(old, field)
            log.steps(record, field, steps)
        extras = dict(record.extras)
        for header, old in record.extras.items():
            extras[header], steps = normalize_value(old, header)
            log.steps(record, header, steps)
        cleaned.append(record.with_values(values, extras))
    return log.result(cleaned)
