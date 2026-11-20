"""Public API of the pure cleaning library (no web, database or file-system access)."""

from algorythm5.cleaning.export import OutputFiles, build_outputs, review_issues, status_counts
from algorythm5.cleaning.ingestion import ingest
from algorythm5.cleaning.mapping import (
    CANONICAL_FIELDS,
    InvalidMappingError,
    has_contact_field,
    propose_mapping,
    validate_targets,
)
from algorythm5.cleaning.models import (
    Change,
    ColumnMapping,
    Issue,
    PipelineError,
    PipelineResult,
    PipelineSettings,
    Record,
    RowStatus,
    Severity,
    SourceFile,
    Table,
)
from algorythm5.cleaning.pipeline import find_duplicate_sources, run_pipeline, run_sources
from algorythm5.cleaning.rules import RULES, RULESET_VERSION, get_rule

__all__ = [
    "CANONICAL_FIELDS",
    "RULES",
    "RULESET_VERSION",
    "Change",
    "ColumnMapping",
    "InvalidMappingError",
    "Issue",
    "OutputFiles",
    "PipelineError",
    "PipelineResult",
    "PipelineSettings",
    "Record",
    "RowStatus",
    "Severity",
    "SourceFile",
    "Table",
    "build_outputs",
    "find_duplicate_sources",
    "get_rule",
    "has_contact_field",
    "ingest",
    "propose_mapping",
    "review_issues",
    "run_pipeline",
    "run_sources",
    "status_counts",
    "validate_targets",
]
