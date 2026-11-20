"""Stage 9: one status per record (first match wins, CLAUDE.md §5 status table)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from algorythm5.cleaning.models import Change, ConsentStatus, Issue, Record, RowStatus, Severity

REJECTION_CODES = frozenset({"ROW-001", "ROW-002"})
CLEAN_FILE_STATUSES = frozenset({RowStatus.OK, RowStatus.FIXED, RowStatus.REVIEW})


def derive_status(record: Record, issues: Sequence[Issue], changed: bool) -> RowStatus:
    if record.merged_into:
        return RowStatus.MERGED
    if record.get("consent_status") == ConsentStatus.OPTED_OUT:
        return RowStatus.SUPPRESSED
    if any(i.rule_code in REJECTION_CODES for i in issues):
        return RowStatus.REJECTED
    if any(i.severity == Severity.BLOCKING for i in issues):
        return RowStatus.BLOCKED
    if any(i.severity == Severity.WARNING for i in issues):
        return RowStatus.REVIEW
    return RowStatus.FIXED if changed else RowStatus.OK


def is_review_item(issue: Issue, status: RowStatus | None) -> bool:
    """Warnings and blocking issues of records that still matter go on the review list."""
    return issue.severity != Severity.INFO and status != RowStatus.MERGED


def assign_statuses(
    records: Sequence[Record], changes: Sequence[Change], issues: Sequence[Issue]
) -> tuple[Record, ...]:
    issues_by_record: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        if issue.record_id:
            issues_by_record[issue.record_id].append(issue)
    changed = {c.record_id for c in changes}
    return tuple(
        replace(
            r,
            status=derive_status(r, issues_by_record[r.record_id], r.record_id in changed),
        )
        for r in records
    )
