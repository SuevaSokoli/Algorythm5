"""Robust column handling: order, missing/wrong headers, merged and repeated columns.

All names, emails and phones are invented.
"""

import pytest

from algorythm5.cleaning import ingest, propose_mapping, run_pipeline
from algorythm5.cleaning.mapping import build_records
from algorythm5.cleaning.models import IngestionMeta, PipelineSettings, RawRow, Table


def _table(headers: list[str], rows: list[list[str]]) -> Table:
    raw = tuple(RawRow(f"R{i + 1:05d}", i + 2, tuple(r)) for i, r in enumerate(rows))
    return Table(tuple(headers), raw, IngestionMeta("csv", "utf-8", ",", None, 0))


def _proposal(table: Table) -> list[tuple[str | None, str]]:
    return [(m.target_field, m.reason) for m in propose_mapping(table)]


ROWS = [
    ["Ana", "Hoxha", "ana.h@example.com", "0692345678"],
    ["Besnik", "Kola", "besnik.k@example.com", "0687654321"],
    ["Drita", "Leka", "drita.l@example.com", "0671234567"],
]


def test_column_order_does_not_matter() -> None:
    headers = ["Emri", "Mbiemri", "Email", "Tel"]
    order = [3, 1, 2, 0]
    shuffled = _table([headers[i] for i in order], [[row[i] for i in order] for row in ROWS])
    targets = {m.header: m.target_field for m in propose_mapping(shuffled)}
    assert targets == {
        "Emri": "first_name", "Mbiemri": "last_name", "Email": "email", "Tel": "phone",
    }  # fmt: skip


def test_columns_without_headers_are_recognised_by_values() -> None:
    table = _table(["column_1", "column_2", "column_3", "column_4"], ROWS)
    assert _proposal(table) == [
        ("first_name", "content"),
        ("last_name", "content"),
        ("email", "content"),
        ("phone", "content"),
    ]


def test_wrong_header_is_overruled_by_values() -> None:
    table = _table(["Name", "Surname", "Tel", "Email"], ROWS)  # last two headers swapped
    result = propose_mapping(table)
    assert [(m.target_field, m.reason) for m in result[2:]] == [
        ("email", "header_mismatch"),
        ("phone", "header_mismatch"),
    ]
    assert {m.method for m in result[2:]} == {"sniffed"}


def test_header_kept_when_values_agree_or_are_too_few() -> None:
    few = _table(["Tel"], [["ana@example.com"]])
    assert _proposal(few) == [("phone", "synonym")]


def test_full_names_in_a_first_name_column_become_full_name() -> None:
    table = _table(["Emri", "Email"], [["Ana Hoxha", "a@example.com"], ["Ben Kola", "b@ex.com"]])
    assert _proposal(table)[0] == ("full_name", "merged_names")


def test_full_name_column_recognised_by_values() -> None:
    table = _table(["Kontakti", "E"], [["Ana Hoxha", "a@example.com"], ["Hoxha, Drita", "d@x.al"]])
    assert _proposal(table)[0] == ("full_name", "content")


def test_repeated_single_field_is_kept_as_extra_with_reason() -> None:
    table = _table(["Emri", "First name", "Email"], [["Ana", "Ana", "a@example.com"]])
    result = propose_mapping(table)
    assert (result[1].target_field, result[1].reason) == (None, "duplicate_column")
    codes = [i.rule_code for i in build_records(table, result).issues]
    assert "MAP-004" in codes


@pytest.mark.parametrize("values", [["red", "blue", "green"], ["Tirana", "Durres", "Tirana"]])
def test_words_that_are_not_names_are_not_guessed(values: list[str]) -> None:
    table = _table(["Col"], [[v] for v in values])
    assert _proposal(table) == [(None, "unmatched")]


def test_header_mismatch_is_logged(settings: PipelineSettings) -> None:
    table = _table(["Name", "Surname", "Tel", "Email"], ROWS)
    result = run_pipeline(table, propose_mapping(table), settings)
    assert [i.rule_code for i in result.issues].count("MAP-003") == 2


def test_headerless_file_keeps_first_row_as_data(settings: PipelineSettings) -> None:
    data = (
        b"Ana;Hoxha;ana.h@example.com;0692345678\n"
        b"Besnik;Kola;besnik.k@example.com;0687654321\n"
        b"Drita;Leka;drita.l@example.com;0671234567\n"
    )
    table = ingest(data, settings)
    assert table.meta.header_row_index == -1
    assert len(table.rows) == 3
    assert [r.source_row for r in table.rows] == [1, 2, 3]
    assert "ING-009" in {i.rule_code for i in table.issues}
    targets = [m.target_field for m in propose_mapping(table)]
    assert targets == ["first_name", "last_name", "email", "phone"]


def test_title_row_above_unknown_headers_is_not_mistaken_for_data(
    settings: PipelineSettings,
) -> None:
    table = ingest(b"Kontakt,Numri\nana@example.com,0692345678\n", settings)
    assert table.meta.header_row_index == 0
    assert [m.target_field for m in propose_mapping(table)] == ["email", "phone"]
