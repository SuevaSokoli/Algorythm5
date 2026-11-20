"""Repository, storage and retention tests against a temporary SQLite database."""

import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from algorythm5.db.connection import Database
from algorythm5.db.repositories import (
    ChangeRepository,
    ChangeRow,
    IssueRepository,
    IssueRow,
    JobRepository,
    JobRow,
    MappingRepository,
    MappingRow,
    OutputFileRepository,
    OutputFileRow,
    RecordRepository,
    RecordRow,
)
from algorythm5.services.retention import purge_expired
from algorythm5.storage.files import FileStorage, validated_job_id

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.migrate()
    return db


def make_job(job_id: str | None = None, **overrides: object) -> JobRow:
    job = JobRow(
        id=job_id or str(uuid.uuid4()),
        original_filename="customers.csv",
        file_kind="csv",
        stored_upload_path="uploads/x.csv",
        detected_encoding="utf-8",
        detected_delimiter=",",
        sheet_name=None,
        header_row_index=0,
        status="uploaded",
        error_code=None,
        settings_json="{}",
        ruleset_version="1.0.0",
        rows_in=None,
        rows_out=None,
        created_at=NOW.isoformat(),
        started_at=None,
        completed_at=None,
        expires_at=(NOW + timedelta(days=7)).isoformat(),
    )
    return replace(job, **overrides)  # type: ignore[arg-type]


def test_migrations_are_idempotent(database: Database) -> None:
    assert database.migrate() == []
    with database.transaction() as connection:
        versions = [r[0] for r in connection.execute("SELECT version FROM schema_migrations")]
        assert versions == [1, 2]
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_job_crud_and_transitions(database: Database) -> None:
    job = make_job()
    with database.transaction() as connection:
        jobs = JobRepository(connection)
        jobs.insert(job)
        assert jobs.get(job.id) == job
        assert jobs.transition(job.id, ["uploaded"], {"status": "mapped"})
        assert not jobs.transition(job.id, ["uploaded"], {"status": "processing"})
        jobs.update(job.id, {"rows_in": 5})
        stored = jobs.get(job.id)
        assert stored is not None and (stored.status, stored.rows_in) == ("mapped", 5)
        with pytest.raises(ValueError, match="not updatable"):
            jobs.update(job.id, {"id": "x"})
        assert jobs.get("missing") is None


def test_check_constraints_reject_bad_status(database: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError), database.transaction() as connection:
        JobRepository(connection).insert(make_job(status="weird"))


def test_cascade_delete_and_children(database: Database) -> None:
    job = make_job()
    with database.transaction() as connection:
        JobRepository(connection).insert(job)
        MappingRepository(connection).replace(
            job.id,
            [MappingRow(0, "Email", "email", "auto", 1.0), MappingRow(1, "X", None, "auto", 0)],
        )
        RecordRepository(connection).insert_many(
            job.id,
            [
                RecordRow("R00001", 2, "{}", "{}", "FIXED", "unknown", None, "2; 3"),
                RecordRow("R00002", 3, "{}", "{}", "MERGED", "unknown", "R00001", "3"),
            ],
        )
        ChangeRepository(connection).insert_many(
            job.id, [ChangeRow("R00001", 2, "email", "A", "a", "EML-001", 1)]
        )
        IssueRepository(connection).insert_many(
            job.id,
            [
                IssueRow("R00001", "phone", "PHN-007", "warning", "bad", "1", None, ""),
                IssueRow("R00002", "phone", "PHN-007", "warning", "bad", "1", None, ""),
                IssueRow("R00001", None, "OPT-003", "info", "unknown", None, None, ""),
                IssueRow(None, "Col", "ING-006", "blocking", "job", None, None, ""),
            ],
        )
        OutputFileRepository(connection).upsert(
            job.id, OutputFileRow("clean_csv", "outputs/x/clean_csv.csv", "c.csv", 3, "ab", "t")
        )
    with database.transaction() as connection:
        assert [m.target_field for m in MappingRepository(connection).list(job.id)] == [
            "email",
            None,
        ]
        assert RecordRepository(connection).status_counts(job.id) == {"FIXED": 1, "MERGED": 1}
        assert set(RecordRepository(connection).get_many(job.id, ["R00001", "R00009"])) == {
            "R00001"
        }
        assert RecordRepository(connection).get_many(job.id, []) == {}
        review = IssueRepository(connection).review_page(job.id, 1, 50)
        # merged record's issue and the info issue are excluded; job-level blocking kept
        assert [(i.record_id, i.rule_code) for i in review.items] == [
            (None, "ING-006"),
            ("R00001", "PHN-007"),
        ]
        assert review.total == 2
        assert (
            IssueRepository(connection).review_page(job.id, 1, 50, severity="blocking").total == 1
        )
        assert (
            IssueRepository(connection).review_page(job.id, 1, 50, rule_code="PHN-007").total == 1
        )
        assert IssueRepository(connection).rule_counts(job.id)["PHN-007"] == 2
        changes = ChangeRepository(connection).page(
            job.id, 1, 10, rule_code="EML-001", field="email"
        )
        assert changes.total == 1 and changes.items[0].new_value == "a"
        assert ChangeRepository(connection).page(job.id, 2, 10).items == []
        assert ChangeRepository(connection).rule_counts(job.id) == {"EML-001": 1}
        assert OutputFileRepository(connection).get(job.id, "clean_csv") is not None
        assert OutputFileRepository(connection).get(job.id, "review_csv") is None
        assert len(OutputFileRepository(connection).list(job.id)) == 1
        assert JobRepository(connection).delete(job.id)
    with database.transaction() as connection:
        for table in ("column_mappings", "records", "changes", "issues", "output_files"):
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            assert count == 0, table


def test_storage_paths_are_confined(tmp_path: Path) -> None:
    storage = FileStorage(tmp_path)
    job_id = str(uuid.uuid4())
    relative = storage.save_upload(job_id, "csv", b"a,b")
    assert relative == f"uploads/{job_id}.csv"
    assert storage.read(relative) == b"a,b"
    output = storage.save_output(job_id, "clean_csv", "csv", b"x")
    assert storage.absolute(output).is_file()
    with pytest.raises(ValueError):
        storage.read("../outside.txt")
    with pytest.raises(ValueError):
        storage.save_upload(job_id, "exe", b"")
    with pytest.raises(ValueError):
        storage.save_output(job_id, "../x", "csv", b"")
    storage.delete_job_files(job_id)
    assert not (tmp_path / "uploads" / f"{job_id}.csv").exists()
    assert not (tmp_path / "outputs" / job_id).exists()


@pytest.mark.parametrize(
    "bad", ["../../etc/passwd", "not-a-uuid", "6ba7b810-9dad-11d1-80b4-00c04fd430c8", ""]
)
def test_validated_job_id_rejects_non_uuid4(bad: str) -> None:
    with pytest.raises(ValueError):
        validated_job_id(bad)


def test_retention_purges_only_expired(database: Database, tmp_path: Path) -> None:
    storage = FileStorage(tmp_path / "data")
    old = make_job(expires_at=(NOW - timedelta(seconds=1)).isoformat())
    fresh = make_job()
    storage.save_upload(old.id, "csv", b"x")
    with database.transaction() as connection:
        JobRepository(connection).insert(old)
        JobRepository(connection).insert(fresh)
    assert purge_expired(database, storage, NOW) == 1
    with database.transaction() as connection:
        assert JobRepository(connection).get(old.id) is None
        assert JobRepository(connection).get(fresh.id) is not None
    assert not (tmp_path / "data" / "uploads" / f"{old.id}.csv").exists()
    assert purge_expired(database, storage, NOW) == 0


def test_transaction_rolls_back_on_error(database: Database) -> None:
    job = make_job()
    with pytest.raises(RuntimeError), database.transaction() as connection:
        JobRepository(connection).insert(job)
        raise RuntimeError("boom")
    with database.transaction() as connection:
        assert JobRepository(connection).get(job.id) is None
