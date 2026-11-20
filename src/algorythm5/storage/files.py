"""Upload and output files under DATA_DIR. Paths are built only from validated job UUIDs."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

UPLOADS = "uploads"
OUTPUTS = "outputs"
ALLOWED_EXTENSIONS = frozenset({"csv", "xlsx"})


def validated_job_id(job_id: str) -> str:
    """Canonical UUID4 string; raises ValueError for anything else (blocks path traversal)."""
    parsed = uuid.UUID(job_id)
    if parsed.version != 4 or str(parsed) != job_id.lower():
        raise ValueError("job id must be a canonical UUID4")
    return str(parsed)


class FileStorage:
    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir.resolve()

    def _inside_root(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("path escapes the data directory")
        return path

    def save_upload(self, job_id: str, extension: str, data: bytes) -> str:
        if extension not in ALLOWED_EXTENSIONS:
            raise ValueError(f"unsupported extension {extension}")
        relative = f"{UPLOADS}/{validated_job_id(job_id)}.{extension}"
        self._write(relative, data)
        return relative

    def save_output(self, job_id: str, kind: str, extension: str, data: bytes) -> str:
        if not kind.replace("_", "").isalnum() or extension not in ALLOWED_EXTENSIONS:
            raise ValueError("invalid output name")
        relative = f"{OUTPUTS}/{validated_job_id(job_id)}/{kind}.{extension}"
        self._write(relative, data)
        return relative

    def read(self, relative: str) -> bytes:
        return self._inside_root(relative).read_bytes()

    def absolute(self, relative: str) -> Path:
        return self._inside_root(relative)

    def delete_job_files(self, job_id: str) -> None:
        job = validated_job_id(job_id)
        for upload in (self.root / UPLOADS).glob(f"{job}.*"):
            upload.unlink(missing_ok=True)
        shutil.rmtree(self.root / OUTPUTS / job, ignore_errors=True)

    def _write(self, relative: str, data: bytes) -> None:
        path = self._inside_root(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
