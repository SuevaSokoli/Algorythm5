# Algorythm5

Turns a messy customer export (CSV or Excel) into a **clean, newsletter-ready file**, plus a **change log** and a **review list**. It runs locally on your computer, and no data leaves it.

## What it does

- Fixes names: CAPS → Title case, Albanian letters (ë, ç), Mc/Mac/O', titles such as Dr. or Znj.
- Formats phones as international numbers (`+355692345678`), repairs Excel damage, and splits several numbers in one cell.
- Fixes email typos (`gmial.com` → `gmail.com`), suggests fixes for doubtful domains, and validates addresses.
- Converts dates to `YYYY-MM-DD`, including Excel serial numbers and Albanian month names.
- Removes people who opted out. An opt-out always wins.
- Merges duplicates safely. Uncertain matches go to review instead of being merged.

Every change is logged with a rule code. The full rules are in [CLAUDE.md](CLAUDE.md) §5.

## Install (Windows PowerShell)

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## Run

```powershell
uvicorn algorythm5.web.app:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>, drop in a file, check the columns, and click **Clean my file**.

To try it, use the sample file: `tests/fixtures/sample_customers_messy.xlsx` (or `.csv`). All of its data is invented.

## What you get back

| File | Contents |
|---|---|
| `…_report.xlsx` | Sheets: Summary, Clean, Review, Changes, Suppressed, Rejected |
| `…_clean.csv` | The newsletter-ready rows (UTF-8, opens correctly in Excel) |
| `…_changes.csv` | Every automatic change: row, field, before, after, rule |
| `…_review.csv` | Rows and values that need a human decision |

## Settings

Settings go in `.env` (see `.env.example`):

- **Phone country:** `DEFAULT_PHONE_REGION`, default `AL`.
- **Unknown consent:** `UNKNOWN_CONSENT_POLICY`, `include` / `review` / `exclude`.
- **Required channel:** `REQUIRED_CHANNEL`, `email` / `any`.
- **Date order:** `DATE_ORDER`, `DMY` / `MDY`.
- **Limits:** file size, rows and columns.
- **Retention:** `RETENTION_DAYS`, default 7. Jobs and files are deleted automatically after this many days, or immediately with "Delete this job now".

## Development

```powershell
ruff check . ; ruff format --check . ; mypy src ; pytest
pytest --cov=algorythm5 --cov-report=term-missing
python tests/fixtures/build_samples.py   # regenerate the sample files
```

The project layout, architecture rules and working agreements are in [CLAUDE.md](CLAUDE.md).
