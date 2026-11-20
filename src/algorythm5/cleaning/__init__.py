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
    Table,
)
from algorythm5.cleaning.pipeline import run_pipeline
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
    "Table",
    "build_outputs",
    "get_rule",
    "has_contact_field",
    "ingest",
    "propose_mapping",
    "review_issues",
    "run_pipeline",
    "status_counts",
    "validate_targets",
]
