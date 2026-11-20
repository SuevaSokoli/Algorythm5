"""Use cases that orchestrate cleaning, database and storage.

`AppError` lives here (not in `web/`) so dependencies only point web -> services.
"""

from __future__ import annotations


class AppError(Exception):
    """A domain error with a safe, plain-language message for the client."""

    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
