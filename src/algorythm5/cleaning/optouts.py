"""Stage 6: opt-outs and consent -> `consent_status` (opt-out always wins)."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from functools import cache

from algorythm5.cleaning.data import load_pairs
from algorythm5.cleaning.duplicates import emails_of
from algorythm5.cleaning.models import (
    ConsentStatus,
    PipelineSettings,
    Record,
    Severity,
    StageResult,
)
from algorythm5.cleaning.rules import StageLog
from algorythm5.cleaning.text import fold

UNKNOWN_CONSENT_SEVERITY = {
    "include": Severity.INFO,
    "review": Severity.WARNING,
    "exclude": Severity.BLOCKING,
}
NOTE_FIELDS = ("notes",)


class Answer(StrEnum):
    OPTED_OUT = "opted_out"
    SUBSCRIBED = "subscribed"
    NO_OPT_OUT = "no_opt_out"
    EMPTY = "empty"
    UNRECOGNIZED = "unrecognized"


@cache
def _terms() -> dict[str, frozenset[str]]:
    by_category: dict[str, set[str]] = {}
    for term, category in load_pairs("optout_terms.csv"):
        by_category.setdefault(category, set()).add(fold(term))
    return {category: frozenset(terms) for category, terms in by_category.items()}


def _category(value: str) -> str | None:
    key = fold(value)
    for category in ("opted_out", "subscribed", "affirmative", "negative"):
        if key in _terms().get(category, frozenset()):
            return category
    return None


def read_opt_out(value: str) -> Answer:
    """Meaning of an opt-out column cell ('yes' = opted out)."""
    if not value.strip():
        return Answer.EMPTY
    category = _category(value)
    if category in ("opted_out", "affirmative"):
        return Answer.OPTED_OUT
    if category in ("subscribed", "negative"):
        return Answer.NO_OPT_OUT
    return Answer.UNRECOGNIZED


def read_consent(value: str) -> Answer:
    """Meaning of a consent column cell ('yes' = subscribed): the inverse of opt-out."""
    if not value.strip():
        return Answer.EMPTY
    category = _category(value)
    if category in ("subscribed", "affirmative"):
        return Answer.SUBSCRIBED
    if category in ("opted_out", "negative"):
        return Answer.OPTED_OUT
    return Answer.UNRECOGNIZED


def contains_opt_out_phrase(text: str) -> bool:
    folded = fold(text)
    return bool(folded) and any(p in folded for p in _terms().get("note_phrase", frozenset()))


def decide(opt_out: Answer, consent: Answer) -> ConsentStatus:
    if Answer.OPTED_OUT in (opt_out, consent):
        return ConsentStatus.OPTED_OUT
    if consent == Answer.SUBSCRIBED:
        return ConsentStatus.SUBSCRIBED
    return ConsentStatus.UNKNOWN


def _classify(record: Record, settings: PipelineSettings, log: StageLog) -> ConsentStatus:
    raw_opt, raw_consent = record.get("opt_out"), record.get("consent")
    opt_out, consent = read_opt_out(raw_opt), read_consent(raw_consent)
    for field, answer, raw in (("opt_out", opt_out, raw_opt), ("consent", consent, raw_consent)):
        if answer == Answer.UNRECOGNIZED:
            log.issue(record, field, "OPT-004", original_value=raw)
    status = decide(opt_out, consent)
    if opt_out == Answer.OPTED_OUT and consent == Answer.SUBSCRIBED:
        log.issue(record, "consent", "OPT-005", original_value=f"{raw_opt} / {raw_consent}")
    if status != ConsentStatus.OPTED_OUT:
        texts = [(f, record.get(f)) for f in NOTE_FIELDS] + list(record.extras.items())
        for field, text in texts:
            if contains_opt_out_phrase(text):
                log.issue(record, field, "OPT-002", original_value=text)
                status = ConsentStatus.OPTED_OUT
                break
    if status == ConsentStatus.OPTED_OUT:
        log.issue(record, "consent_status", "OPT-001")
    elif status == ConsentStatus.UNKNOWN:
        severity = UNKNOWN_CONSENT_SEVERITY[settings.unknown_consent_policy]
        log.issue(record, "consent_status", "OPT-003", severity=severity)
    return status


def apply(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    log = StageLog()
    cleaned: list[Record] = []
    for record in records:
        values = dict(record.values)
        values["consent_status"] = _classify(record, settings, log).value
        cleaned.append(record.with_values(values))
    return log.result(cleaned)


def propagate_by_email(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    """Opt-out always wins: a row whose email opted out anywhere else is suppressed too.

    Runs after duplicate merging, so it covers rows that were kept apart (for example the
    same address used under two different names, or in a file uploaded later).
    """
    del settings
    log = StageLog()
    opted_out = ConsentStatus.OPTED_OUT.value
    live = [r for r in records if not r.merged_into]
    blocked: dict[str, str] = {}
    for record in live:
        if record.get("consent_status") == opted_out:
            for email in emails_of(record):
                blocked.setdefault(email, record.record_id)
    result: list[Record] = []
    for record in records:
        email = record.get("email")
        source = blocked.get(email)
        if record.merged_into or record.get("consent_status") == opted_out or source is None:
            result.append(record)
            continue
        values = dict(record.values)
        log.change(record, "consent_status", values.get("consent_status", ""), opted_out, "OPT-006")
        values["consent_status"] = opted_out
        log.issue(record, "consent_status", "OPT-006", original_value=email, related=[source])
        result.append(record.with_values(values))
    return log.result(result)
