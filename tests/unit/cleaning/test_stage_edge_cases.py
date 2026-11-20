"""Every stage must survive the CLAUDE.md §9 edge cases on every field it touches."""

from collections.abc import Callable, Sequence

import pytest

from algorythm5.cleaning import dates, emails, names, optouts, phones, placeholders, text
from algorythm5.cleaning.models import PipelineSettings, Record, StageResult
from tests.unit.cleaning.conftest import make_record

Stage = Callable[[Sequence[Record], PipelineSettings], StageResult]
STAGES: dict[str, tuple[Stage, tuple[str, ...]]] = {
    "text": (text.apply, ("first_name", "email", "city")),
    "placeholders": (placeholders.apply, ("first_name", "phone", "email")),
    "names": (names.apply, ("first_name", "last_name", "full_name", "city")),
    "phones": (phones.apply, ("phone",)),
    "emails": (emails.apply, ("email",)),
    "dates": (dates.apply, ("birth_date", "signup_date")),
    "optouts": (optouts.apply, ("opt_out", "consent", "notes")),
}
EDGE_VALUES = {
    "empty": "",
    "whitespace": "   ",
    "placeholder": "N/A",
    "very_long": "x" * 1000,
    "mojibake": "Ã‹rmira Ã‡ela",
    "non_latin": "Αννα Ιβάνοβα 李",
}
ALREADY_CORRECT = {
    "first_name": "Ana",
    "last_name": "Hoxha",
    "full_name": "",
    "city": "Tiranë",
    "email": "ana.hoxha@gmail.com",
    "phone": "+355692345678",
    "birth_date": "1990-05-17",
    "signup_date": "2023-01-05",
    "opt_out": "",
    "consent": "po",
    "notes": "",
}


@pytest.mark.parametrize("stage_name", list(STAGES))
@pytest.mark.parametrize("edge", list(EDGE_VALUES))
def test_stage_handles_edge_values(settings: PipelineSettings, stage_name: str, edge: str) -> None:
    stage, fields = STAGES[stage_name]
    for field in fields:
        result = stage([make_record(**{field: EDGE_VALUES[edge]})], settings)
        assert len(result.records) == 1
        for change in result.changes:
            assert change.old_value != change.new_value


@pytest.mark.parametrize("stage_name", list(STAGES))
def test_already_correct_values_produce_no_changes(
    settings: PipelineSettings, stage_name: str
) -> None:
    stage, _ = STAGES[stage_name]
    result = stage([make_record(**ALREADY_CORRECT)], settings)
    assert result.changes == ()
    assert result.records[0].values == {**ALREADY_CORRECT, **_derived(stage_name)}


def _derived(stage_name: str) -> dict[str, str]:
    """Output-only fields a stage adds even when nothing needed fixing."""
    return {
        "phones": {"phone_other": ""},
        "emails": {"email_other": ""},
        "optouts": {"consent_status": "subscribed"},
    }.get(stage_name, {})
