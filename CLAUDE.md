# CLAUDE.md — Algorythm5

Source of truth for scope, architecture, cleaning rules and working agreements. Extends the parent `CLAUDE.md`; this file wins on conflict. **Status:** specification only, no code yet.

## 1. Overview and Users
A local web app: a user drops in a messy customer export (CSV/XLSX from an old system) and gets back **(1)** a clean, newsletter-ready file, **(2)** a change log (row, field, old → new, rule code), **(3)** a review list (uncertain items, plain-language reason, suggested fix).
- **Primary user:** non-technical colleague who knows Excel — needs drag-and-drop, plain language, one-click downloads, no jargon. **Secondary:** developer maintaining the rules.
- **Principles:** never change silently (log everything); never guess (flag + suggest); never lose data (originals kept, extras go to `*_other`); deterministic (same input + settings + `RULESET_VERSION` = identical output); local and private.
- **Out of scope (v1):** sending newsletters, `.xls`/`.ods`, online email/DNS checks, user accounts, resolving review items in the UI.
- **Open decisions (configurable defaults; confirm before implementing):** D1 `DEFAULT_PHONE_REGION=AL` · D2 `UNKNOWN_CONSENT_POLICY=include` · D3 `REQUIRED_CHANNEL=email` · D4 UI in English (strings centralized) · D5 `DATE_ORDER=DMY` · D6 single local machine only.

## 2. Tech Stack
| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12+ (dev machine: 3.13) | Strong Unicode/text handling, mature data libraries |
| Web | FastAPI + Uvicorn + python-multipart | Typed, small, Pydantic validation, serves static UI |
| Config | pydantic-settings | Typed settings from env/`.env`, validated at startup |
| Database | SQLite via stdlib `sqlite3` | Zero setup, one file; parameterized SQL in a repository layer, no ORM |
| XLSX | openpyxl + defusedxml | Keeps real cell types (exact phone digits), writes text cells; blocks XML attacks |
| CSV | stdlib `csv` (**no pandas**) | Reads everything as strings; pandas turns phones into floats and drops leading zeros |
| Encoding | charset-normalizer + ftfy | Detects cp1252/cp1250/ISO-8859-16; repairs mojibake (`Ã«` → `ë`) |
| Phones | phonenumbers | libphonenumber port: validation, mobile vs fixed, E.164 |
| Emails | email-validator (`check_deliverability=False`) | RFC syntax + IDN, no network calls |
| Fuzzy matching | rapidfuzz | Fast, MIT-licensed similarity for duplicates and domain typos |
| Frontend | Vanilla HTML/CSS/JS (ES modules) | No build step, framework or CDN |
| Quality | pytest, pytest-cov, httpx, ruff, mypy | Standard testing; lint+format in one tool; type safety |

Pin versions in `pyproject.toml`. No dependency outside this table without approval.

## 3. Folder Structure
```
src/algorythm5/
  config.py                 # Settings (single source of config)
  cleaning/                 # PURE library: no imports from web/db/services/storage, no I/O
    models.py rules.py pipeline.py ingestion.py mapping.py text.py placeholders.py
    names.py phones.py emails.py dates.py optouts.py duplicates.py status.py export.py
    data/                   # reference lists: synonyms, placeholders, domains, typos, titles, particles, mac_surnames, optout_terms
  db/        connection.py, migrations/0001_initial.sql, repositories.py
  storage/   files.py       # files under DATA_DIR
  services/  jobs.py, retention.py   # orchestration
  web/       app.py, errors.py, schemas.py, routes/{health,jobs,rules}.py
static/      index.html, css/{tokens,app}.css, js/{main,api,strings,theme}.js, js/views/
tests/       unit/cleaning/, integration/, golden/, fixtures/ (synthetic only)
data/        runtime DB + uploads/outputs (git-ignored)
```
Dependencies point one way only: `web → services → (cleaning, db, storage)`.

## 4. Commands (PowerShell)
| Task | Command |
|---|---|
| Setup | `py -3 -m venv .venv; .venv\Scripts\Activate.ps1; python -m pip install -e ".[dev]"; Copy-Item .env.example .env` |
| Run | `uvicorn algorythm5.web.app:app --reload --host 127.0.0.1 --port 8000` → http://127.0.0.1:8000 |
| Test / coverage | `pytest` · `pytest --cov=algorythm5 --cov-report=term-missing` |
| Lint / format / types | `ruff check .` · `ruff format .` · `mypy src` |
| Quality gate | `ruff check . ; ruff format --check . ; mypy src ; pytest` |

Migrations and the retention purge run at startup; the purge then repeats hourly.

## 5. Cleaning Logic
**Contract:** each stage is pure: `stage(records, settings) -> (records, changes, issues)`. `Change` = `(record_id, source_row, field, old, new, rule_code)`; `Issue` = `(record_id, field, rule_code, severity, message, suggestion, related_ids)`. **Severity:** `info` = logged only; `warning` = row stays in clean file and is listed for review; `blocking` = row excluded and listed for review. "Today" is injected via settings. Records get stable IDs `R00001…`.

**Stages, in order:** 1 ingestion → 2 header detection + column mapping (user confirms in UI) → 3 text normalization → 4 placeholders → 5 names/emails/phones/dates → 6 opt-outs → 7 row validation → 8 duplicates → 9 statuses → 10 export.

**Ingestion.** Limits: 10 MB, 50k rows, 100 columns, 1000 chars/cell (truncated, `ING-008`). Type is decided by content: XLSX needs ZIP magic bytes and ≤ 100 MB uncompressed; CSV must have no NUL bytes; anything else is refused (`ING-901`). CSV: detect encoding (`ING-001`), sniff delimiter (`, ; \t |`), read every cell as a string, pad ragged rows (overflow kept in `_overflow`, `ING-006`). XLSX: first visible sheet with data (`ING-005` if several), formula results only; integral floats < 2^53 become exact digits (recovers phones Excel only *displays* in scientific notation); dates become ISO. Header row = first of the first 20 rows with ≥ 50% text cells and a synonym match (`ING-003` if not line 1). Empty rows dropped (`ING-004`).

**Text normalization** (every cell): repair mojibake (`TXT-001`); NFC; remove zero-width/control chars, NBSP → space (`TXT-002`); trim and collapse spaces (`TXT-003`); normalize apostrophes in names (`TXT-004`).

**Column mapping.** Canonical fields: `customer_id, salutation, first_name, last_name, full_name, email, phone, mobile, phone_extension, company, street, postal_code, city, country, birth_date, signup_date, opt_out, consent, notes`. Confidence: exact synonym 1.0; fuzzy ≥ 90 → 0.8; content sniffing (≥ 80% of a 200-value sample are emails/phones/dates) → 0.6. Synonyms are English + Albanian: `emri`→first_name, `mbiemri`→last_name, `emri i plotë`→full_name, `tel/telefon/celular`→phone, `posta elektronike`→email, `qyteti`→city, `adresa`→street, `datëlindja`→birth_date, `pëlqim`→consent. The user always confirms the mapping. Unmapped columns are kept as passthrough (`MAP-002`). No email or phone column stops the run (`MAP-901`).

**Placeholders** are cleared and logged (`PLC-001`): `n/a, none, null, -, --, ?, x, xxx, 0, unknown, test, tbd, asdf, nuk ka, s'ka, pa`; one repeated char (`000000`); sequence or same-digit phones; fake emails (`test@test.com`, `a@a.com`, `noemail@*`, `example.*`). `no/jo/0` are real values in opt-out/consent fields.

**Names** (casing also reused for city/street/company):
- Full-name split (`NAM-004`): `LAST, FIRST`, otherwise first token = first name, rest = last name. More than 3 tokens or a single token → warning `NAM-008`.
- Titles → `salutation` (`NAM-002`): `Mr, Mrs, Ms, Dr, Prof`, Albanian `Z., Znj., Zsh., Ing., Av.`. Suffixes `Jr. Sr. II III IV` normalized (`NAM-003`).
- Casing (`NAM-001`) applies **only** to all-caps or all-lowercase values; mixed case is left alone. Capitalize each word and hyphen segment (`ANNA-MARIA`→`Anna-Maria`). Albanian letters use Unicode casing (`ËRMIRA`→`Ërmira`, `ÇELA`→`Çela`); letter pairs (Dh Gj Ll Nj Rr Sh Th Xh Zh) keep only the first letter upper (`XHEMAL`→`Xhemal`). `MCDONALD`→`McDonald`; `Mac` only for names in `mac_surnames.txt` (`Macedo`, `Mack` unchanged). `O'BRIEN`→`O'Brien`, `D'ANGELO`→`D'Angelo`. Particles (van, von, de, di, da, del, la, le, bin) lowercase unless first token.
- Warnings: digits/symbols (`NAM-005`), identical first and last name (`NAM-006`), lost characters `?`/`�` (`NAM-007`, never guess the letter); initial only → info `NAM-009`. Never add or remove diacritics.

**Phones** (region from `DEFAULT_PHONE_REGION`, overridden by a `country` column):
1. Split multiples on `/ ; , |`, newline, ` or `, ` ose ` (`PHN-006`); mobile columns first. Extensions (`ext`, `x`, `#`, `int`, `brendshme` + digits) move to `phone_extension` (`PHN-005`).
2. Excel damage: trailing `.0` removed (`PHN-003`). Scientific notation in text (`3.55692E+11`) has lost digits and is **unrecoverable** → warning `PHN-002`, value not used.
3. Strip formatting; leading `00` → `+` (`PHN-008`). Parse with phonenumbers; if invalid, try adding `+` (`355692345678`), then a leading `0` (`692345678`), and keep the first that `is_valid` (`PHN-004`).
4. Valid → E.164 `+355692345678` (`PHN-001`); invalid → warning `PHN-007`, original to `phone_other`. Primary = first valid mobile, else first valid number; others to `phone_other` (`; `-joined, de-duplicated). Always written as text cells.

**Emails:**
1. Unwrap `mailto:`, `Name <a@b>`, quotes, trailing punctuation (`EML-002`). Split multiples (`EML-006`): first valid → `email`, rest → `email_other`.
2. Trim, remove spaces, lowercase the whole address (`EML-001`) — deliberate, so de-duplication works.
3. Auto-fix (`EML-003`), only if the result is valid: `,`→`.` in domain, `..`, `@@`, `(at)`, `(dot)`; known typos by exact lookup: `gmial/gmai/gamil/gmail.con`→`gmail.com`, `hotmial`→`hotmail.com`, `yaho`→`yahoo.com`, `outlok`→`outlook.com`, `.con/.cmo/.comm`→`.com`.
4. Unknown domain within edit distance 2 of a known domain (incl. `.al`, `.com.al`) → suggestion only, value unchanged (`EML-004`, warning).
5. Validate with email-validator. Invalid → `EML-005`: blocking if it was the only channel, else warning; value goes to `email_other` as `INVALID: …`. Role addresses (`info@`, `office@`, `noreply@`) → info `EML-007`.

**Dates:** accept ISO, `DD.MM.YYYY`, `DD/MM/YYYY`, `MM/DD/YYYY`, 2-digit years (pivot on current year; birth dates never in the future), English/Albanian month names (`janar…dhjetor`), Excel serials (base `1899-12-30`, `DAT-002`). Day/month ambiguity: if any value in the column proves the order, use it for the whole column, else `DATE_ORDER` (`DAT-003`); contradicting evidence → warning. Output ISO `YYYY-MM-DD` (`DAT-001`). Unparsable → `DAT-004`; future, before 1900, Excel sentinels, or birth-date age < 13 or > 120 → `DAT-005`.

**Opt-outs** → `consent_status` ∈ `subscribed | opted_out | unknown`. Opt-out terms: `yes/1/x/po/unsubscribed/opt-out/dnc/çabonuar/mos dërgo`; not opted out: `no/0/jo/empty`; a `consent` column means the inverse; unrecognized values → `OPT-004`. When opt-out and consent disagree, opt-out wins (`OPT-005`). An opt-out phrase in notes (`unsubscribe`, `do not contact`, `mos më dërgoni`) suppresses the row as a precaution and lists it for review (`OPT-002`). Confirmed opt-out → `OPT-001`. Unknown consent → `OPT-003`, severity set by `UNKNOWN_CONSENT_POLICY` (include = info, review = warning, exclude = blocking).

**Duplicates.** Names are compared accent-folded (`ë→e`, `ç→c`), for comparison only. Records are compared only when they share a blocking key: email, phone, or first 3 letters of last name + first initial.

| Tier | Condition | Action |
|---|---|---|
| T1 | Same email (incl. `email_other`) | Auto-merge, `DUP-001` |
| T2 | Same E.164 phone + name `token_sort_ratio ≥ 90` | Auto-merge, `DUP-002` |
| T3 | Name ratio ≥ 92 + same city, birth date or postal code | Review only, `DUP-003` |
| — | Same phone, name ratio < 90 | No merge (families share phones), `DUP-005` |

Matches chain transitively (union-find). **Safety brake:** a group is **not** merged (`DUP-004`) if it has two different valid emails, any name pair below 80, or more than 5 members. **Merge:** survivor = member with the most filled fields (tie → lowest source row); empty fields filled from others in row order; conflicting emails/phones → `*_other`; earliest `signup_date` kept; other conflicts logged (`DUP-006`); any opt-out in the group suppresses the merged record. Survivor gets `source_rows`; the others get `MERGED` + `merged_into`.

**Row validation and statuses.** No valid email or phone → `ROW-001`; phone only when `REQUIRED_CHANNEL=email` → `ROW-002` (both blocking). Each row gets the first status that matches:

| Status | Condition | In clean file? |
|---|---|---|
| `MERGED` | Absorbed into a survivor | No |
| `SUPPRESSED` | Opted out | No (Suppressed sheet) |
| `REJECTED` | `ROW-001`/`ROW-002` | No (Rejected sheet) |
| `BLOCKED` | Other blocking issue | No |
| `REVIEW` | At least one warning | Yes (also listed for review) |
| `FIXED` | Changed, no warnings | Yes |
| `OK` | Untouched | Yes |

**Output files** (`<stem>_A5_<YYYYMMDD-HHMM>`): `_report.xlsx` with sheets Summary (counts, top rules, ruleset version, settings), Clean, Review, Changes, Suppressed, Rejected; plus `_clean.csv`, `_changes.csv`, `_review.csv` in UTF-8 **with BOM** (so Excel shows `ë/ç`).
- Clean columns: `record_id, salutation, first_name, last_name, email, email_other, phone, phone_extension, phone_other, company, street, postal_code, city, country, birth_date, signup_date, consent_status, review_flag, source_rows, <passthrough>`.
- Review columns: `record_id, source_rows, status, field, rule_code, problem, original_value, current_value, suggestion, related_record_ids`.
- Frozen bold header and auto-filter; phone, postal and ID columns written as text. Formula-injection guard: cells starting with `= + - @`, TAB or CR get a `'` prefix, except validated E.164 phones.

**Rule codes** are defined once in `cleaning/rules.py` (code, severity, plain-language explanation) and never reused for another meaning. Prefixes: `ING TXT MAP PLC NAM PHN EML DAT OPT DUP ROW`. `x9xx` codes stop the whole job (`ING-901` bad file, `ING-902` over limits, `MAP-901` no contact column). Any behavior change bumps `RULESET_VERSION`.

## 6. Database Model (SQLite)
PRAGMAs `foreign_keys=ON`, `journal_mode=WAL`. Numbered SQL migrations tracked in `schema_migrations`, never edited after commit. UUID4 job IDs, UTC ISO timestamps, `ON DELETE CASCADE` from `jobs`.
- **jobs:** `id, original_filename (sanitized), file_kind (csv|xlsx), stored_upload_path, detected_encoding, detected_delimiter, sheet_name, header_row_index, status (uploaded|mapped|processing|completed|failed), error_code, settings_json, ruleset_version, rows_in, rows_out, created_at, started_at, completed_at, expires_at`.
- **column_mappings:** `job_id, source_index, source_header, target_field (NULL = passthrough), method (auto|sniffed|manual), confidence`; PK `(job_id, source_index)`.
- **records:** `job_id, record_id, source_row, raw_json, clean_json, status, consent_status, merged_into, source_rows`; PK `(job_id, record_id)`, index `(job_id, status)`.
- **changes:** `id, job_id, record_id, source_row, field, old_value, new_value, rule_code, seq`; index `(job_id, rule_code)`.
- **issues:** `id, job_id, record_id (NULL = job-level), field, rule_code, severity, message, original_value, suggestion, related_record_ids`.
- **output_files:** `job_id, kind (report_xlsx|clean_csv|changes_csv|review_csv), stored_path, download_name, size_bytes, sha256, created_at`.
- **Retention:** jobs and files purged after `RETENTION_DAYS=7`, or immediately on delete.

## 7. API Endpoints
Error envelope `{"error": {"code", "message"}}`. Status codes: 400, 404, 409 (wrong job state), 413, 415, 422, 500 (generic message only). `job_id` validated as UUID4.
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness + `ruleset_version` |
| GET | `/api/rules` | Rule legend `[{code, severity, explanation}]` |
| POST | `/api/jobs` | Multipart upload → `201 {job_id, encoding, sheet_name, columns+proposed mapping, preview (10 rows)}` |
| GET | `/api/jobs/{id}` | Status + counts per status |
| PUT | `/api/jobs/{id}/mapping` | Confirm/edit mapping `{columns:[{index, target_field\|null}]}` → status `mapped` |
| POST | `/api/jobs/{id}/run` | Start cleaning as background task → `202`; `409` if not mapped or already run |
| GET | `/api/jobs/{id}/summary` | Counts, top rules, ruleset version |
| GET | `/api/jobs/{id}/changes` | Paginated change log `?page&page_size(≤200)&rule_code&field` |
| GET | `/api/jobs/{id}/review` | Paginated review list `?page&page_size&severity&rule_code` |
| GET | `/api/jobs/{id}/files/{kind}` | Download `report_xlsx\|clean_csv\|changes_csv\|review_csv` |
| DELETE | `/api/jobs/{id}` | Delete job and files now → `204` |
| GET | `/` | Frontend |

## 8. UI Requirements
- **Flow (one page, 4 steps):** 1) **Upload** — drag-and-drop zone that is also a button; type and size limits in plain words. 2) **Check columns** — each source column shows header, 3 sample values, target dropdown and a confidence chip (Sure/Likely/Guess); 10-row preview. 3) **Cleaning** — progress state polling every second, `aria-live` status. 4) **Results** — summary cards (received, ready, fixed, needs review, merged, opted out, couldn't use); primary "Download clean file (.xlsx)" plus secondary downloads; filterable Review and Changes tabs; "Delete job" (with confirmation) and "Clean another file".
- **Modern, pretty:** design tokens as CSS variables in `tokens.css` (no hardcoded colors); 8-px spacing scale; 12-px rounded cards with soft shadows; one accent color plus success/warning/danger; system font stack; transitions ≤ 200 ms that respect `prefers-reduced-motion`.
- **Dark mode:** follows `prefers-color-scheme`; light/dark/system toggle stored in `localStorage` (try/catch) and applied before first paint.
- **Responsive:** mobile-first from 360 px; no horizontal page scroll (tables scroll in their container); summary cards in 1/2/4 columns.
- **Accessible (WCAG 2.1 AA):** semantic HTML, full keyboard use, visible focus, never color alone, contrast checked in both themes.
- **Plain language:** explanations first, rule codes as small badges; all text in `strings.js`.
- **Safety:** render data only via `textContent`, never `innerHTML`; all `fetch` calls in `api.js`; no CDN, fonts or analytics.

## 9. Coding Standards and Testing
- Follow the parent `CLAUDE.md` (SOLID, DRY, KISS, YAGNI). Python: `ruff format` (line length 100); ruff rules `E,F,W,I,B,UP,N,SIM,S,RUF`; type hints everywhere; `mypy --strict` on `cleaning/`; frozen slotted dataclasses; named constants for thresholds; no mutable module state.
- Errors: services raise `AppError(code, message, status)`, formatted by one handler. Cleaning reports data problems as `Issue`s and never raises for them. Logs contain job IDs, counts, durations and rule codes only — **never** personal data.
- JS/CSS: ES modules, `async/await`, no globals, BEM-like class names, mobile-first CSS.
- **Unit tests:** table-driven `parametrize` per cleaning module; every example in §5 is a test case. Every stage covers empty, whitespace-only, placeholder, very long, mojibake, non-Latin, and already-correct values (which must produce **no** change).
- **Idempotence:** running the pipeline on its own clean output produces zero changes.
- **Golden tests:** synthetic CSV/XLSX inputs (encodings, delimiters, header offsets, duplicates, opt-outs) compared exactly to expected outputs.
- **Integration tests:** full API flow, every error code, limits, spoofed file types, path traversal, migrations, retention.
- **Coverage:** ≥ 90% for `cleaning/`, ≥ 80% overall. Fixtures are **synthetic only**. Bug fixes start with a failing test. Done means the quality gate passes with no new warnings.

## 10. Git Workflow
- Repo: `origin` = https://github.com/SuevaSokoli/Algorythm5 (`main` tracks `origin/main`). Short-lived branches off `main`: `feat/…`, `fix/…`, `refactor/…`, `test/…`, `docs/…`, `chore/…`.
- Conventional Commits: `type(scope): imperative summary` (≤ 72 chars), body explains *why*. Types: `feat fix refactor test docs chore build ci perf style`. Scopes: `ingestion mapping names phones emails dates optouts dedupe export rules db api ui config deps`.
- One logical change per commit, with its tests. Commit **only when asked**, after showing what is staged.
- **Push only when the user asks** (plain `git push` to `origin`). Never force-push, rebase, amend pushed commits, `reset --hard` or use `--no-verify`. **Never rewrite history.**
- Never commit `.env`, `data/`, `*.db`, uploads/outputs, real customer files or `.venv/`.

## 11. Security and Privacy
- Binds to `127.0.0.1`. **No outbound network calls** (no DNS/MX checks, CDNs, fonts or telemetry). Network exposure needs auth + HTTPS (D6).
- Uploads: size/row/column/cell limits, content-based type detection, zip-bomb guard, defusedxml, formulas never evaluated.
- Files stored as `<uuid>.<ext>` under `DATA_DIR`; paths built only from validated UUIDs; user filenames sanitized and used for display only.
- Parameterized SQL only; formula-injection guard on every export; retention purge plus delete on demand.
- Headers: strict CSP (`default-src 'self'; frame-ancestors 'none'`), `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` on API responses and downloads.
- Clients get generic error messages; stack traces stay in the server log, which never contains personal data.
- No secrets in v1; config in a git-ignored `.env`, safe defaults in `.env.example`; never hardcode credentials. Never put real customer data in code, tests, commits or prompts.

## 12. Rules for How Claude Works
1. Read this file first. Point out any conflict and ask before proceeding.
2. Ask instead of assuming. Never invent requirements, fields or dependencies; open decisions (§1) need confirmation.
3. Stay in scope; offer improvements as suggestions only.
4. Work in small steps. For cleaning rules, write the tests first, then the code.
5. A rule change updates §5, `rules.py`, tests and golden files, and bumps `RULESET_VERSION`.
6. Keep layers separate: logic in `cleaning/`, SQL in `repositories.py`, HTTP in `web/`, orchestration in `services/`.
7. Run the quality gate before reporting; report exactly what passed, failed or was skipped.
8. Self-review every change for correctness, privacy, security and consistency with this file.
9. Update this file in the same change whenever a decision is made.
10. Follow §10 for Git; use PowerShell commands and `pathlib` for paths.
11. When asked to wait, give a short summary and stop.
