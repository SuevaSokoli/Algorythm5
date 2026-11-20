import pytest

from algorythm5.cleaning import placeholders
from algorythm5.cleaning.models import PipelineSettings
from tests.unit.cleaning.conftest import make_record, make_record_with_extras


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ("N/A", "city"),
        ("n.a.", "company"),
        ("NULL", "first_name"),
        ("-", "last_name"),
        ("?", "first_name"),
        ("xxx", "street"),
        ("0", "postal_code"),
        ("Unknown", "city"),
        ("nuk ka", "notes"),
        ("s'ka", "company"),
        ("000000", "postal_code"),
        ("-----", "notes"),
        ("0000000", "phone"),
        ("123456789", "phone"),
        ("0123456789", "phone"),
        ("987654321", "phone"),
        ("111 111 111", "phone"),
        ("test@test.com", "email"),
        ("A@A.COM", "email"),
        ("noemail@shop.al", "email"),
        ("someone@example.com", "email"),
        ("x@x.al", "email"),
        ("(n/a)", "city"),
    ],
)
def test_placeholders_are_detected(value: str, field: str) -> None:
    assert placeholders.is_placeholder(value, field)


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ("", "city"),
        ("Tiranë", "city"),
        ("Ana", "first_name"),
        ("no", "opt_out"),
        ("jo", "consent"),
        ("0", "opt_out"),
        ("x", "opt_out"),
        ("0692345678", "phone"),
        ("ana@gmail.com", "email"),
        ("1001", "postal_code"),
        ("Li", "last_name"),
    ],
)
def test_real_values_are_kept(value: str, field: str) -> None:
    assert not placeholders.is_placeholder(value, field)


def test_stage_clears_and_logs(settings: PipelineSettings) -> None:
    record = make_record_with_extras({"Segment": "n/a"}, city="N/A", first_name="Ana")
    result = placeholders.apply([record], settings)
    assert result.records[0].get("city") == ""
    assert result.records[0].get("first_name") == "Ana"
    assert result.records[0].extras["Segment"] == ""
    assert sorted((c.field, c.old_value, c.rule_code) for c in result.changes) == [
        ("Segment", "n/a", "PLC-001"),
        ("city", "N/A", "PLC-001"),
    ]


def test_no_changes_for_clean_record(settings: PipelineSettings) -> None:
    assert placeholders.apply([make_record(city="Durrës")], settings).changes == ()
