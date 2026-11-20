"""Runs the cleaning stages in the order fixed by CLAUDE.md §5 and collects their output."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from algorythm5.cleaning import (
    dates,
    duplicates,
    emails,
    mapping,
    names,
    optouts,
    phones,
    placeholders,
    text,
)
from algorythm5.cleaning.models import (
    Change,
    ColumnMapping,
    ConsentStatus,
    Issue,
    PipelineError,
    PipelineResult,
    PipelineSettings,
    Record,
    StageResult,
    Table,
)
from algorythm5.cleaning.rules import RULESET_VERSION, StageLog
from algorythm5.cleaning.status import assign_statuses

Stage = Callable[[Sequence[Record], PipelineSettings], StageResult]

# Phones run before emails so an invalid email can be judged against the phone channel.
FIELD_STAGES: tuple[Stage, ...] = (
    text.apply,
    placeholders.apply,
    phones.apply,
    emails.apply,
    names.apply,
    dates.apply,
    optouts.apply,
)


def validate_rows(records: Sequence[Record], settings: PipelineSettings) -> StageResult:
    """Stage 7: every row needs a usable channel (and an email when email is required)."""
    log = StageLog()
    for record in records:
        if not record.get("email") and not record.get("phone"):
            log.issue(record, None, "ROW-001")
        elif settings.required_channel == "email" and not record.get("email"):
            log.issue(record, "email", "ROW-002")
    return log.result(records)


def _drop_stale_issues(records: Sequence[Record], issues: Sequence[Issue]) -> list[Issue]:
    """Merging can fill a survivor's gaps; drop findings the merged values have resolved."""
    by_id = {r.record_id: r for r in records}

    def resolved(issue: Issue) -> bool:
        record = by_id.get(issue.record_id or "")
        if record is None or record.merged_into:
            return False
        if issue.rule_code == "OPT-003":
            return record.get("consent_status") != ConsentStatus.UNKNOWN
        if issue.rule_code == "ROW-001":
            return bool(record.get("email") or record.get("phone"))
        if issue.rule_code == "ROW-002":
            return bool(record.get("email"))
        return False

    return [i for i in issues if not resolved(i)]


def run_pipeline(
    table: Table, mappings: Sequence[ColumnMapping], settings: PipelineSettings
) -> PipelineResult:
    """Clean a table with a confirmed mapping. Raises PipelineError for job-level problems."""
    targets = [m.target_field for m in mappings]
    mapping.validate_targets(targets)
    if not mapping.has_contact_field(targets):
        raise PipelineError("MAP-901")
    changes: list[Change] = []
    issues: list[Issue] = list(table.issues)

    def collect(result: StageResult) -> tuple[Record, ...]:
        changes.extend(result.changes)
        issues.extend(result.issues)
        return result.records

    records = collect(mapping.build_records(table, mappings))
    for stage in (*FIELD_STAGES, validate_rows, duplicates.apply):
        records = collect(stage(records, settings))
    final_issues = _drop_stale_issues(records, issues)
    records = assign_statuses(records, changes, final_issues)
    return PipelineResult(
        table=table,
        mappings=tuple(mappings),
        records=records,
        changes=tuple(changes),
        issues=tuple(final_issues),
        settings=settings,
        ruleset_version=RULESET_VERSION,
    )
