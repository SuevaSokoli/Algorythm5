from dataclasses import replace

import pytest

from algorythm5.cleaning import optouts
from algorythm5.cleaning.models import ConsentStatus, PipelineSettings, Record, Severity
from algorythm5.cleaning.optouts import Answer
from tests.unit.cleaning.conftest import make_record, make_record_with_extras


@pytest.mark.parametrize(
    ("raw", "answer"),
    [
        ("yes", Answer.OPTED_OUT), ("1", Answer.OPTED_OUT), ("X", Answer.OPTED_OUT),
        ("Po", Answer.OPTED_OUT), ("Unsubscribed", Answer.OPTED_OUT), ("DNC", Answer.OPTED_OUT),
        ("çabonuar", Answer.OPTED_OUT), ("mos dërgo", Answer.OPTED_OUT),
        ("no", Answer.NO_OPT_OUT), ("jo", Answer.NO_OPT_OUT), ("0", Answer.NO_OPT_OUT),
        ("", Answer.EMPTY), ("maybe", Answer.UNRECOGNIZED),
    ],
)  # fmt: skip
def test_read_opt_out(raw: str, answer: Answer) -> None:
    assert optouts.read_opt_out(raw) == answer


@pytest.mark.parametrize(
    ("raw", "answer"),
    [
        ("yes", Answer.SUBSCRIBED), ("po", Answer.SUBSCRIBED), ("opt-in", Answer.SUBSCRIBED),
        ("no", Answer.OPTED_OUT), ("jo", Answer.OPTED_OUT), ("unsubscribed", Answer.OPTED_OUT),
        ("", Answer.EMPTY), ("??", Answer.UNRECOGNIZED),
    ],
)  # fmt: skip
def test_read_consent_is_inverse(raw: str, answer: Answer) -> None:
    assert optouts.read_consent(raw) == answer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Please unsubscribe me", True),
        ("Klienti: mos më dërgoni email", True),
        ("DO NOT CONTACT", True),
        ("VIP customer", False),
        ("", False),
    ],
)
def test_note_phrases(text: str, expected: bool) -> None:
    assert optouts.contains_opt_out_phrase(text) is expected


def _status(records: tuple[Record, ...]) -> str:
    return records[0].get("consent_status")


def test_opt_out_wins_over_consent(settings: PipelineSettings) -> None:
    result = optouts.apply([make_record(opt_out="yes", consent="yes")], settings)
    assert _status(result.records) == ConsentStatus.OPTED_OUT
    assert {i.rule_code for i in result.issues} == {"OPT-005", "OPT-001"}


def test_consent_yes_is_subscribed(settings: PipelineSettings) -> None:
    result = optouts.apply([make_record(consent="po")], settings)
    assert _status(result.records) == ConsentStatus.SUBSCRIBED
    assert result.issues == ()


def test_note_phrase_suppresses_with_warning(settings: PipelineSettings) -> None:
    record = make_record_with_extras({"Comment": "unsubscribe pls"}, consent="yes")
    result = optouts.apply([record], settings)
    assert _status(result.records) == ConsentStatus.OPTED_OUT
    warning = next(i for i in result.issues if i.rule_code == "OPT-002")
    assert (warning.field, warning.severity) == ("Comment", Severity.WARNING)


def test_unrecognized_value_is_flagged(settings: PipelineSettings) -> None:
    result = optouts.apply([make_record(opt_out="maybe")], settings)
    assert _status(result.records) == ConsentStatus.UNKNOWN
    assert "OPT-004" in {i.rule_code for i in result.issues}


@pytest.mark.parametrize(
    ("policy", "severity"),
    [("include", Severity.INFO), ("review", Severity.WARNING), ("exclude", Severity.BLOCKING)],
)
def test_unknown_consent_policy(
    settings: PipelineSettings, policy: str, severity: Severity
) -> None:
    policy_settings = replace(settings, unknown_consent_policy=policy)  # type: ignore[arg-type]
    result = optouts.apply([make_record()], policy_settings)
    issue = next(i for i in result.issues if i.rule_code == "OPT-003")
    assert issue.severity == severity


def test_no_opt_out_alone_stays_unknown(settings: PipelineSettings) -> None:
    result = optouts.apply([make_record(opt_out="no")], settings)
    assert _status(result.records) == ConsentStatus.UNKNOWN
