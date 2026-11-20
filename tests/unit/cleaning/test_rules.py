import re
from pathlib import Path

import pytest

from algorythm5.cleaning import rules
from algorythm5.cleaning.models import Severity
from tests.unit.cleaning.conftest import make_record

SOURCE_DIR = Path(rules.__file__).parent


def test_every_code_used_in_source_is_registered() -> None:
    used: set[str] = set()
    for path in SOURCE_DIR.glob("*.py"):
        used |= set(re.findall(r'"([A-Z]{3}-\d{3})"', path.read_text(encoding="utf-8")))
    assert used <= set(rules.RULES), used - set(rules.RULES)


def test_codes_are_well_formed_and_unique() -> None:
    codes = [code for code, _, _ in rules._RULE_TABLE]
    assert len(codes) == len(set(codes))
    assert all(
        re.fullmatch(r"(ING|TXT|MAP|PLC|NAM|PHN|EML|DAT|OPT|DUP|ROW)-\d{3}", c) for c in codes
    )


def test_job_level_codes() -> None:
    assert rules.is_job_level("ING-901")
    assert rules.is_job_level("MAP-901")
    assert not rules.is_job_level("ING-001")


def test_unknown_code_is_a_programming_error() -> None:
    with pytest.raises(KeyError):
        rules.get_rule("XXX-000")


def test_stage_log_skips_no_op_changes_and_formats_messages() -> None:
    log = rules.StageLog()
    record = make_record()
    log.change(record, "city", "a", "a", "TXT-003")
    log.issue(record, "phone", "PHN-007", detail="12", severity=Severity.BLOCKING)
    result = log.result([record])
    assert result.changes == ()
    assert result.issues[0].message == "Phone number is not valid: 12"
    assert result.issues[0].severity == Severity.BLOCKING
