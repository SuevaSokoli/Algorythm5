"""Shared helpers for cleaning unit tests. All data here is invented."""

from __future__ import annotations

from datetime import date

import pytest

from algorythm5.cleaning.models import PipelineSettings, Record

TODAY = date(2026, 11, 20)


def make_record(record_id: str = "R00001", source_row: int = 2, **values: str) -> Record:
    return Record(record_id, source_row, dict(values), {}, source_rows=(source_row,))


def make_record_with_extras(extras: dict[str, str], **values: str) -> Record:
    return Record("R00001", 2, dict(values), dict(extras), source_rows=(2,))


@pytest.fixture
def settings() -> PipelineSettings:
    return PipelineSettings(today=TODAY)
