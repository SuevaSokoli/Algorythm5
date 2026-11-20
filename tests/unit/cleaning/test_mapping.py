import pytest

from algorythm5.cleaning import mapping
from algorythm5.cleaning.models import ColumnMapping, IngestionMeta, RawRow, Table


def _table(headers: list[str], rows: list[list[str]]) -> Table:
    raw = tuple(RawRow(f"R{i + 1:05d}", i + 2, tuple(r)) for i, r in enumerate(rows))
    return Table(tuple(headers), raw, IngestionMeta("csv", "utf-8", ",", None, 0))


def _targets(table: Table) -> list[str | None]:
    return [m.target_field for m in mapping.propose_mapping(table)]


def test_exact_synonyms_english_and_albanian() -> None:
    table = _table(
        ["Emri", "Mbiemri", "E-mail", "Nr. Tel", "Qyteti", "Datëlindja", "Pëlqim", "Shënime"],
        [["Ana", "Hoxha", "a@b.al", "0692345678", "Tiranë", "01.01.1990", "po", ""]],
    )
    result = mapping.propose_mapping(table)
    assert [m.target_field for m in result] == [
        "first_name", "last_name", "email", "phone", "city", "birth_date", "consent", "notes",
    ]  # fmt: skip
    assert {m.confidence for m in result} == {1.0}
    assert {m.method for m in result} == {"auto"}


def test_fuzzy_header_match() -> None:
    result = mapping.propose_mapping(_table(["Emaill Adress"], [["a@b.al"]]))
    assert result[0].target_field == "email"


def test_fuzzy_confidence() -> None:
    result = mapping.propose_mapping(_table(["Telefonn"], [[""]]))
    assert (result[0].target_field, result[0].confidence) == ("phone", 0.8)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["ana@gmail.com", "ben@yahoo.com"], "email"),
        (["0692345678", "+355 68 765 4321"], "phone"),
        (["01.02.2020", "2020-05-06"], "signup_date"),
        (["red", "blue"], None),
        (["", ""], None),
    ],
)
def test_content_sniffing(values: list[str], expected: str | None) -> None:
    result = mapping.propose_mapping(_table(["Col X"], [[v] for v in values]))
    assert result[0].target_field == expected
    if expected:
        assert (result[0].method, result[0].confidence) == ("sniffed", 0.6)


def test_single_fields_mapped_once_multi_fields_many() -> None:
    table = _table(["Emri", "Emer", "Email", "Email 2", "Tel"], [["a", "b", "", "", ""]])
    targets = _targets(table)
    assert targets[0] == "first_name"
    assert targets[1] is None
    assert targets[2] == "email"
    assert targets[3] == "email"


def test_validate_targets() -> None:
    mapping.validate_targets(["email", "email", "first_name", None])
    with pytest.raises(mapping.InvalidMappingError):
        mapping.validate_targets(["first_name", "first_name"])
    with pytest.raises(mapping.InvalidMappingError):
        mapping.validate_targets(["shoe_size"])


def test_has_contact_field() -> None:
    assert mapping.has_contact_field(["first_name", "mobile"])
    assert not mapping.has_contact_field(["first_name", None])


def test_build_records_combines_mobile_first_and_keeps_passthrough() -> None:
    table = _table(
        ["Tel", "Celular", "Email", "Email 2", "Segment", "city"],
        [["04 223 4567", "0692345678", "a@b.al", "c@d.al", "VIP", "Tiranë"]],
    )
    mappings = [
        ColumnMapping(0, "Tel", "phone", "auto", 1.0),
        ColumnMapping(1, "Celular", "mobile", "auto", 1.0),
        ColumnMapping(2, "Email", "email", "auto", 1.0),
        ColumnMapping(3, "Email 2", "email", "manual", 1.0),
        ColumnMapping(4, "Segment", None, "auto", 0.0),
        ColumnMapping(5, "city", None, "manual", 0.0),
    ]
    result = mapping.build_records(table, mappings)
    record = result.records[0]
    assert record.get("phone") == "0692345678; 04 223 4567"
    assert record.get("email") == "a@b.al; c@d.al"
    assert "mobile" not in record.values
    assert record.extras == {"Segment": "VIP", "city (extra)": "Tiranë"}
    assert record.source_rows == (2,)
    codes = sorted(i.rule_code for i in result.issues)
    assert codes == ["MAP-001", "MAP-001", "MAP-001", "MAP-002", "MAP-002"]
