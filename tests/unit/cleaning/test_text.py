import pytest

from algorythm5.cleaning import text
from algorythm5.cleaning.models import PipelineSettings
from tests.unit.cleaning.conftest import make_record, make_record_with_extras


@pytest.mark.parametrize(
    ("raw", "field", "expected", "codes"),
    [
        ("Ã«shtÃ«", "first_name", "është", ["TXT-001"]),  # Ã«→ë
        ("Ã‡ela", "last_name", "Çela", ["TXT-001"]),  # Ã‡ela → Çela
        ("Ana​", "first_name", "Ana", ["TXT-002"]),
        ("Ana Hoxha", "full_name", "Ana Hoxha", ["TXT-002"]),
        ("  Ana   Hoxha  ", "full_name", "Ana Hoxha", ["TXT-003"]),
        ("O’Brien", "last_name", "O'Brien", ["TXT-004"]),
        ("O’Brien", "city", "O’Brien", []),  # apostrophes only fixed in names
        ("a@b.al\na2@b.al", "email", "a@b.al; a2@b.al", ["TXT-003"]),
        ("Rruga\nKavajës", "street", "Rruga Kavajës", ["TXT-003"]),
        ("", "first_name", "", []),
        ("   ", "first_name", "", ["TXT-003"]),
        ("Ana Hoxha", "full_name", "Ana Hoxha", []),
        ("Αννα", "first_name", "Αννα", []),  # Greek
    ],
)
def test_normalize_value(raw: str, field: str, expected: str, codes: list[str]) -> None:
    value, steps = text.normalize_value(raw, field)
    assert value == expected
    assert [s.rule_code for s in steps] == codes


def test_nfc_normalization_is_silent() -> None:
    decomposed = "Hoxhë"  # e + combining diaeresis
    value, steps = text.normalize_value(decomposed, "last_name")
    assert value == "Hoxhë"
    assert steps == ()


def test_fold_and_header_normalization() -> None:
    assert text.fold("  ÇELA  Hoxhë ") == "cela hoxhe"
    assert text.normalize_header("E-mail_Address") == "e mail address"
    assert text.normalize_header("Emri i plotë") == "emri i plote"


def test_stage_logs_changes_for_values_and_extras(settings: PipelineSettings) -> None:
    record = make_record_with_extras({"Note": " hi "}, first_name=" Ana ")
    result = text.apply([record], settings)
    assert result.records[0].get("first_name") == "Ana"
    assert result.records[0].extras["Note"] == "hi"
    assert {(c.field, c.rule_code) for c in result.changes} == {
        ("first_name", "TXT-003"),
        ("Note", "TXT-003"),
    }


def test_already_clean_record_produces_no_changes(settings: PipelineSettings) -> None:
    result = text.apply([make_record(first_name="Ana", email="ana@gmail.com")], settings)
    assert result.changes == ()
