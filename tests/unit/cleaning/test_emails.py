import pytest

from algorythm5.cleaning import emails
from algorythm5.cleaning.models import PipelineSettings, Severity
from tests.unit.cleaning.conftest import make_record


@pytest.mark.parametrize(
    ("raw", "email", "others", "invalid", "codes"),
    [
        ("ana@gmail.com", "ana@gmail.com", [], [], []),
        (" ANA@Gmail.com ", "ana@gmail.com", [], [], ["EML-001"]),
        ("Ana Hoxha <ana@gmail.com>", "ana@gmail.com", [], [], ["EML-002"]),
        ("mailto:ana@gmail.com", "ana@gmail.com", [], [], ["EML-002"]),
        ('"ana@gmail.com"', "ana@gmail.com", [], [], ["EML-002"]),
        ("ana@gmail.com.", "ana@gmail.com", [], [], ["EML-002"]),
        ("ana@gmial.com", "ana@gmail.com", [], [], ["EML-003"]),
        ("ana@gmail.con", "ana@gmail.com", [], [], ["EML-003"]),
        ("ana@hotmial.com", "ana@hotmail.com", [], [], ["EML-003"]),
        ("ana@yaho.com", "ana@yahoo.com", [], [], ["EML-003"]),
        ("ana@outlok.com", "ana@outlook.com", [], [], ["EML-003"]),
        ("ana@shop.cmo", "ana@shop.com", [], [], ["EML-003"]),
        ("ana@gmail,com", "ana@gmail.com", [], [], ["EML-003"]),
        ("ana..b@gmail.com", "ana.b@gmail.com", [], [], ["EML-003"]),
        ("ana@@gmail.com", "ana@gmail.com", [], [], ["EML-003"]),
        ("ana (at) gmail (dot) com", "ana@gmail.com", [], [], ["EML-003"]),
        ("ana [at] gmail.com", "ana@gmail.com", [], [], ["EML-003"]),
        ("ana @ gmail.com", "ana@gmail.com", [], [], ["EML-003"]),
        ("ana@gmail.com; ben@yahoo.com", "ana@gmail.com", ["ben@yahoo.com"], [], ["EML-006"]),
        ("ana@gmail.com, ana@gmail.com", "ana@gmail.com", [], [], ["EML-006"]),
        ("not-an-email", "", [], ["not-an-email"], []),
        ("ana@gmail", "", [], ["ana@gmail"], []),
        ("test@test.com", "", [], [], ["PLC-001"]),
        ("", "", [], [], []),
    ],
)
def test_clean_email_cell(
    raw: str, email: str, others: list[str], invalid: list[str], codes: list[str]
) -> None:
    outcome = emails.clean_email_cell(raw)
    assert outcome.email == email
    assert outcome.others == others
    assert outcome.invalid == invalid
    assert [s.rule_code for s in outcome.steps] == codes


def test_fuzzy_domain_is_suggestion_only() -> None:
    outcome = emails.clean_email_cell("ana@gmaul.com")
    assert outcome.email == "ana@gmaul.com"  # not changed
    assert [(f.rule_code, f.suggestion) for f in outcome.flags] == [("EML-004", "ana@gmail.com")]


def test_known_and_unrelated_domains_get_no_suggestion() -> None:
    assert emails.suggest_domain("ana@gmail.com") is None
    assert emails.suggest_domain("ana@albtelecom-shpk.al") is None
    assert emails.suggest_domain("no-at-sign") is None


def test_role_address_is_flagged_but_kept() -> None:
    outcome = emails.clean_email_cell("info@dyqani.al")
    assert outcome.email == "info@dyqani.al"
    assert [f.rule_code for f in outcome.flags] == ["EML-007"]


def test_typo_fix_only_applied_when_result_is_valid() -> None:
    assert emails.fix_known_typo("nobody") == "nobody"
    assert emails.fix_structure("a@b.al.") == "a@b.al"


def test_stage_invalid_only_channel_is_blocking(settings: PipelineSettings) -> None:
    result = emails.apply([make_record(email="ana@gmail")], settings)
    issue = result.issues[0]
    assert (issue.rule_code, issue.severity) == ("EML-005", Severity.BLOCKING)
    assert result.records[0].get("email_other") == "INVALID: ana@gmail"


def test_stage_invalid_with_phone_is_warning(settings: PipelineSettings) -> None:
    result = emails.apply([make_record(email="ana@gmail", phone="+355692345678")], settings)
    assert result.issues[0].severity == Severity.WARNING


def test_stage_extra_valid_and_invalid_go_to_email_other(settings: PipelineSettings) -> None:
    result = emails.apply([make_record(email="ana@gmail.com; ben@yahoo.com; bad@")], settings)
    record = result.records[0]
    assert record.get("email") == "ana@gmail.com"
    assert record.get("email_other") == "ben@yahoo.com; INVALID: bad@"
    assert result.issues[0].severity == Severity.WARNING  # a valid email exists


def test_stage_clean_value_produces_no_changes(settings: PipelineSettings) -> None:
    result = emails.apply([make_record(email="ana@gmail.com")], settings)
    assert result.changes == ()
    assert result.issues == ()
