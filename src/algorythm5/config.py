"""Application settings: the single source of configuration (env vars and `.env`)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import phonenumbers
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BYTES_PER_MB = 1024 * 1024


class Settings(BaseSettings):
    """Validated runtime settings. Field names map to upper-case environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Path("data")
    max_upload_mb: int = Field(default=10, ge=1, le=100)
    max_rows: int = Field(default=50_000, ge=1, le=500_000)
    max_columns: int = Field(default=100, ge=1, le=1_000)
    max_cell_chars: int = Field(default=1_000, ge=10, le=100_000)
    max_xlsx_uncompressed_mb: int = Field(default=100, ge=1, le=1_000)
    default_phone_region: str = "AL"
    unknown_consent_policy: Literal["include", "review", "exclude"] = "include"
    required_channel: Literal["email", "any"] = "email"
    date_order: Literal["DMY", "MDY"] = "DMY"
    csv_delimiter: Literal[",", ";"] = ","
    retention_days: int = Field(default=7, ge=1, le=365)

    @field_validator("default_phone_region")
    @classmethod
    def _known_region(cls, value: str) -> str:
        region = value.strip().upper()
        if region not in phonenumbers.SUPPORTED_REGIONS:
            raise ValueError(f"unknown phone region: {value!r}")
        return region

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * BYTES_PER_MB

    @property
    def max_xlsx_uncompressed_bytes(self) -> int:
        return self.max_xlsx_uncompressed_mb * BYTES_PER_MB

    @property
    def database_path(self) -> Path:
        return self.data_dir / "algorythm5.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings loaded once per process."""
    return Settings()
