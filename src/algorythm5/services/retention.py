"""Deletes expired jobs together with their files (CLAUDE.md §6 retention)."""

from __future__ import annotations

import logging
from datetime import datetime

from algorythm5.db.connection import Database
from algorythm5.db.repositories import JobRepository
from algorythm5.storage.files import FileStorage

logger = logging.getLogger(__name__)


def purge_expired(database: Database, storage: FileStorage, now: datetime) -> int:
    """Delete every job whose `expires_at` has passed; returns how many were deleted."""
    with database.transaction() as connection:
        expired = JobRepository(connection).expired_ids(now.isoformat())
    for job_id in expired:
        storage.delete_job_files(job_id)
        with database.transaction() as connection:
            JobRepository(connection).delete(job_id)
    if expired:
        logger.info("retention purge deleted %d job(s)", len(expired))
    return len(expired)
