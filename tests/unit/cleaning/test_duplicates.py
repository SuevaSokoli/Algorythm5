from algorythm5.cleaning import duplicates
from algorythm5.cleaning.models import PipelineSettings, Record, Severity
from tests.unit.cleaning.conftest import make_record


def rec(record_id: str, row: int, **values: str) -> Record:
    defaults = {"consent_status": "unknown"}
    return make_record(record_id, row, **{**defaults, **values})


def _by_id(records: tuple[Record, ...]) -> dict[str, Record]:
    return {r.record_id: r for r in records}


def test_t1_same_email_auto_merges_and_fills(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", last_name="Hoxha", email="ana@gmail.com"),
        rec("R00002", 3, first_name="Ana", last_name="Hoxha", email="ana@gmail.com",
            phone="+355692345678", city="Tiranë"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    merged = _by_id(result.records)
    survivor = merged["R00002"]  # more filled fields
    assert survivor.source_rows == (2, 3)
    assert merged["R00001"].merged_into == "R00002"
    assert {i.rule_code for i in result.issues} == {"DUP-001"}


def test_t1_matches_email_other(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", email="", email_other="ana@gmail.com"),
        rec("R00002", 3, first_name="Ana", email="ana@gmail.com"),
    ]
    assert duplicates.classify_pair(records[0], records[1]) == "T1"


def test_t2_same_phone_similar_name(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ërmira", last_name="Hoxha", phone="+355692345678"),
        rec("R00002", 3, first_name="Ermira", last_name="Hoxha", phone="+355692345678",
            email="ermira@gmail.com"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    assert _by_id(result.records)["R00001"].merged_into == "R00002"
    assert "DUP-002" in {i.rule_code for i in result.issues}


def test_shared_phone_different_names_not_merged(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", last_name="Hoxha", phone="+355692345678"),
        rec("R00002", 3, first_name="Besnik", last_name="Hoxha", phone="+355692345678"),
    ]
    result = duplicates.apply(records, settings)
    assert all(r.merged_into is None for r in result.records)
    assert [i.rule_code for i in result.issues] == ["DUP-005", "DUP-005"]


def test_t3_probable_duplicate_is_review_only(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Arben", last_name="Kola", email="a1@gmail.com", city="Fier"),
        rec("R00002", 3, first_name="Arben", last_name="Kolla", email="a2@gmail.com", city="FIER"),
    ]
    result = duplicates.apply(records, settings)
    assert all(r.merged_into is None for r in result.records)
    issues = [i for i in result.issues if i.rule_code == "DUP-003"]
    assert [(i.record_id, i.related_record_ids, i.severity) for i in issues] == [
        ("R00001", ("R00002",), Severity.WARNING),
        ("R00002", ("R00001",), Severity.WARNING),
    ]


def test_similar_names_without_supporting_field_are_ignored(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Arben", last_name="Kola", email="a1@gmail.com"),
        rec("R00002", 3, first_name="Arben", last_name="Kola", email="a2@gmail.com"),
    ]
    assert duplicates.apply(records, settings).issues == ()


def test_brake_different_primary_emails(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", last_name="Hoxha", email="ana@gmail.com",
            phone="+355692345678"),
        rec("R00002", 3, first_name="Ana", last_name="Hoxha", email="ana.h@yahoo.com",
            phone="+355692345678"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    assert all(r.merged_into is None for r in result.records)
    assert {i.rule_code for i in result.issues} == {"DUP-004"}


def test_same_email_different_names_is_not_merged(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", last_name="Hoxha", email="shared@gmail.com"),
        rec("R00002", 3, first_name="Zef", last_name="Prenga", email="shared@gmail.com"),
    ]
    result = duplicates.apply(records, settings)
    assert all(r.merged_into is None for r in result.records)
    assert {i.rule_code for i in result.issues} == {"DUP-007"}
    assert "names are too different" in result.issues[0].message


def test_brake_cluster_too_large(settings: PipelineSettings) -> None:
    records = [
        rec(f"R{i:05d}", i + 1, first_name="Ana", email="ana@gmail.com") for i in range(1, 7)
    ]
    result = duplicates.apply(records, settings)
    assert all(r.merged_into is None for r in result.records)
    assert len([i for i in result.issues if i.rule_code == "DUP-004"]) == 6


def test_merge_rules_conflicts_and_consent(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", last_name="Hoxha", email="ana@gmail.com",
            phone="+355692345678", city="Tiranë", signup_date="2022-05-01",
            consent_status="subscribed"),
        rec("R00002", 3, first_name="Ana", last_name="Hoxha", email="ana@gmail.com",
            phone="+355687654321", city="Durrës", signup_date="2021-01-01",
            consent_status="opted_out", company="Alba"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    survivor = _by_id(result.records)["R00002"]  # has company, so more fields
    assert survivor.get("phone") == "+355687654321"
    assert survivor.get("phone_other") == "+355692345678"
    assert survivor.get("signup_date") == "2021-01-01"
    assert survivor.get("city") == "Durrës"
    assert survivor.get("consent_status") == "opted_out"
    conflict = next(i for i in result.issues if i.rule_code == "DUP-006")
    assert (conflict.field, conflict.original_value) == ("city", "Tiranë")


def test_earliest_signup_date_wins_when_survivor_is_later(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", email="ana@gmail.com", signup_date="2019-01-01"),
        rec("R00002", 3, first_name="Ana", email="ana@gmail.com", signup_date="2022-01-01",
            city="Fier"),
    ]  # fmt: skip
    survivor = _by_id(duplicates.apply(records, settings).records)["R00002"]
    assert survivor.get("signup_date") == "2019-01-01"


def test_survivor_tie_goes_to_lowest_row() -> None:
    a = rec("R00001", 2, email="a@b.al")
    b = rec("R00002", 3, email="a@b.al")
    assert duplicates.choose_survivor([b, a]) is a


def test_transitive_cluster_merges_once(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Ana", last_name="Hoxha", email="ana@gmail.com"),
        rec("R00002", 3, first_name="Ana", last_name="Hoxha", email="ana@gmail.com",
            phone="+355692345678"),
        rec("R00003", 4, first_name="Ana", last_name="Hoxha", phone="+355692345678",
            city="Tiranë", company="X"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    survivors = [r for r in result.records if r.merged_into is None]
    assert len(survivors) == 1
    assert survivors[0].source_rows == (2, 3, 4)


def test_passthrough_values_are_merged(settings: PipelineSettings) -> None:
    a = Record("R00001", 2, {"email": "a@b.al", "first_name": "Ana", "city": "Fier",
                             "consent_status": "unknown"}, {"Segment": ""}, (2,))  # fmt: skip
    b = Record("R00002", 3, {"email": "a@b.al", "first_name": "Ana",
                             "consent_status": "unknown"}, {"Segment": "VIP"}, (3,))  # fmt: skip
    survivor = _by_id(duplicates.apply([a, b], settings).records)["R00001"]
    assert survivor.extras["Segment"] == "VIP"


def test_review_pair_points_to_survivor_after_merge(settings: PipelineSettings) -> None:
    records = [
        rec("R00001", 2, first_name="Arben", last_name="Kola", email="a1@gmail.com", city="Fier"),
        rec("R00002", 3, first_name="Arben", last_name="Kola", email="a1@gmail.com",
            city="Fier", company="X"),
        rec("R00003", 4, first_name="Arben", last_name="Kolla", email="a3@gmail.com", city="Fier"),
    ]  # fmt: skip
    result = duplicates.apply(records, settings)
    review = [i for i in result.issues if i.rule_code == "DUP-003"]
    assert {i.record_id for i in review} == {"R00002", "R00003"}


def test_blocking_keys() -> None:
    record = rec("R00001", 2, first_name="Ërmira", last_name="Çela", email="e@b.al",
                 phone="+355692345678", phone_other="12345; +355687654321")  # fmt: skip
    assert duplicates.blocking_keys(record) == {
        "e:e@b.al", "p:+355692345678", "p:+355687654321", "n:cele",
    }  # fmt: skip
