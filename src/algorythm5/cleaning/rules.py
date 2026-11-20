"""Rule-code registry (single definition of every code) and the stage event collector."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from algorythm5.cleaning.models import Change, Flag, Issue, Record, Severity, StageResult, Step

# Bump on ANY change to cleaning behaviour (see CLAUDE.md §5 and §12).
RULESET_VERSION = "1.2.0"

INFO, WARN, BLOCK = Severity.INFO, Severity.WARNING, Severity.BLOCKING


@dataclass(frozen=True, slots=True)
class Rule:
    code: str
    severity: Severity
    explanation: str


_RULE_TABLE: tuple[tuple[str, Severity, str], ...] = (
    ("ING-001", INFO, "File was not UTF-8; text was converted"),
    ("ING-003", INFO, "Header row was found below the first line"),
    ("ING-004", INFO, "Empty rows were removed"),
    ("ING-005", INFO, "Several sheets had data; the first visible one was used"),
    ("ING-006", WARN, "Row had more cells than headers; extra cells kept in _overflow"),
    ("ING-008", WARN, "Cell was too long and was shortened"),
    ("ING-009", INFO, "No header row found; columns were recognized from their values"),
    ("ING-901", BLOCK, "The file could not be read. Please save it as .xlsx or .csv"),
    ("ING-902", BLOCK, "The file is too large (size, rows or columns)"),
    ("TXT-001", INFO, "Garbled characters repaired"),
    ("TXT-002", INFO, "Invisible characters removed"),
    ("TXT-003", INFO, "Extra spaces removed"),
    ("TXT-004", INFO, "Apostrophe normalized"),
    ("MAP-001", INFO, "Column matched automatically"),
    ("MAP-002", INFO, "Column not matched; kept as an extra column"),
    ("MAP-003", INFO, "Column header did not match its values; matched by its values"),
    ("MAP-004", INFO, "Column repeats a field that is already used; kept as an extra column"),
    ("MAP-901", BLOCK, "No email or phone column was chosen"),
    ("SRC-001", INFO, "The same file was uploaded twice; the copy was ignored"),
    ("PLC-001", INFO, "Placeholder value removed"),
    ("NAM-001", INFO, "Capitalization fixed"),
    ("NAM-002", INFO, "Title moved to salutation"),
    ("NAM-003", INFO, "Name suffix normalized"),
    ("NAM-004", INFO, "Full name split into first and last name"),
    ("NAM-005", WARN, "Name contains digits or unusual symbols"),
    ("NAM-006", WARN, "First and last name are identical"),
    ("NAM-007", WARN, "Name has lost characters (? or �)"),
    ("NAM-008", WARN, "Full name split is uncertain"),
    ("NAM-009", INFO, "First name is only an initial"),
    ("PHN-001", INFO, "Phone formatted to international format"),
    ("PHN-002", WARN, "Phone damaged by Excel scientific notation; digits are lost"),
    ("PHN-003", INFO, "Excel '.0' removed from phone"),
    ("PHN-004", INFO, "Missing + or leading 0 restored"),
    ("PHN-005", INFO, "Extension moved to phone_extension"),
    ("PHN-006", INFO, "Several phones in one cell were split"),
    ("PHN-007", WARN, "Phone number is not valid"),
    ("PHN-008", INFO, "00 prefix converted to +"),
    ("EML-001", INFO, "Email trimmed and lowercased"),
    ("EML-002", INFO, "mailto:, display name or punctuation removed"),
    ("EML-003", INFO, "Email typo fixed"),
    ("EML-004", WARN, "Unknown email domain, possibly a typo"),
    ("EML-005", WARN, "Email address is not valid"),
    ("EML-006", INFO, "Several emails in one cell were split"),
    ("EML-007", INFO, "Role address (info@, office@, ...)"),
    ("DAT-001", INFO, "Date converted to YYYY-MM-DD"),
    ("DAT-002", INFO, "Excel serial number converted to a date"),
    ("DAT-003", INFO, "Day/month order was ambiguous"),
    ("DAT-004", WARN, "Date could not be read"),
    ("DAT-005", WARN, "Date is implausible"),
    ("OPT-001", INFO, "Opted out; removed from the newsletter"),
    ("OPT-002", WARN, "Opt-out wording found in notes; removed as a precaution"),
    ("OPT-003", INFO, "Consent is unknown"),
    ("OPT-004", WARN, "Opt-out or consent value not recognized"),
    ("OPT-005", WARN, "Opt-out and consent columns contradict each other"),
    ("OPT-006", WARN, "This email address opted out in another row; removed as well"),
    ("DUP-001", INFO, "Merged with a duplicate: same email"),
    ("DUP-002", INFO, "Merged with a duplicate: same phone and similar name"),
    ("DUP-003", WARN, "Possible duplicate, not merged"),
    ("DUP-004", WARN, "Duplicate group too uncertain to merge automatically"),
    ("DUP-005", INFO, "Shares a phone number with a different person"),
    ("DUP-006", INFO, "Conflicting values while merging; kept the more complete or newer one"),
    ("DUP-007", WARN, "Same email address used by people with different names; not merged"),
    ("DUP-008", WARN, "Same name in another file but different details; kept as different people"),
    ("DUP-009", INFO, "Merged with the same person from another file: same name, matching details"),
    ("ROW-001", BLOCK, "No usable email or phone"),
    ("ROW-002", BLOCK, "No email address, so it cannot receive the newsletter"),
)

RULES: dict[str, Rule] = {code: Rule(code, sev, text) for code, sev, text in _RULE_TABLE}


def get_rule(code: str) -> Rule:
    try:
        return RULES[code]
    except KeyError as exc:  # programming error: every code must be registered
        raise KeyError(f"unregistered rule code {code}") from exc


def is_job_level(code: str) -> bool:
    """Codes x9xx stop the whole job."""
    return code.split("-")[1].startswith("9")


class StageLog:
    """Collects changes and issues for one stage, filling messages from the registry."""

    def __init__(self) -> None:
        self._changes: list[Change] = []
        self._issues: list[Issue] = []

    def change(self, record: Record, field: str, old: str, new: str, code: str) -> None:
        get_rule(code)
        if old != new:
            self._changes.append(
                Change(
                    record.record_id, record.source_row, field, old, new, code, record.source_file
                )
            )

    def steps(self, record: Record, field: str, steps: Iterable[Step]) -> None:
        for step in steps:
            self.change(record, field, step.old, step.new, step.rule_code)

    def issue(
        self,
        record: Record | None,
        field: str | None,
        code: str,
        *,
        severity: Severity | None = None,
        original_value: str | None = None,
        suggestion: str | None = None,
        detail: str | None = None,
        related: Sequence[str] = (),
    ) -> None:
        rule = get_rule(code)
        message = f"{rule.explanation}: {detail}" if detail else rule.explanation
        self._issues.append(
            Issue(
                record_id=record.record_id if record else None,
                field=field,
                rule_code=code,
                severity=severity or rule.severity,
                message=message,
                original_value=original_value,
                suggestion=suggestion,
                related_record_ids=tuple(related),
            )
        )

    def flags(self, record: Record, field: str, flags: Iterable[Flag]) -> None:
        for flag in flags:
            self.issue(
                record,
                field,
                flag.rule_code,
                severity=flag.severity,
                original_value=flag.original_value,
                suggestion=flag.suggestion,
                detail=flag.detail,
            )

    def result(self, records: Iterable[Record]) -> StageResult:
        return StageResult(tuple(records), tuple(self._changes), tuple(self._issues))
