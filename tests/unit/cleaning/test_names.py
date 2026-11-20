import pytest

from algorythm5.cleaning import names
from algorythm5.cleaning.models import PipelineSettings, Severity
from tests.unit.cleaning.conftest import make_record


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ËRMIRA", "Ërmira"),  # ËRMIRA → Ërmira
        ("ÇELA", "Çela"),  # ÇELA → Çela
        ("XHEMAL", "Xhemal"),
        ("GJERGJ", "Gjergj"),
        ("DHURATA", "Dhurata"),
        ("SHPRESA", "Shpresa"),
        ("MCDONALD", "McDonald"),
        ("MACDONALD", "MacDonald"),
        ("MACEDO", "Macedo"),
        ("MACK", "Mack"),
        ("O'BRIEN", "O'Brien"),
        ("D'ANGELO", "D'Angelo"),
        ("ANDREA'S", "Andrea's"),
        ("ANNA-MARIA", "Anna-Maria"),
        ("LUDWIG VAN BEETHOVEN", "Ludwig van Beethoven"),
        ("DE LUCA", "De Luca"),
        ("ana hoxha", "Ana Hoxha"),
        ("GJERGJ III", "Gjergj III"),
        ("McDonald", "McDonald"),  # mixed case is intentional
        ("van Dijk", "van Dijk"),
        ("DeVito", "DeVito"),
        ("", ""),
        ("123", "123"),
    ],
)
def test_fix_case(raw: str, expected: str) -> None:
    assert names.fix_case(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("aNa", "Ana"),
        ("aNa hOxha", "Ana Hoxha"),
        ("ANa HoXHA", "Ana Hoxha"),
        ("hOxha-kOLA", "Hoxha-Kola"),
        ("ËrMIRA çELA", "Ërmira Çela"),
        ("xHEMAL", "Xhemal"),
        ("o'bRIEN", "O'Brien"),
        ("mcDONALD", "McDonald"),
        ("Ana Hoxha", "Ana Hoxha"),  # already correct: no change
        ("McDonald", "McDonald"),
        ("DiCaprio", "DiCaprio"),
        ("O'Brien", "O'Brien"),
        ("Anna-Maria", "Anna-Maria"),
        ("Ludwig van Beethoven", "Ludwig van Beethoven"),
        ("Gjergj III", "Gjergj III"),
        ("Ana hOxha van Dijk", "Ana Hoxha van Dijk"),
    ],
)
def test_fix_case_repairs_random_mixed_case_in_names(raw: str, expected: str) -> None:
    assert names.fix_case(raw, fix_mixed=True) == expected


def test_mixed_case_outside_names_is_kept() -> None:
    assert names.fix_case("IBM Albania", particles=False) == "IBM Albania"


def test_name_stage_fixes_mixed_case(settings: PipelineSettings) -> None:
    record = make_record(first_name="aNa", last_name="hOxha", company="IBM Albania")
    result = names.apply([record], settings)
    values = result.records[0].values
    assert (values["first_name"], values["last_name"], values["company"]) == (
        "Ana", "Hoxha", "IBM Albania",
    )  # fmt: skip
    assert {c.rule_code for c in result.changes} == {"NAM-001"}


def test_particles_not_lowered_outside_name_fields() -> None:
    assert names.fix_case("RRUGA DE GASPERI", particles=False) == "Rruga De Gasperi"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Smith Jr", "Smith Jr."), ("Smith sr.", "Smith Sr."), ("Gjergj iii", "Gjergj III"),
     ("Jr", "Jr"), ("Hoxha", "Hoxha")],
)  # fmt: skip
def test_normalize_suffix(raw: str, expected: str) -> None:
    assert names.normalize_suffix(raw) == expected


@pytest.mark.parametrize(
    ("raw", "title", "rest"),
    [
        ("Dr. Ana Hoxha", "Dr.", "Ana Hoxha"),
        ("MR John", "Mr.", "John"),
        ("Znj. Drita", "Znj.", "Drita"),
        ("Z. Arben", "Z.", "Arben"),
        ("Z Arben", None, "Z Arben"),  # single letter without dot may be an initial
        ("Dr.", None, "Dr."),
        ("Drita", None, "Drita"),
    ],
)
def test_extract_title(raw: str, title: str | None, rest: str) -> None:
    assert names.extract_title(raw) == (title, rest)


@pytest.mark.parametrize(
    ("raw", "first", "last", "uncertain"),
    [
        ("HOXHA, ANA", "ANA", "HOXHA", False),
        ("Ana Hoxha", "Ana", "Hoxha", False),
        ("Ana Maria Hoxha", "Ana", "Maria Hoxha", False),
        ("Ana", "Ana", "", True),
        ("A B C D", "A", "B C D", True),
        ("Hoxha,", "", "Hoxha", True),
    ],
)
def test_split_full_name(raw: str, first: str, last: str, uncertain: bool) -> None:
    split = names.split_full_name(raw)
    assert (split.first, split.last, split.uncertain) == (first, last, uncertain)


@pytest.mark.parametrize(
    ("value", "code"),
    [("Ana2", "NAM-005"), ("Ana@", "NAM-005"), ("Gj?rgj", "NAM-007"),
     ("Gj�rgj", "NAM-007"), ("Anna-Maria O'Neil Jr.", None)],
)  # fmt: skip
def test_suspicious_reason(value: str, code: str | None) -> None:
    assert names.suspicious_reason(value) == code


def test_stage_full_name_split_title_and_casing(settings: PipelineSettings) -> None:
    record = make_record(full_name="DR. ËRMIRA HOXHA")
    result = names.apply([record], settings)
    cleaned = result.records[0]
    assert cleaned.get("salutation") == "Dr."
    assert cleaned.get("first_name") == "Ërmira"
    assert cleaned.get("last_name") == "Hoxha"
    codes = {(c.field, c.rule_code) for c in result.changes}
    assert ("first_name", "NAM-004") in codes
    assert ("first_name", "NAM-001") in codes
    assert ("salutation", "NAM-002") in codes


def test_stage_separate_fields_win_over_full_name(settings: PipelineSettings) -> None:
    record = make_record(full_name="Ana Hoxha", first_name="Anila", last_name="")
    cleaned = names.apply([record], settings).records[0]
    assert cleaned.get("first_name") == "Anila"
    assert cleaned.get("last_name") == "Hoxha"


def test_stage_uncertain_split_is_flagged(settings: PipelineSettings) -> None:
    result = names.apply([make_record(full_name="Ana Maria Elena Hoxha Kola")], settings)
    issue = next(i for i in result.issues if i.rule_code == "NAM-008")
    assert issue.severity == Severity.WARNING
    assert issue.suggestion == "First name: Ana / Last name: Maria Elena Hoxha Kola"


def test_stage_title_in_first_name_moves_to_salutation(settings: PipelineSettings) -> None:
    cleaned = names.apply([make_record(first_name="Prof. Arben", last_name="Kola")], settings)
    assert cleaned.records[0].get("salutation") == "Prof."
    assert cleaned.records[0].get("first_name") == "Arben"


def test_stage_existing_salutation_is_canonicalised(settings: PipelineSettings) -> None:
    cleaned = names.apply([make_record(salutation="znj", first_name="Drita")], settings)
    assert cleaned.records[0].get("salutation") == "Znj."


def test_stage_flags_suspicious_names(settings: PipelineSettings) -> None:
    result = names.apply(
        [make_record(first_name="Kola", last_name="KOLA"), make_record(first_name="J.")], settings
    )
    codes = sorted(i.rule_code for i in result.issues)
    assert codes == ["NAM-006", "NAM-009"]


def test_stage_cases_city_street_company(settings: PipelineSettings) -> None:
    record = make_record(city="TIRANË", street="RRUGA E DIBRËS", company="alba shpk")
    cleaned = names.apply([record], settings).records[0]
    assert cleaned.get("city") == "Tiranë"
    assert cleaned.get("street") == "Rruga E Dibrës"
    assert cleaned.get("company") == "Alba Shpk"


def test_never_adds_or_removes_diacritics(settings: PipelineSettings) -> None:
    cleaned = names.apply([make_record(first_name="HOXHA", last_name="DIBRË")], settings)
    assert cleaned.records[0].get("first_name") == "Hoxha"
    assert cleaned.records[0].get("last_name") == "Dibrë"


def test_clean_record_produces_no_changes(settings: PipelineSettings) -> None:
    result = names.apply([make_record(first_name="Ana", last_name="Hoxha", city="Tiranë")],
                         settings)  # fmt: skip
    assert result.changes == ()
    assert result.issues == ()
