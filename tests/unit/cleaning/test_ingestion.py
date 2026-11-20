import io
import zipfile
from dataclasses import replace
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from algorythm5.cleaning import ingestion
from algorythm5.cleaning.models import PipelineError, PipelineSettings


def _xlsx(sheets: dict[str, list[list[object]]], hidden: tuple[str, ...] = ()) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
        if title in hidden:
            sheet.sheet_state = "hidden"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_csv_utf8_semicolon(settings: PipelineSettings) -> None:
    data = b"Emri;Email\nAna;ana@gmail.com\nBen;ben@yahoo.com\n"
    table = ingestion.ingest(data, settings)
    assert table.headers == ("Emri", "Email")
    assert [r.cells for r in table.rows] == [("Ana", "ana@gmail.com"), ("Ben", "ben@yahoo.com")]
    assert [r.source_row for r in table.rows] == [2, 3]
    assert [r.record_id for r in table.rows] == ["R00001", "R00002"]
    assert (table.meta.encoding, table.meta.delimiter) == ("utf-8", ";")
    assert table.issues == ()


def test_csv_cp1252_with_albanian_letters(settings: PipelineSettings) -> None:
    text = "Emri,Qyteti\nËrmira,Durrës\nÇlirim,Korçë\nDritë,Shkodër\n"
    table = ingestion.ingest(text.encode("cp1252"), settings)
    assert table.rows[0].cells == ("Ërmira", "Durrës")
    assert table.meta.file_kind == "csv"
    assert [i.rule_code for i in table.issues] == ["ING-001"]


def test_csv_with_bom_and_tabs(settings: PipelineSettings) -> None:
    table = ingestion.ingest("﻿Email\tTel\na@b.al\t069\n".encode(), settings)
    assert table.headers == ("Email", "Tel")
    assert table.meta.encoding == "utf-8-sig"


def test_header_below_title_rows_and_empty_rows(settings: PipelineSettings) -> None:
    data = b"Customer export 2024,,\n,,\nName,Email,Phone\nAna,a@b.al,069\n,,\nBen,b@c.al,068\n"
    table = ingestion.ingest(data, settings)
    assert table.headers == ("Name", "Email", "Phone")
    assert table.meta.header_row_index == 2
    assert [r.source_row for r in table.rows] == [4, 6]
    assert sorted(i.rule_code for i in table.issues) == ["ING-003", "ING-004"]


def test_ragged_rows_and_overflow(settings: PipelineSettings) -> None:
    data = b"Name,Email\nAna\nBen,b@c.al,extra1,extra2\nCan,c@d.al,,\n"
    table = ingestion.ingest(data, settings)
    assert table.headers == ("Name", "Email", "_overflow")
    assert table.rows[0].cells == ("Ana", "", "")
    assert table.rows[1].cells == ("Ben", "b@c.al", "extra1 | extra2")
    assert table.rows[2].cells == ("Can", "c@d.al", "")
    overflow = [i for i in table.issues if i.rule_code == "ING-006"]
    assert [i.record_id for i in overflow] == ["R00002"]


def test_duplicate_and_blank_headers(settings: PipelineSettings) -> None:
    table = ingestion.ingest(b"Email,Email,,Name\na,b,c,d\n", settings)
    assert table.headers == ("Email", "Email_2", "column_3", "Name")


def test_long_cell_is_truncated(settings: PipelineSettings) -> None:
    data = ("Name,Notes\nAna," + "x" * 50 + "\n").encode()
    table = ingestion.ingest(data, replace(settings, max_cell_chars=10))
    assert table.rows[0].cells[1] == "x" * 10
    assert [i.rule_code for i in table.issues] == ["ING-008"]


def test_xlsx_preserves_phone_digits_and_dates(settings: PipelineSettings) -> None:
    data = _xlsx(
        {
            "Data": [
                ["Name", "Phone", "Joined", "Score", "VIP"],
                ["Ana", 355692345678.0, datetime(2023, 1, 5), 1.5, True],
                ["Ben", 692345678, date(2020, 2, 3), None, False],
            ]
        }
    )
    table = ingestion.ingest(data, settings)
    assert table.meta.file_kind == "xlsx"
    assert table.meta.sheet_name == "Data"
    assert table.rows[0].cells == ("Ana", "355692345678", "2023-01-05", "1.5", "TRUE")
    assert table.rows[1].cells == ("Ben", "692345678", "2020-02-03", "", "FALSE")


def test_xlsx_skips_hidden_and_reports_multiple_sheets(settings: PipelineSettings) -> None:
    data = _xlsx(
        {
            "Hidden": [["Email"], ["hidden@x.al"]],
            "Main": [["Email"], ["a@b.al"]],
            "Other": [["Email"], ["c@d.al"]],
        },
        hidden=("Hidden",),
    )
    table = ingestion.ingest(data, settings)
    assert table.meta.sheet_name == "Main"
    assert [i.rule_code for i in table.issues] == ["ING-005"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), (12, "12"), (1.0e11, "100000000000"), (0.25, "0.25"),
     (datetime(2020, 1, 2, 3, 4, 5), "2020-01-02 03:04:05"), ("x", "x")],
)  # fmt: skip
def test_cell_to_text(value: object, expected: str) -> None:
    assert ingestion.cell_to_text(value) == expected


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "ING-901"),
        (b"   \n ", "ING-901"),
        (b"\x00\x01binary", "ING-901"),
        (b"PK\x03\x04not really a zip", "ING-901"),
    ],
)
def test_unreadable_files(settings: PipelineSettings, data: bytes, code: str) -> None:
    with pytest.raises(PipelineError) as error:
        ingestion.ingest(data, settings)
    assert error.value.rule_code == code


def test_zip_that_is_not_a_workbook(settings: PipelineSettings) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(PipelineError) as error:
        ingestion.ingest(buffer.getvalue(), settings)
    assert error.value.rule_code == "ING-901"


def test_zip_bomb_guard(settings: PipelineSettings) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", "<x/>")
        archive.writestr("xl/big.xml", "0" * 2_000_000)
    small = replace(settings, max_xlsx_uncompressed_bytes=1_000_000)
    with pytest.raises(PipelineError) as error:
        ingestion.ingest(buffer.getvalue(), small)
    assert error.value.rule_code == "ING-902"


@pytest.mark.parametrize(
    ("override", "data"),
    [
        ({"max_upload_bytes": 5}, b"Email\na@b.al\n"),
        ({"max_rows": 1}, b"Email\na@b.al\nc@d.al\n"),
        ({"max_columns": 1}, b"Email,Name\na@b.al,Ana\n"),
    ],
)
def test_limits(settings: PipelineSettings, override: dict[str, int], data: bytes) -> None:
    with pytest.raises(PipelineError) as error:
        ingestion.ingest(data, replace(settings, **override))  # type: ignore[arg-type]
    assert error.value.rule_code == "ING-902"


def test_delimiter_fallback_when_sniffer_fails() -> None:
    assert ingestion.sniff_delimiter("a|b|c\n1|2|3\n4|5|6\n") == "|"
    assert ingestion._most_consistent_delimiter(["a;b", "c;d", "e,f;g"]) == ";"
