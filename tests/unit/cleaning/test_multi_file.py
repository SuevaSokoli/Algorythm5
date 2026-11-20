"""Several files -> one clean result; cross-file duplicates. All data here is invented."""

from algorythm5.cleaning import duplicates, ingest, optouts, propose_mapping, run_sources
from algorythm5.cleaning.export import (
    build_outputs,
    file_rows,
    format_source_rows,
    merge_rows,
    same_name_rows,
)
from algorythm5.cleaning.models import (
    PipelineResult,
    PipelineSettings,
    Record,
    RowStatus,
    SourceFile,
)
from tests.unit.cleaning.conftest import make_record


def _source(label: str, data: bytes, settings: PipelineSettings) -> SourceFile:
    table = ingest(data, settings)
    return SourceFile(label, table, propose_mapping(table))


def _run(settings: PipelineSettings, *files: tuple[str, bytes]) -> PipelineResult:
    return run_sources([_source(label, data, settings) for label, data in files], settings)


def _by_name(result: PipelineResult) -> dict[str, list[Record]]:
    found: dict[str, list[Record]] = {}
    for record in result.records:
        found.setdefault(f"{record.get('first_name')} {record.get('last_name')}", []).append(record)
    return found


CRM = "Emri;Mbiemri;Email;Tel;Datëlindja\nAna;Hoxha;ana.h@gmail.com;0692345678;12.03.1985\n"
SHOP = (
    "Phone,E-mail,Last name,First name,Birth date,City\n"
    "+355 69 234 5678,,HOXHA,ANA,1985-03-12,Tiranë\n"
)


def rec(record_id: str, file: str, **values: str) -> Record:
    base = make_record(record_id, 2, **{"consent_status": "unknown", **values})
    return Record(base.record_id, 2, base.values, {}, (2,), source_file=file, source_files=(file,))


# ---------- sources: encodings, delimiters, ids, duplicates ----------


def test_files_with_different_encodings_delimiters_and_columns(
    settings: PipelineSettings,
) -> None:
    result = _run(settings, ("crm.csv", CRM.encode("cp1252")), ("shop.csv", SHOP.encode()))
    metas = [s.table.meta for s in result.sources]
    assert [m.delimiter for m in metas] == [";", ","]
    assert metas[0].encoding in ("cp1252", "cp1250")
    assert metas[1].encoding == "utf-8"
    assert [r.record_id for r in result.records] == ["R00001", "R00002"]
    assert [(r.source_file, r.source_row) for r in result.records] == [
        ("crm.csv", 2),
        ("shop.csv", 2),
    ]


def test_same_person_in_two_files_with_matching_details_is_merged(
    settings: PipelineSettings,
) -> None:
    result = _run(settings, ("crm.csv", CRM.encode()), ("shop.csv", SHOP.encode()))
    kept, merged = result.records
    assert merged.merged_into == kept.record_id
    assert kept.origins() == (("crm.csv", 2), ("shop.csv", 2))
    assert format_source_rows(kept, True) == "crm.csv:2; shop.csv:2"
    assert kept.get("city") == "Tiranë"  # filled from the second file
    assert "DUP-009" in {i.rule_code for i in result.issues}
    assert merge_rows(result)[0][:5] == [
        "R00001",
        "crm.csv:2; shop.csv:2",
        "R00002",
        "shop.csv",
        "2",
    ]


def test_same_name_with_conflicting_details_stays_two_people(
    settings: PipelineSettings,
) -> None:
    other = "First name,Last name,Email,Birth date\nAna,Hoxha,ana.other@gmail.com,1999-07-01\n"
    result = _run(settings, ("crm.csv", CRM.encode()), ("events.csv", other.encode()))
    assert all(r.merged_into is None for r in result.records)
    report = same_name_rows(result)
    assert len(report) == 1
    row = report[0]
    assert row[0] == "Ana Hoxha"
    assert row[1] == "different email, birth date"
    assert [i.rule_code for i in result.issues].count("DUP-008") == 2


def test_same_file_uploaded_twice_is_ignored(settings: PipelineSettings) -> None:
    data = CRM.encode()
    result = _run(settings, ("crm.csv", data), ("crm (2).csv", data))
    assert len(result.records) == 1
    assert result.duplicate_sources == {1: 0}
    source_issue = next(i for i in result.issues if i.rule_code == "SRC-001")
    assert "crm (2).csv (same as crm.csv)" in source_issue.message
    assert file_rows(result)[1][7] == "no, same as crm.csv"


def test_job_level_findings_name_their_file(settings: PipelineSettings) -> None:
    result = _run(settings, ("crm.csv", CRM.encode("cp1252")), ("shop.csv", SHOP.encode()))
    encoding = next(i for i in result.issues if i.rule_code == "ING-001")
    assert encoding.message.startswith("crm.csv: ")


def test_opt_out_in_one_file_wins_after_merge(settings: PipelineSettings) -> None:
    later = "Email,Emri,Mbiemri,Abonuar\nana.h@gmail.com,Ana,Hoxha,jo\n"
    result = _run(settings, ("crm.csv", CRM.encode()), ("newsletter.csv", later.encode()))
    kept = next(r for r in result.records if not r.merged_into)
    assert kept.status == RowStatus.SUPPRESSED


def test_outputs_have_source_file_and_report_sheets(settings: PipelineSettings) -> None:
    result = _run(settings, ("crm.csv", CRM.encode()), ("shop.csv", SHOP.encode()))
    outputs = build_outputs(result)
    header = outputs.clean_csv.decode("utf-8-sig").splitlines()[0]
    assert "source_rows,source_file" in header
    assert b"crm.csv:2; shop.csv:2" in outputs.clean_csv
    assert outputs.changes_csv.decode("utf-8-sig").startswith("record_id,source_file,")


# ---------- pair rules ----------


def test_same_name_without_any_details_goes_to_review(settings: PipelineSettings) -> None:
    a = rec("R00001", "a.csv", first_name="Ana", last_name="Hoxha", email="a@gmail.com")
    b = rec("R00002", "b.csv", first_name="Ana", last_name="Hoxha")
    assert duplicates.classify_pair(a, b) == "T3"


def test_matching_city_alone_is_not_enough_to_merge() -> None:
    a = rec("R00001", "a.csv", first_name="Ana", last_name="Hoxha", city="Tiranë")
    b = rec("R00002", "b.csv", first_name="ANA", last_name="HOXHA", city="Tirane")
    assert duplicates.classify_pair(a, b) == "T3"


def test_different_city_is_a_conflict() -> None:
    a = rec("R00001", "a.csv", first_name="Ana", last_name="Hoxha", city="Fier")
    b = rec("R00002", "b.csv", first_name="Ana", last_name="Hoxha", city="Vlorë")
    assert duplicates.classify_pair(a, b) == "DIFFERENT"


def test_same_street_with_different_punctuation_agrees() -> None:
    a = rec("R00001", "a.csv", first_name="Ana", last_name="Hoxha", street="Rr. Kavajës 12")
    b = rec("R00002", "b.csv", first_name="Ana", last_name="Hoxha", street="RR KAVAJES 12")
    assert duplicates.classify_pair(a, b) == "T4"


def test_people_judged_different_are_never_merged_through_a_third_record(
    settings: PipelineSettings,
) -> None:
    records = [
        rec("R00001", "a.csv", first_name="Ana", last_name="Hoxha", email="x@gmail.com",
            birth_date="1985-03-12"),
        rec("R00002", "b.csv", first_name="Ana", last_name="Hoxha", phone="+355692345678",
            birth_date="1999-07-01"),
        # Links to R00001 by email and to R00002 by phone + similar name.
        rec("R00003", "a.csv", first_name="Ana", last_name="Hoxa", email="x@gmail.com",
            phone="+355692345678"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    assert all(r.merged_into is None for r in result.records)
    brake = next(i for i in result.issues if i.rule_code == "DUP-004")
    assert "same name but different details" in brake.message
    assert "DUP-008" in {i.rule_code for i in result.issues}


def test_conflicting_values_keep_the_more_complete_one(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", "a.csv", first_name="Ana", last_name="Hoxha", email="a@gmail.com",
            street="Rr. Kavajës 12, Tiranë", company="Alba"),
        rec("R00002", "b.csv", first_name="Ana", last_name="Hoxha", email="a@gmail.com",
            street="Rr. Kavajës"),
    ]  # fmt: skip
    survivor = duplicates.apply(records, settings).records[0]
    assert survivor.get("street") == "Rr. Kavajës 12, Tiranë"


def test_conflicting_values_otherwise_keep_the_newer_one(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", "old.csv", first_name="Ana", last_name="Hoxha", email="a@gmail.com",
            company="Alba", postal_code="1001"),
        rec("R00002", "new.csv", first_name="Ana", last_name="Hoxha", email="a@gmail.com",
            company="Vega"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    survivor = result.records[0]
    assert survivor.record_id == "R00001"  # more complete record survives
    assert survivor.get("company") == "Vega"  # but the newer file's value wins the conflict
    conflict = next(i for i in result.issues if i.rule_code == "DUP-006")
    assert conflict.original_value == "Alba"
    assert "newer" in conflict.message


def test_opt_out_spreads_to_rows_with_the_same_email(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", "a.csv", first_name="Ana", email="shared@gmail.com",
            consent_status="opted_out"),
        rec("R00002", "b.csv", first_name="Zef", email="shared@gmail.com",
            consent_status="subscribed"),
        rec("R00003", "b.csv", first_name="Ben", email="ben@gmail.com",
            consent_status="subscribed"),
    ]  # fmt: skip
    result = optouts.propagate_by_email(records, settings)
    statuses = [r.get("consent_status") for r in result.records]
    assert statuses == ["opted_out", "opted_out", "subscribed"]
    assert [(i.record_id, i.rule_code) for i in result.issues] == [("R00002", "OPT-006")]


def test_same_email_different_names_across_files_end_to_end(
    settings: PipelineSettings,
) -> None:
    first = "Email,Emri,Mbiemri\nfamily@gmail.com,Ana,Hoxha\n"
    second = "Email,Emri,Mbiemri,Abonuar\nfamily@gmail.com,Zef,Prenga,jo\n"
    result = _run(settings, ("a.csv", first.encode()), ("b.csv", second.encode()))
    assert all(r.merged_into is None for r in result.records)
    assert [r.status for r in result.records] == [RowStatus.SUPPRESSED, RowStatus.SUPPRESSED]
    codes = {i.rule_code for i in result.issues}
    assert {"DUP-007", "OPT-006"} <= codes
    assert _by_name(result).keys() == {"Ana Hoxha", "Zef Prenga"}
