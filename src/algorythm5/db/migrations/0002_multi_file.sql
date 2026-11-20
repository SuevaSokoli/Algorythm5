-- 0002: several files per job, mapping per file, source file per record/change.
-- Never edit after commit; add a new migration instead.

CREATE TABLE job_files (
    job_id             TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    file_index         INTEGER NOT NULL,
    original_filename  TEXT NOT NULL,
    file_kind          TEXT NOT NULL CHECK (file_kind IN ('csv', 'xlsx')),
    stored_upload_path TEXT NOT NULL,
    detected_encoding  TEXT,
    detected_delimiter TEXT,
    sheet_name         TEXT,
    header_row_index   INTEGER,
    rows_in            INTEGER NOT NULL,
    sha256             TEXT NOT NULL,
    duplicate_of       INTEGER,
    PRIMARY KEY (job_id, file_index)
);

INSERT INTO job_files (job_id, file_index, original_filename, file_kind, stored_upload_path,
                       detected_encoding, detected_delimiter, sheet_name, header_row_index,
                       rows_in, sha256, duplicate_of)
SELECT id, 0, original_filename, file_kind, stored_upload_path, detected_encoding,
       detected_delimiter, sheet_name, header_row_index, COALESCE(rows_in, 0), '', NULL
FROM jobs;

CREATE TABLE column_mappings_v2 (
    job_id        TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    file_index    INTEGER NOT NULL DEFAULT 0,
    source_index  INTEGER NOT NULL,
    source_header TEXT NOT NULL,
    target_field  TEXT,
    method        TEXT NOT NULL CHECK (method IN ('auto', 'sniffed', 'manual')),
    confidence    REAL NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, file_index, source_index)
);
INSERT INTO column_mappings_v2 (job_id, file_index, source_index, source_header, target_field,
                                method, confidence, reason)
SELECT job_id, 0, source_index, source_header, target_field, method, confidence, ''
FROM column_mappings;
DROP TABLE column_mappings;
ALTER TABLE column_mappings_v2 RENAME TO column_mappings;

ALTER TABLE records ADD COLUMN source_file TEXT NOT NULL DEFAULT '';
ALTER TABLE changes ADD COLUMN source_file TEXT NOT NULL DEFAULT '';
