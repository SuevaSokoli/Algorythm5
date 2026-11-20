"""Stage 5c: emails — unwrap, split, normalize, fix known typos, suggest, validate."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cache

from email_validator import EmailNotValidError, validate_email
from rapidfuzz.distance import Levenshtein
from rapidfuzz.process import extractOne

from algorythm5.cleaning.data import load_lines, load_pairs
from algorythm5.cleaning.models import Flag, PipelineSettings, Record, Severity, StageResult, Step
from algorythm5.cleaning.placeholders import is_fake_email
from algorythm5.cleaning.rules import StageLog

MAX_SUGGESTION_DISTANCE = 2
LIST_SEPARATOR = "; "
INVALID_PREFIX = "INVALID: "
ROLE_LOCAL_PARTS = frozenset(
    {"info", "admin", "office", "sales", "contact", "noreply", "no-reply", "support", "hello"}
)

_DISPLAY_NAME = re.compile(r"[^<>;,]*<([^<>]+)>")
_MAILTO = re.compile(r"mailto:", re.IGNORECASE)
_OBFUSCATED_AT = re.compile(r"\s*[\(\[]\s*at\s*[\)\]]\s*", re.IGNORECASE)
_OBFUSCATED_DOT = re.compile(r"\s*[\(\[]\s*dot\s*[\)\]]\s*", re.IGNORECASE)
_SPACES_AROUND_AT = re.compile(r"\s*@\s*")
_SPACE_BEFORE_DOT = re.compile(r"(?<=\S)\s+\.(?=\S)")
_COMMA_IN_DOMAIN = re.compile(r"(@[\w.-]+),([a-zA-Z]{2,6})(?=$|[\s;/,])")
_SPLIT = re.compile(r"[;,/\s]+")
_EDGE_PUNCTUATION = "\"'.,;:()[]<>"


@dataclass(slots=True)
class EmailOutcome:
    email: str = ""
    others: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)


@cache
def known_domains() -> frozenset[str]:
    return frozenset(load_lines("email_domains.txt"))


@cache
def _typos() -> tuple[dict[str, str], dict[str, str]]:
    domains: dict[str, str] = {}
    tlds: dict[str, str] = {}
    for typo, fix in load_pairs("email_typos.csv"):
        (tlds if typo.startswith(".") else domains)[typo] = fix
    return domains, tlds


def is_valid_email(address: str) -> bool:
    try:
        validate_email(address, check_deliverability=False)
    except EmailNotValidError:
        return False
    return True


def fix_known_typo(address: str) -> str:
    """Exact-lookup typo fix of the domain or its TLD; unchanged if nothing matches."""
    local, at, domain = address.rpartition("@")
    if not at:
        return address
    domain_typos, tld_typos = _typos()
    if domain in domain_typos:
        return f"{local}@{domain_typos[domain]}"
    for typo, fix in tld_typos.items():
        if domain.endswith(typo):
            return f"{local}@{domain[: -len(typo)]}{fix}"
    return address


def fix_structure(address: str) -> str:
    """Repair doubled separators and a trailing dot in the domain."""
    fixed = re.sub(r"@{2,}", "@", address)
    fixed = re.sub(r"\.{2,}", ".", fixed)
    local, at, domain = fixed.rpartition("@")
    return f"{local}{at}{domain.rstrip('.')}" if at else fixed


def suggest_domain(address: str) -> str | None:
    """'ana@gmaul.com' -> 'ana@gmail.com' when the domain is close to a known one."""
    local, at, domain = address.rpartition("@")
    if not at or not domain or domain in known_domains():
        return None
    match = extractOne(
        domain, known_domains(), scorer=Levenshtein.distance, score_cutoff=MAX_SUGGESTION_DISTANCE
    )
    return f"{local}@{match[0]}" if match else None


def _unwrap(value: str) -> str:
    unwrapped = _DISPLAY_NAME.sub(r"\1", value)
    unwrapped = _MAILTO.sub("", unwrapped)
    return unwrapped.replace('"', "")


def _deobfuscate(value: str) -> str:
    value = _OBFUSCATED_AT.sub("@", value)
    value = _OBFUSCATED_DOT.sub(".", value)
    value = _SPACES_AROUND_AT.sub("@", value)
    value = _SPACE_BEFORE_DOT.sub(".", value)
    return _COMMA_IN_DOMAIN.sub(r"\1.\2", value)


def _clean_part(part: str, outcome: EmailOutcome) -> str | None:
    """Normalize one address; returns it when valid, else records it as invalid."""
    current = part
    trimmed = current.strip(_EDGE_PUNCTUATION)
    if trimmed != current:
        outcome.steps.append(Step("EML-002", current, trimmed))
        current = trimmed
    lowered = current.lower()
    if lowered != current:
        outcome.steps.append(Step("EML-001", current, lowered))
        current = lowered
    for fixer in (fix_structure, fix_known_typo):
        candidate = fixer(current)
        if candidate != current and is_valid_email(candidate):
            outcome.steps.append(Step("EML-003", current, candidate))
            current = candidate
    if is_fake_email(current):
        outcome.steps.append(Step("PLC-001", current, ""))
        return None
    suggestion = suggest_domain(current)
    if is_valid_email(current):
        if suggestion:
            outcome.flags.append(Flag("EML-004", original_value=current, suggestion=suggestion))
        local = current.partition("@")[0]
        if local in ROLE_LOCAL_PARTS:
            outcome.flags.append(Flag("EML-007", original_value=current))
        return current
    outcome.invalid.append(current)
    outcome.flags.append(Flag("EML-005", original_value=current, suggestion=suggestion))
    return None


def clean_email_cell(value: str) -> EmailOutcome:
    """Turn a raw email cell into one primary address plus the rest."""
    outcome = EmailOutcome()
    if not value:
        return outcome
    current = value
    for code, transform in (("EML-002", _unwrap), ("EML-003", _deobfuscate)):
        new = transform(current)
        if new != current:
            outcome.steps.append(Step(code, current, new))
            current = new
    parts = [p for p in _SPLIT.split(current) if p.strip(_EDGE_PUNCTUATION)]
    if len(parts) > 1:
        outcome.steps.append(Step("EML-006", current, LIST_SEPARATOR.join(parts)))
    valid: list[str] = []
    for part in parts:
        cleaned = _clean_part(part, outcome)
        if cleaned and cleaned not in valid:
            valid.append(cleaned)
    if valid:
        outcome.email, outcome.others = valid[0], valid[1:]
    return outcome


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    """Clean emails. Runs after phones so a bad email can be judged against other channels."""
    del settings
    log = StageLog()
    cleaned: list[Record] = []
    for record in records:
        values = dict(record.values)
        outcome = clean_email_cell(values.get("email", ""))
        log.steps(record, "email", outcome.steps)
        only_channel = not outcome.email and not values.get("phone")
        for flag in outcome.flags:
            severity = flag.severity
            if flag.rule_code == "EML-005":
                severity = Severity.BLOCKING if only_channel else Severity.WARNING
            log.issue(
                record,
                "email",
                flag.rule_code,
                severity=severity,
                original_value=flag.original_value,
                suggestion=flag.suggestion,
            )
        values["email"] = outcome.email
        others = outcome.others + [INVALID_PREFIX + bad for bad in outcome.invalid]
        values["email_other"] = LIST_SEPARATOR.join(others)
        cleaned.append(record.with_values(values))
    return log.result(cleaned)
