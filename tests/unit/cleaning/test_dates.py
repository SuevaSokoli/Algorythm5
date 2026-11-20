from dataclasses import replace
from datetime import date

import pytest

from algorythm5.cleaning import dates
from algorythm5.cleaning.models import PipelineSettings, Severity
from tests.unit.cleaning.conftest import TODAY, make_record


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2023-01-05", date(2023, 1, 5)),
        ("2023/1/5", date(2023, 1, 5)),
        ("2023-01-05 10:30:00", date(2023, 1, 5)),
        ("25.03.1985", date(1985, 3, 25)),
        ("25/03/1985", date(1985, 3, 25)),
        ("03/25/1985", date(1985, 3, 25)),  # second part > 12 proves MDY
        ("05.06.85", date(1985, 6, 5)),
        ("05.06.24", date(2024, 6, 5)),
        ("12 mars 1990", date(1990, 3, 12)),
        ("12 Nëntor 1990", date(1990, 11, 12)),
        ("1 dhjetor 2001", date(2001, 12, 1)),
        ("3-Jan-2020", date(2020, 1, 3)),
        ("March 5, 2020", date(2020, 3, 5)),
        ("44927", date(2023, 1, 1)),
        ("44927.0", date(2023, 1, 1)),
    ],
)
def test_parse_date(raw: str, expected: date) -> None:
    parsed = dates.parse_date(raw, "DMY", TODAY)
    assert parsed is not None
    assert parsed.value == expected


@pytest.mark.parametrize("raw", ["31.02.2020", "1985", "hello", "", "13/13/2020", "99999"])
def test_unparsable(raw: str) -> None:
    assert dates.parse_date(raw, "DMY", TODAY) is None


def test_two_digit_birth_year_never_in_future() -> None:
    parsed = dates.parse_date("01.01.30", "DMY", TODAY, birth=True)
    assert parsed is not None and parsed.value == date(1930, 1, 1)


def test_order_setting_for_ambiguous_value() -> None:
    dmy = dates.parse_date("03/04/2020", "DMY", TODAY)
    mdy = dates.parse_date("03/04/2020", "MDY", TODAY)
    assert dmy is not None and dmy.value == date(2020, 4, 3) and dmy.ambiguous
    assert mdy is not None and mdy.value == date(2020, 3, 4)


def test_serial_flag() -> None:
    parsed = dates.parse_date("44927", "DMY", TODAY)
    assert parsed is not None and parsed.is_serial


@pytest.mark.parametrize(
    ("value", "birth", "reason"),
    [
        (date(2027, 1, 1), False, "in the future"),
        (date(1899, 1, 1), False, "before 1900"),
        (date(2020, 1, 1), True, "age would be 6"),
        (date(1900, 1, 2), True, "age would be 126"),
        (date(1990, 1, 1), True, None),
    ],
)
def test_implausibility(value: date, birth: bool, reason: str | None) -> None:
    assert dates.implausibility(value, TODAY, birth=birth) == reason


def test_column_evidence() -> None:
    assert dates.column_order_evidence(["03/04/2020", "25/12/2020"]) == {"DMY"}
    assert dates.column_order_evidence(["12/25/2020"]) == {"MDY"}
    assert dates.column_order_evidence(["03/04/2020"]) == set()


def test_stage_column_evidence_overrides_setting(settings: PipelineSettings) -> None:
    mdy_settings = replace(settings, date_order="MDY")
    records = [
        make_record("R00001", 2, signup_date="03/04/2020"),
        make_record("R00002", 3, signup_date="25/12/2020"),
    ]
    result = dates.apply(records, mdy_settings)
    assert result.records[0].get("signup_date") == "2020-04-03"
    assert not [i for i in result.issues if i.rule_code == "DAT-003"]  # order was proven


def test_stage_default_order_logs_info(settings: PipelineSettings) -> None:
    result = dates.apply([make_record(signup_date="03/04/2020")], settings)
    issue = next(i for i in result.issues if i.rule_code == "DAT-003")
    assert issue.severity == Severity.INFO


def test_stage_contradicting_column_warns(settings: PipelineSettings) -> None:
    records = [
        make_record("R00001", 2, signup_date="25/12/2020"),
        make_record("R00002", 3, signup_date="12/25/2020"),
        make_record("R00003", 4, signup_date="03/04/2020"),
    ]
    result = dates.apply(records, settings)
    issue = next(i for i in result.issues if i.rule_code == "DAT-003")
    assert (issue.record_id, issue.severity) == ("R00003", Severity.WARNING)


def test_stage_codes(settings: PipelineSettings) -> None:
    records = [
        make_record("R00001", 2, signup_date="44927"),
        make_record("R00002", 3, birth_date="garbage"),
        make_record("R00003", 4, birth_date="2020-01-01"),
        make_record("R00004", 5, birth_date="1900-01-00"),
    ]
    result = dates.apply(records, settings)
    assert result.records[0].get("signup_date") == "2023-01-01"
    assert result.records[1].get("birth_date") == "garbage"
    assert result.records[3].get("birth_date") == ""
    # R00003 is already ISO, so it produces no change entry.
    assert {(c.record_id, c.rule_code) for c in result.changes} == {
        ("R00001", "DAT-002"),
        ("R00004", "DAT-005"),
    }
    assert sorted((i.record_id, i.rule_code) for i in result.issues) == [
        ("R00002", "DAT-004"),
        ("R00003", "DAT-005"),
        ("R00004", "DAT-005"),
    ]


def test_stage_clean_value_produces_no_changes(settings: PipelineSettings) -> None:
    result = dates.apply([make_record(birth_date="1990-05-17")], settings)
    assert result.changes == ()
    assert result.issues == ()
