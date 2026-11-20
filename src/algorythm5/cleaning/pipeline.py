"""Runs the cleaning stages in the order fixed by CLAUDE.md §5 and collects their output."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace

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
    SourceFile,
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


def find_duplicate_sources(tables: Sequence[Table]) -> dict[int, int]:
    """Files whose content repeats an earlier file (same headers and cells): index -> original.

    Compares parsed content, so the same data saved once as CSV and once as XLSX also counts.
    """
    seen: dict[tuple[tuple[str, ...], tuple[tuple[str, ...], ...]], int] = {}
    repeats: dict[int, int] = {}
    for index, table in enumerate(tables):
        key = (table.headers, tuple(row.cells for row in table.rows))
        if key in seen:
            repeats[index] = seen[key]
        else:
            seen[key] = index
    return repeats


def _renumber(source: SourceFile, offset: int) -> SourceFile:
    """Give the rows of a later file record IDs that continue after the earlier files."""
    if offset == 0:
        return source
    table = source.table
    ids = {row.record_id: f"R{offset + n:05d}" for n, row in enumerate(table.rows, start=1)}
    rows = tuple(replace(row, record_id=ids[row.record_id]) for row in table.rows)
    issues = tuple(
        replace(i, record_id=ids.get(i.record_id, i.record_id)) if i.record_id else i
        for i in table.issues
    )
    return replace(source, table=replace(table, rows=rows, issues=issues))


def _label_job_issues(issues: Sequence[Issue], label: str, multi: bool) -> list[Issue]:
    """With several files, file-level findings say which file they are about."""
    if not multi or not label:
        return list(issues)
    return [
        replace(i, message=f"{label}: {i.message}") if i.record_id is None else i for i in issues
    ]


def _check_mapping(source: SourceFile) -> None:
    targets = [m.target_field for m in source.mappings]
    mapping.validate_targets(targets)
    if not mapping.has_contact_field(targets):
        raise PipelineError("MAP-901", source.label)


def run_sources(sources: Sequence[SourceFile], settings: PipelineSettings) -> PipelineResult:
    """Clean one or more files into one result. Raises PipelineError for job-level problems.

    Record IDs run on across files in upload order, so "later" always means a later file
    (or a later row in the same file). A file identical to an earlier one is skipped.
    """
    if not sources:
        raise PipelineError("ING-901", "no file")
    multi = len(sources) > 1
    repeats = find_duplicate_sources([s.table for s in sources])
    changes: list[Change] = []
    issues: list[Issue] = []
    built: list[Record] = []
    numbered: list[SourceFile] = []
    offset = 0
    for index, original in enumerate(sources):
        source = _renumber(original, offset)
        offset += len(source.table.rows)
        numbered.append(source)
        if index in repeats:
            log = StageLog()
            detail = f"{source.label} (same as {sources[repeats[index]].label})"
            log.issue(None, None, "SRC-001", detail=detail)
            issues.extend(log.result(()).issues)
            continue
        _check_mapping(source)
        result = mapping.build_records(source.table, source.mappings, source.label)
        issues.extend(
            _label_job_issues((*source.table.issues, *result.issues), source.label, multi)
        )
        changes.extend(result.changes)
        built.extend(result.records)

    def collect(result: StageResult) -> tuple[Record, ...]:
        changes.extend(result.changes)
        issues.extend(result.issues)
        return result.records

    records: tuple[Record, ...] = tuple(built)
    for stage in (*FIELD_STAGES, validate_rows, duplicates.apply, optouts.propagate_by_email):
        records = collect(stage(records, settings))
    final_issues = _drop_stale_issues(records, issues)
    records = assign_statuses(records, changes, final_issues)
    return PipelineResult(
        sources=tuple(numbered),
        records=records,
        changes=tuple(changes),
        issues=tuple(final_issues),
        settings=settings,
        ruleset_version=RULESET_VERSION,
        duplicate_sources=repeats,
    )


def run_pipeline(
    table: Table, mappings: Sequence[ColumnMapping], settings: PipelineSettings, label: str = ""
) -> PipelineResult:
    """Clean a single table with a confirmed mapping (see run_sources)."""
    return run_sources([SourceFile(label, table, tuple(mappings))], settings)
