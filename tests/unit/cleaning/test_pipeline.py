from dataclasses import replace

import pytest

from algorythm5.cleaning import ingest, propose_mapping, run_pipeline
from algorythm5.cleaning.export import CLEAN_COLUMNS, build_outputs, guard_cell
from algorythm5.cleaning.models import (
    ColumnMapping,
    Issue,
    PipelineError,
    PipelineResult,
    PipelineSettings,
    RowStatus,
    Severity,
)
from algorythm5.cleaning.status import derive_status, is_review_item
from tests.unit.cleaning.conftest import make_record

MESSY = """Emri,Mbiemri,Email,Tel,Qyteti,Abonuar,Shënime
ANA,HOXHA,ANA.HOXHA@GMIAL.COM,0692345678,TIRANË,po,
Ana,Hoxha,ana.hoxha@gmail.com,069 234 5678,Tiranë,,
BEN,KOLA,,3.55692E+11,DURRËS,,
Drita,Leka,drita@yahoo.con,0687654321,Vlorë,jo,
Arben,Çela,arben@gmaul.com,,Shkodër,po,unsubscribe please
Elira,Dushku,,0691112233,Berat,po,
Klea,Muça,klea.muca@gmail.com,,Korçë,po,
""".encode()


def _run(settings: PipelineSettings, data: bytes = MESSY) -> PipelineResult:
    table = ingest(data, settings)
    return run_pipeline(table, propose_mapping(table), settings)


def test_statuses_end_to_end(settings: PipelineSettings) -> None:
    result = _run(settings)
    statuses = {r.record_id: r.status for r in result.records}
    assert statuses == {
        "R00001": RowStatus.FIXED,  # survivor of the Ana Hoxha pair
        "R00002": RowStatus.MERGED,
        "R00003": RowStatus.REJECTED,  # only a damaged phone
        "R00004": RowStatus.SUPPRESSED,  # consent "jo"
        "R00005": RowStatus.SUPPRESSED,  # unsubscribe in notes
        "R00006": RowStatus.REJECTED,  # phone only, email required
        "R00007": RowStatus.OK,
    }


def test_required_channel_any_keeps_phone_only_rows(settings: PipelineSettings) -> None:
    result = _run(replace(settings, required_channel="any"))
    assert {r.record_id: r.status for r in result.records}["R00006"] == RowStatus.FIXED


def test_unknown_consent_exclude_policy_blocks(settings: PipelineSettings) -> None:
    data = b"Email\nana@gmail.com\n"
    result = _run(replace(settings, unknown_consent_policy="exclude"), data)
    assert result.records[0].status == RowStatus.BLOCKED


def test_missing_contact_column_stops_job(settings: PipelineSettings) -> None:
    table = ingest(b"Emri,Qyteti\nAna,Fier\n", settings)
    with pytest.raises(PipelineError) as error:
        run_pipeline(table, propose_mapping(table), settings)
    assert error.value.rule_code == "MAP-901"


def test_every_change_has_a_registered_code_and_real_difference(
    settings: PipelineSettings,
) -> None:
    for change in _run(settings).changes:
        assert change.old_value != change.new_value


def test_pipeline_is_deterministic(settings: PipelineSettings) -> None:
    first = build_outputs(_run(settings))
    second = build_outputs(_run(settings))
    assert first == second


def test_idempotent_on_its_own_clean_output(settings: PipelineSettings) -> None:
    clean = build_outputs(_run(settings)).clean_csv
    table = ingest(clean, settings)
    targets = {c: c for c in ("first_name", "last_name", "email", "phone", "city", "salutation")}
    mappings = [
        ColumnMapping(i, h, targets.get(h), "manual", 1.0) for i, h in enumerate(table.headers)
    ]
    second = run_pipeline(table, mappings, settings)
    assert second.changes == ()


def test_outputs_contain_clean_schema(settings: PipelineSettings) -> None:
    outputs = build_outputs(_run(settings))
    header = outputs.clean_csv.decode("utf-8-sig").splitlines()[0].split(",")
    assert header[: len(CLEAN_COLUMNS)] == list(CLEAN_COLUMNS)
    assert outputs.clean_csv.startswith(b"\xef\xbb\xbf")
    assert outputs.report_xlsx.startswith(b"PK")
    review = outputs.review_csv.decode("utf-8-sig")
    assert "PHN-002" in review and "OPT-002" in review and "ROW-001" in review


@pytest.mark.parametrize(
    ("column", "value", "expected"),
    [
        ("notes", "=SUM(A1)", "'=SUM(A1)"),
        ("notes", "@cmd", "'@cmd"),
        ("notes", "-1", "'-1"),
        ("phone", "+355692345678", "+355692345678"),
        ("phone_other", "+355692345678; +35542234567", "+355692345678; +35542234567"),
        ("phone_other", "+1 =HYPERLINK()", "'+1 =HYPERLINK()"),
        ("first_name", "Ana", "Ana"),
    ],
)
def test_formula_injection_guard(column: str, value: str, expected: str) -> None:
    assert guard_cell(column, value) == expected


def test_derive_status_precedence() -> None:
    warn = Issue("R00001", None, "NAM-005", Severity.WARNING, "x")
    block = Issue("R00001", None, "OPT-003", Severity.BLOCKING, "x")
    reject = Issue("R00001", None, "ROW-001", Severity.BLOCKING, "x")
    opted = make_record(consent_status="opted_out")
    plain = make_record()
    assert derive_status(replace(plain, merged_into="R2"), [reject], True) == RowStatus.MERGED
    assert derive_status(opted, [reject], True) == RowStatus.SUPPRESSED
    assert derive_status(plain, [warn, reject], True) == RowStatus.REJECTED
    assert derive_status(plain, [warn, block], True) == RowStatus.BLOCKED
    assert derive_status(plain, [warn], False) == RowStatus.REVIEW
    assert derive_status(plain, [], True) == RowStatus.FIXED
    assert derive_status(plain, [], False) == RowStatus.OK
    assert is_review_item(warn, RowStatus.REVIEW)
    assert not is_review_item(warn, RowStatus.MERGED)
