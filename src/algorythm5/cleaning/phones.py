"""Stage 5b: phones — split, extensions, Excel damage, recovery, E.164, primary selection."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType

from algorythm5.cleaning.models import Flag, PipelineSettings, Record, StageResult, Step
from algorythm5.cleaning.placeholders import is_placeholder
from algorythm5.cleaning.rules import StageLog
from algorythm5.cleaning.text import fold

MIN_NUMBER_DIGITS = 6
LIST_SEPARATOR = "; "

_SPLIT = re.compile(r"\s*(?:[/;,|\n]|\bor\b|\bose\b)\s*", re.IGNORECASE)
_EXTENSION = re.compile(
    r"(?:\s*(?:ext\.?|x|#|int\.?|brendshme)\s*:?\s*)(\d{1,6})\s*$", re.IGNORECASE
)
_FLOAT_ARTIFACT = re.compile(r"^(\+?\d+)\.0+$")
_SCIENTIFIC = re.compile(r"^\d+(?:[.,]\d+)?[eE]\+?\d+$")
_FORMATTING = re.compile(r"[\s.\-()']")
_MOBILE_TYPES = frozenset({PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE})
E164_LIST = re.compile(r"^\+\d{6,15}(?:; \+\d{6,15})*$")

# Country names (EN/AL, folded) that override the default region per row.
_COUNTRY_REGIONS = {
    "albania": "AL", "shqiperi": "AL", "shqiperia": "AL", "shqiperise": "AL",
    "kosovo": "XK", "kosova": "XK", "kosove": "XK", "north macedonia": "MK", "macedonia": "MK",
    "maqedonia": "MK", "maqedoni": "MK", "maqedonia e veriut": "MK", "montenegro": "ME",
    "mali i zi": "ME", "italy": "IT", "italia": "IT", "itali": "IT", "greece": "GR",
    "greqi": "GR", "greqia": "GR", "germany": "DE", "gjermani": "DE", "gjermania": "DE",
    "united kingdom": "GB", "uk": "GB", "anglia": "GB", "united states": "US", "usa": "US",
    "shba": "US", "switzerland": "CH", "zvicer": "CH", "zvicra": "CH", "france": "FR",
    "franca": "FR", "austria": "AT",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class ParsedPart:
    original: str
    e164: str | None
    is_mobile: bool


@dataclass(slots=True)
class PhoneOutcome:
    phone: str = ""
    others: list[str] = field(default_factory=list)
    extension: str = ""
    steps: list[Step] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)


def region_for(country: str, default_region: str) -> str:
    """ISO region from a country cell ('AL', 'Kosovë', 'Italy'), else the default."""
    key = fold(country)
    if len(key) == 2 and key.upper() in phonenumbers.SUPPORTED_REGIONS:
        return key.upper()
    return _COUNTRY_REGIONS.get(key, default_region)


def split_numbers(value: str) -> list[str]:
    """Split on separators, re-joining fragments too short to be a number ('04/2234567')."""
    parts = [p for p in _SPLIT.split(value) if p.strip()]
    merged: list[str] = []
    for part in parts:
        digits = len(re.sub(r"\D", "", part))
        if merged and (digits < MIN_NUMBER_DIGITS or _digit_count(merged[-1]) < MIN_NUMBER_DIGITS):
            merged[-1] = f"{merged[-1]}{part}"
        else:
            merged.append(part.strip())
    return merged


def _digit_count(value: str) -> int:
    return len(re.sub(r"\D", "", value))


def _try_parse(candidate: str, region: str) -> phonenumbers.PhoneNumber | None:
    try:
        number = phonenumbers.parse(candidate, region)
    except NumberParseException:
        return None
    return number if phonenumbers.is_valid_number(number) else None


def parse_part(part: str, region: str, outcome: PhoneOutcome) -> ParsedPart | None:
    """Clean one number; append steps/flags to `outcome`. None when unusable."""
    original = part
    current = part.lstrip("'")
    match = _EXTENSION.search(current)
    if match and _digit_count(current[: match.start()]) >= MIN_NUMBER_DIGITS:
        outcome.extension = outcome.extension or match.group(1)
        new = current[: match.start()].strip()
        outcome.steps.append(Step("PHN-005", current, new))
        current = new
    if _SCIENTIFIC.match(current):
        outcome.flags.append(Flag("PHN-002", original_value=original))
        return None
    artifact = _FLOAT_ARTIFACT.match(current)
    if artifact:
        outcome.steps.append(Step("PHN-003", current, artifact.group(1)))
        current = artifact.group(1)
    compact = _FORMATTING.sub("", current)
    if compact.startswith("00"):
        outcome.steps.append(Step("PHN-008", current, "+" + compact[2:]))
        compact = "+" + compact[2:]
    if not re.fullmatch(r"\+?\d+", compact):
        _record_invalid(original, outcome)
        return None
    number = _try_parse(compact, region)
    if number is None and not compact.startswith(("+", "0")):
        for recovered in ("+" + compact, "0" + compact):
            number = _try_parse(recovered, region)
            if number is not None:
                outcome.steps.append(Step("PHN-004", compact, recovered))
                break
    if number is None:
        _record_invalid(original, outcome)
        return None
    e164 = phonenumbers.format_number(number, PhoneNumberFormat.E164)
    outcome.steps.append(Step("PHN-001", current, e164))
    mobile = phonenumbers.number_type(number) in _MOBILE_TYPES
    return ParsedPart(original, e164, mobile)


def _record_invalid(original: str, outcome: PhoneOutcome) -> None:
    outcome.flags.append(Flag("PHN-007", original_value=original))
    outcome.others.append(original)


def clean_phone_cell(value: str, region: str) -> PhoneOutcome:
    """Turn a raw phone cell into a primary E.164 number plus the rest."""
    outcome = PhoneOutcome()
    if not value:
        return outcome
    parts = split_numbers(value)
    if len(parts) > 1:
        outcome.steps.append(Step("PHN-006", value, LIST_SEPARATOR.join(parts)))
    parsed: list[ParsedPart] = []
    for part in parts:
        if is_placeholder(part, "phone"):
            outcome.steps.append(Step("PLC-001", part, ""))
            continue
        result = parse_part(part, region, outcome)
        if result is not None:
            parsed.append(result)
    valid = list(dict.fromkeys(p.e164 for p in parsed if p.e164))
    mobiles = [p.e164 for p in parsed if p.is_mobile and p.e164]
    primary = mobiles[0] if mobiles else (valid[0] if valid else "")
    outcome.phone = primary
    invalid = outcome.others
    outcome.others = [n for n in valid if n != primary] + invalid
    return outcome


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    log = StageLog()
    cleaned: list[Record] = []
    for record in records:
        values = dict(record.values)
        raw = values.get("phone", "")
        region = region_for(values.get("country", ""), settings.default_phone_region)
        outcome = clean_phone_cell(raw, region)
        log.steps(record, "phone", outcome.steps)
        log.flags(record, "phone", outcome.flags)
        values["phone"] = outcome.phone
        existing_other = [v for v in values.get("phone_other", "").split(LIST_SEPARATOR) if v]
        values["phone_other"] = LIST_SEPARATOR.join(dict.fromkeys(existing_other + outcome.others))
        if outcome.extension and not values.get("phone_extension"):
            values["phone_extension"] = outcome.extension
            log.change(record, "phone_extension", "", outcome.extension, "PHN-005")
        cleaned.append(record.with_values(values))
    return log.result(cleaned)
