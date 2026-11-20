"""Loaders for the versioned reference lists stored next to this file.

Lists are read once per process and cached; blank lines and lines starting with `#` are ignored.
"""

from __future__ import annotations

import csv
from functools import cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent


@cache
def load_lines(name: str) -> tuple[str, ...]:
    """Non-empty, non-comment lines of a text list, stripped."""
    text = (DATA_DIR / name).read_text(encoding="utf-8")
    return tuple(
        line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")
    )


@cache
def load_pairs(name: str) -> tuple[tuple[str, str], ...]:
    """Two-column CSV rows (header row skipped)."""
    lines = load_lines(name)
    rows = list(csv.reader(lines[1:]))
    return tuple((row[0].strip(), row[1].strip()) for row in rows if len(row) >= 2)
