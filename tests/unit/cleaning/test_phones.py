import pytest

from algorythm5.cleaning import phones
from algorythm5.cleaning.models import PipelineSettings
from tests.unit.cleaning.conftest import make_record


@pytest.mark.parametrize(
    ("raw", "phone", "others", "extension", "codes", "flags"),
    [
        ("692345678", "+355692345678", [], "", ["PHN-001"], []),
        ("069 234 5678", "+355692345678", [], "", ["PHN-001"], []),
        ("+355692345678", "+355692345678", [], "", [], []),
        ("0692345678.0", "+355692345678", [], "", ["PHN-003", "PHN-001"], []),
        ("'0692345678", "+355692345678", [], "", ["PHN-001"], []),
        ("00355 69 234 5678", "+355692345678", [], "", ["PHN-008", "PHN-001"], []),
        ("3.55692E+11", "", [], "", [], ["PHN-002"]),
        ("3,55692E+11", "", [], "", [], ["PHN-002"]),
        ("+355 69 234 5678 ext 12", "+355692345678", [], "12", ["PHN-005", "PHN-001"], []),
        ("0692345678 x 7", "+355692345678", [], "7", ["PHN-005", "PHN-001"], []),
        ("04 223 4567", "+35542234567", [], "", ["PHN-001"], []),
        ("04/2234567", "+35542234567", [], "", ["PHN-001"], []),
        ("12345", "", ["12345"], "", [], ["PHN-007"]),
        ("call me", "", ["call me"], "", [], ["PHN-007"]),
        ("", "", [], "", [], []),
    ],
)
def test_clean_phone_cell(
    raw: str, phone: str, others: list[str], extension: str, codes: list[str], flags: list[str]
) -> None:
    outcome = phones.clean_phone_cell(raw, "AL")
    assert outcome.phone == phone
    assert outcome.others == others
    assert outcome.extension == extension
    assert [s.rule_code for s in outcome.steps if s.old != s.new] == codes
    assert [f.rule_code for f in outcome.flags] == flags


def test_multiple_numbers_prefer_mobile() -> None:
    outcome = phones.clean_phone_cell("04 223 4567 / 069 234 5678", "AL")
    assert outcome.phone == "+355692345678"
    assert outcome.others == ["+35542234567"]
    assert outcome.steps[0].rule_code == "PHN-006"


@pytest.mark.parametrize("separator", [";", ",", "|", " or ", " ose ", "; "])
def test_split_separators(separator: str) -> None:
    outcome = phones.clean_phone_cell(f"0692345678{separator}0687654321", "AL")
    assert outcome.phone == "+355692345678"
    assert outcome.others == ["+355687654321"]


def test_duplicate_numbers_are_deduplicated() -> None:
    outcome = phones.clean_phone_cell("0692345678; +355 69 234 5678", "AL")
    assert outcome.others == []


def test_missing_plus_is_recovered_for_foreign_region() -> None:
    outcome = phones.clean_phone_cell("393471234567", "AL")  # Italian mobile without +
    assert outcome.phone == "+393471234567"


def test_fake_number_in_list_is_dropped() -> None:
    outcome = phones.clean_phone_cell("0692345678; 0000000", "AL")
    assert outcome.phone == "+355692345678"
    assert [s.rule_code for s in outcome.steps if s.rule_code == "PLC-001"] == ["PLC-001"]


@pytest.mark.parametrize(
    ("country", "expected"),
    [("", "AL"), ("Kosovë", "XK"), ("Kosova", "XK"), ("IT", "IT"), ("Italia", "IT"),
     ("Shqipëri", "AL"), ("Atlantis", "AL")],
)  # fmt: skip
def test_region_for(country: str, expected: str) -> None:
    assert phones.region_for(country, "AL") == expected


def test_stage_uses_country_region(settings: PipelineSettings) -> None:
    record = make_record(phone="044 123 456", country="Kosova")
    cleaned = phones.apply([record], settings).records[0]
    assert cleaned.get("phone") == "+38344123456"


def test_stage_moves_extension_and_keeps_existing(settings: PipelineSettings) -> None:
    result = phones.apply(
        [
            make_record(phone="0692345678 ext 12"),
            make_record(phone="0692345678 ext 12", phone_extension="99"),
        ],
        settings,
    )
    assert result.records[0].get("phone_extension") == "12"
    assert result.records[1].get("phone_extension") == "99"


def test_stage_invalid_goes_to_phone_other(settings: PipelineSettings) -> None:
    result = phones.apply([make_record(phone="12345")], settings)
    assert result.records[0].get("phone") == ""
    assert result.records[0].get("phone_other") == "12345"
    assert [i.rule_code for i in result.issues] == ["PHN-007"]


def test_stage_clean_value_produces_no_changes(settings: PipelineSettings) -> None:
    result = phones.apply([make_record(phone="+355692345678")], settings)
    assert result.changes == ()
    assert result.issues == ()
