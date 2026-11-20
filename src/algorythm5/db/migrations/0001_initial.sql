-- 0001: initial schema (CLAUDE.md §6). Never edit after commit; add a new migration instead.

CREATE TABLE jobs (
    id                 TEXT PRIMARY KEY,
    original_filename  TEXT NOT NULL,
    file_kind          TEXT NOT NULL CHECK (file_kind IN ('csv', 'xlsx')),
    stored_upload_path TEXT NOT NULL,
    detected_encoding  TEXT,
    detected_delimiter TEXT,
    sheet_name         TEXT,
    header_row_index   INTEGER,
    status             TEXT NOT NULL CHECK (status IN
                           ('uploaded', 'mapped', 'processing', 'completed', 'failed')),
    error_code         TEXT,
    settings_json      TEXT NOT NULL,
    ruleset_version    TEXT NOT NULL,
    rows_in            INTEGER,
    rows_out           INTEGER,
    created_at         TEXT NOT NULL,
    started_at         TEXT,
    completed_at       TEXT,
    expires_at         TEXT NOT NULL
);
CREATE INDEX idx_jobs_expires ON jobs (expires_at);

CREATE TABLE column_mappings (
    job_id        TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    source_index  INTEGER NOT NULL,
    source_header TEXT NOT NULL,
    target_field  TEXT,
    method        TEXT NOT NULL CHECK (method IN ('auto', 'sniffed', 'manual')),
    confidence    REAL NOT NULL,
    PRIMARY KEY (job_id, source_index)
);

CREATE TABLE records (
    job_id         TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    record_id      TEXT NOT NULL,
    source_row     INTEGER NOT NULL,
    raw_json       TEXT NOT NULL,
    clean_json     TEXT,
    status         TEXT CHECK (status IN
                       ('OK', 'FIXED', 'REVIEW', 'BLOCKED', 'REJECTED', 'SUPPRESSED', 'MERGED')),
    consent_status TEXT CHECK (consent_status IN ('subscribed', 'opted_out', 'unknown')),
    merged_into    TEXT,
    source_rows    TEXT,
    PRIMARY KEY (job_id, record_id)
);
CREATE INDEX idx_records_status ON records (job_id, status);

CREATE TABLE changes (
    id          INTEGER PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    record_id   TEXT NOT NULL,
    source_row  INTEGER NOT NULL,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    rule_code   TEXT NOT NULL,
    seq         INTEGER NOT NULL
);
CREATE INDEX idx_changes_job_rule ON changes (job_id, rule_code);

CREATE TABLE issues (
    id                 INTEGER PRIMARY KEY,
    job_id             TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    record_id          TEXT,
    field              TEXT,
    rule_code          TEXT NOT NULL,
    severity           TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'blocking')),
    message            TEXT NOT NULL,
    original_value     TEXT,
    suggestion         TEXT,
    related_record_ids TEXT
);
CREATE INDEX idx_issues_job_sev ON issues (job_id, severity);

CREATE TABLE output_files (
    job_id        TEXT NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN
                      ('report_xlsx', 'clean_csv', 'changes_csv', 'review_csv')),
    stored_path   TEXT NOT NULL,
    download_name TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (job_id, kind)
);
