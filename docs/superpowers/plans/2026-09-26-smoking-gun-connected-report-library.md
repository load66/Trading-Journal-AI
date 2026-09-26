# Smoking Gun Connected Report Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ChatGPT read trades already stored by the Trading Journal, generate a versioned Smoking Gun audit without a second broker-file upload, save the structured report back to the journal, and present it in a fast professional Report Library with on-demand HTML and CSV downloads.

**Architecture:** Preserve `backend/performance_report.py` as the deterministic analytics source and add a separate report-library persistence/service layer. Saved reports live in the private `journal` schema (with SQLite parity for local mode), store structured metrics/diagnosis/action-plan data rather than multi-megabyte HTML, and use a deterministic fingerprint to detect duplicates and stale reports. The React Reports → Smoking Gun area becomes a two-mode professional terminal: Live Analytics and Report Library.

**Tech Stack:** Python 3 / FastAPI, SQLite, PostgreSQL/Supabase, psycopg 3, React 19, Axios, Recharts, React Testing Library/Jest, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-smoking-gun-connected-report-library-design.md`

## Global Constraints

- The canonical trade source is the Trading Journal database; do not require a second CSV upload to ChatGPT.
- The private `journal` schema remains private; do not expose it merely to support this feature.
- Never expose a Supabase service-role/secret key in frontend code.
- No personal trading data, account identifiers, generated report payloads, or report artifacts are committed to GitHub.
- Deterministic calculations own P&L, timestamps, sizing, behavior cohorts, stop models, projections, and fingerprints; AI may interpret but must not recalculate source metrics.
- Preserve the exact hold-time buckets already defined in `backend/performance_report.py`.
- Preserve the exact option-size buckets already defined in `backend/performance_report.py`.
- Behavior counterfactuals overlap and must never be summed.
- Thin samples must be visibly marked; unsupported behaviors must not be presented as leaks.
- Scenario projections must be labeled as scenarios, not forecasts or guarantees.
- Saved reports must be versioned and immutable unless explicitly regenerated as a new version.
- Report Library list requests return summary metadata only; heavy structured payloads load only when a report is opened.
- HTML and CSV exports are generated on demand from saved structured data; do not store a multi-megabyte Plotly bundle in Postgres.
- Maintain both SQLite local-mode compatibility and Postgres/Supabase compatibility.
- Use the journal's existing dark design language; no unrelated global visual redesign.
- Do not refactor unrelated reporting code or change current deterministic formulas unless a task below explicitly requires it.

## Review Focus

- **Changed source trades after a report was saved:** library/list/detail must mark the report stale by recomputing the canonical fingerprint for the report's account/date range; it must not silently present it as current.
- **Same data generated twice:** the persistence layer must return/conflict with the existing report for the same account + date range + fingerprint + report version instead of silently creating a duplicate.
- **Missing/partial timestamp coverage:** a saved report must preserve the deterministic coverage metadata and the UI must show reduced coverage rather than hiding timestamp-dependent sections or fabricating values.
- **Large structured reports:** list endpoints must not load JSON blobs; detail/export may load one report payload at a time and the UI must not eagerly render every historical report.
- **Cross-database parity:** schema, JSON serialization, uniqueness, and CRUD behavior must pass in SQLite tests and use equivalent JSONB/index/constraint behavior in Postgres.

---

## File Structure

### Backend

- Create: `backend/smoking_gun_library.py`
  - Canonical source-row normalization and SHA-256 fingerprinting.
  - Saved-report repository/service functions.
  - JSON encode/decode compatibility between SQLite TEXT and Postgres JSONB.
  - Duplicate and stale detection.
- Create: `backend/smoking_gun_exports.py`
  - Standalone HTML renderer from a saved structured report.
  - Trade-ledger CSV renderer from the saved compact ledger.
- Create: `backend/tests/test_smoking_gun_library.py`
  - Persistence, fingerprint, duplicate/stale, serialization, and API contract tests.
- Create: `backend/tests/test_smoking_gun_exports.py`
  - HTML/CSV export correctness and escaping tests.
- Modify: `backend/database.py`
  - Add SQLite `smoking_gun_reports` schema and latest Postgres application schema version check.
- Modify: `backend/performance_report.py`
  - Add a compact deterministic `trade_ledger` payload and explicit analytics/behavior schema version metadata without changing existing calculations.
- Modify: `backend/tests/test_performance_report.py`
  - Pin compact ledger fields, reconciliation, and version metadata.
- Modify: `backend/main.py`
  - Include a focused Smoking Gun library router; keep existing live endpoints intact.

### Supabase

- Create via `supabase migration new create_smoking_gun_report_library`: `supabase/migrations/<generated_timestamp>_create_smoking_gun_report_library.sql`
  - Add `journal.smoking_gun_reports`, indexes, RLS, private grants, and new schema version row.

### Frontend

- Create: `frontend/src/components/SmokingGunLibrary.js`
  - Report-library metadata list and report-card actions.
- Create: `frontend/src/components/SmokingGunSavedReport.js`
  - Professional saved-report detail view rendered from stored structured payload.
- Create: `frontend/src/components/SmokingGunTerminal.css`
  - Scoped terminal-grade responsive layout/styles.
- Modify: `frontend/src/components/SmokingGunReport.js`
  - Orchestrate Live Analytics vs Report Library without rewriting deterministic live sections.
- Modify: `frontend/src/api.js`
  - Add saved-report list/detail/delete/export clients.
- Modify: `frontend/src/app.integration.test.js`
  - Pin subnavigation, report library, detail, stale state, and download actions.

### Documentation

- Create: `docs/smoking-gun-report-contract.md`
  - Stable connected-workflow contract for future ChatGPT sessions: source query, canonical fingerprint, report schema, version fields, and direct Supabase write-back expectations.

---

### Task 1: Add Versioned Saved-Report Persistence

**Files:**
- Modify: `backend/database.py`
- Create: `backend/tests/test_smoking_gun_library.py`
- Create via Supabase CLI: `supabase/migrations/<generated_timestamp>_create_smoking_gun_report_library.sql`

**Interfaces:**
- Consumes: existing `get_db()`, `init_db()`, SQLite/Postgres adapter behavior, and `journal.accounts(id)`.
- Produces: table `smoking_gun_reports` in SQLite and `journal.smoking_gun_reports` in Postgres with the same logical columns.

- [ ] **Step 1: Write the failing SQLite schema test**

Add `test_sqlite_init_creates_smoking_gun_reports_table()` to `backend/tests/test_smoking_gun_library.py`.

Assert the initialized table contains these columns:

```python
expected = {
    "id", "account_id", "title", "date_from", "date_to", "generated_at",
    "report_version", "analytics_engine_version", "behavior_version",
    "analysis_provider", "analysis_model", "trade_count", "gross_pnl",
    "net_pnl", "primary_edge", "primary_leak", "data_fingerprint",
    "source_metrics_json", "diagnosis_json", "action_plan_json",
    "export_manifest_json", "status",
}
assert expected <= column_names
```

Also assert there is a uniqueness constraint equivalent to:

`(account_id, date_from, date_to, data_fingerprint, report_version)`.

- [ ] **Step 2: Run the schema test and verify it fails**

Run:

```bash
cd backend
pytest tests/test_smoking_gun_library.py::test_sqlite_init_creates_smoking_gun_reports_table -v
```

Expected: FAIL because `smoking_gun_reports` does not exist.

- [ ] **Step 3: Add the SQLite table to `SCHEMA_STATEMENTS`**

In `backend/database.py`, add a `CREATE TABLE IF NOT EXISTS smoking_gun_reports` statement with:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `account_id INTEGER NOT NULL REFERENCES accounts(id)`
- `title TEXT NOT NULL`
- `date_from TEXT NOT NULL`
- `date_to TEXT NOT NULL`
- `generated_at TEXT NOT NULL DEFAULT (datetime('now'))`
- `report_version TEXT NOT NULL`
- `analytics_engine_version TEXT NOT NULL`
- `behavior_version TEXT NOT NULL`
- `analysis_provider TEXT`
- `analysis_model TEXT`
- `trade_count INTEGER NOT NULL`
- `gross_pnl REAL`
- `net_pnl REAL`
- `primary_edge TEXT`
- `primary_leak TEXT`
- `data_fingerprint TEXT NOT NULL`
- `source_metrics_json TEXT NOT NULL`
- `diagnosis_json TEXT`
- `action_plan_json TEXT`
- `export_manifest_json TEXT NOT NULL DEFAULT '{}'`
- `status TEXT NOT NULL DEFAULT 'complete' CHECK(status IN ('complete','draft','failed'))`
- `UNIQUE(account_id, date_from, date_to, data_fingerprint, report_version)`

Add indexes:

- `idx_smoking_gun_reports_account_generated` on `(account_id, generated_at)`
- `idx_smoking_gun_reports_fingerprint` on `(data_fingerprint)`

- [ ] **Step 4: Create the Supabase migration using the CLI-generated name**

Run:

```bash
npx supabase migration new create_smoking_gun_report_library
```

Use the exact path printed by the CLI. Do not invent a timestamped filename.

Populate it with the Postgres equivalent:

- schema: `journal`
- JSON fields: `jsonb`
- timestamp: `timestamptz not null default now()`
- FK: `account_id references journal.accounts(id) on delete cascade`
- same unique key and indexes as SQLite
- `alter table journal.smoking_gun_reports enable row level security`
- revoke all table/sequence privileges from `anon` and `authenticated`
- do not add permissive browser RLS policies
- insert the new application schema migration id `20260926_002_smoking_gun_reports` into `journal.schema_migrations`

Update `APPLICATION_SCHEMA_VERSION` in `backend/database.py` to `20260926_002_smoking_gun_reports`.

- [ ] **Step 5: Run database-mode tests**

Run:

```bash
cd backend
pytest tests/test_smoking_gun_library.py tests/test_database_modes.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/database.py backend/tests/test_smoking_gun_library.py supabase/migrations
git commit -m "feat: add Smoking Gun report persistence"
```

---

### Task 2: Add Stable Report Versions, Compact Ledger, and Canonical Fingerprints

**Files:**
- Create: `backend/smoking_gun_library.py`
- Modify: `backend/performance_report.py`
- Modify: `backend/tests/test_performance_report.py`
- Modify: `backend/tests/test_smoking_gun_library.py`

**Interfaces:**
- Consumes: raw trade dictionaries from the existing trades query.
- Produces:
  - `ANALYTICS_ENGINE_VERSION: str`
  - `BEHAVIOR_VERSION: str`
  - `REPORT_SCHEMA_VERSION: str`
  - `build_source_fingerprint(trades: list[dict], account_id: int | None, date_from: str | None, date_to: str | None) -> str`
  - `build_compact_trade_ledger(enriched_trades: list[dict]) -> list[dict]`
  - new `meta.analytics_engine_version`, `meta.behavior_version`, `meta.report_schema_version`
  - new top-level `trade_ledger`

- [ ] **Step 1: Write failing fingerprint tests**

Add:

```python
def test_source_fingerprint_is_order_independent_and_content_sensitive():
    a = [trade("a", ...), trade("b", ...)]
    b = list(reversed(a))
    assert build_source_fingerprint(a, 1, "2026-09-01", "2026-09-30") == \
           build_source_fingerprint(b, 1, "2026-09-01", "2026-09-30")

    changed = [dict(a[0]), dict(a[1])]
    changed[0]["net_pnl"] += 1
    assert build_source_fingerprint(changed, 1, "2026-09-01", "2026-09-30") != \
           build_source_fingerprint(a, 1, "2026-09-01", "2026-09-30")
```

Also add `test_source_fingerprint_canonicalizes_execution_json_key_order()` so semantically identical execution objects with different JSON key ordering hash identically.

- [ ] **Step 2: Run fingerprint tests and verify failure**

Run:

```bash
cd backend
pytest tests/test_smoking_gun_library.py -k fingerprint -v
```

Expected: FAIL because the helper does not exist.

- [ ] **Step 3: Implement `build_source_fingerprint`**

In `backend/smoking_gun_library.py`:

```python
REPORT_SCHEMA_VERSION = "1"
ANALYTICS_ENGINE_VERSION = "2026.09.26.1"
BEHAVIOR_VERSION = "2026.09.26.1"

def build_source_fingerprint(
    trades: list[dict],
    account_id: int | None,
    date_from: str | None,
    date_to: str | None,
) -> str:
    ...
```

Canonical fields per trade, in stable order:

- `id`
- `account_id`
- `trade_group`
- `date`
- `ticker`
- `instrument_type`
- `side`
- `gross_pnl`
- `net_pnl`
- `commissions`
- parsed/canonicalized `executions`
- `option_expiry`
- `option_strike`
- `option_type`
- `source`

Sort canonical trades by `(account_id, date, trade_group, id)`, serialize with sorted JSON keys and compact separators, include the filter tuple, then return lowercase SHA-256 hex.

- [ ] **Step 4: Write failing compact-ledger reconciliation test**

Add to `backend/tests/test_performance_report.py`:

```python
def test_trade_ledger_reconciles_to_report_meta_and_pnl():
    report = build_performance_report(rows)
    ledger = report["trade_ledger"]
    assert len(ledger) == report["meta"]["trade_count"]
    assert round(sum(r["net_pnl"] for r in ledger), 2) == round(
        sum(r["total_pnl"] for r in report["daily_pnl"]), 2
    )
    assert {"trade_group", "date", "ticker", "instrument_type", "side",
            "entry_time", "exit_time", "hold_sec", "entry_size",
            "gross_pnl", "commissions", "net_pnl"} <= set(ledger[0])
```

- [ ] **Step 5: Add the compact ledger and version metadata**

In `backend/performance_report.py`, emit one compact row per completed trade. Do not copy raw execution JSON into `trade_ledger`.

Use:

- `entry_time`: ISO-like timestamp string from enriched `entry_dt`
- `exit_time`: ISO-like timestamp string from enriched `exit_dt`
- `hold_sec`: numeric or null
- `entry_size`: option contracts for options; entry notional for stocks/futures
- `gross_pnl`, `commissions`, `net_pnl`
- `hold_bucket`, `size_bucket`
- deterministic behavior flags already derivable during report construction when available

Add version strings to `meta`.

- [ ] **Step 6: Run deterministic analytics tests**

Run:

```bash
cd backend
pytest tests/test_performance_report.py tests/test_smoking_gun_library.py -v
```

Expected: PASS with all existing hold/size/behavior tests unchanged.

- [ ] **Step 7: Commit**

```bash
git add backend/performance_report.py backend/smoking_gun_library.py backend/tests/test_performance_report.py backend/tests/test_smoking_gun_library.py
git commit -m "feat: version and fingerprint Smoking Gun analytics"
```

---

### Task 3: Build Saved-Report Repository, Duplicate Prevention, and Stale Detection

**Files:**
- Modify: `backend/smoking_gun_library.py`
- Modify: `backend/tests/test_smoking_gun_library.py`

**Interfaces:**
- Consumes:
  - `build_source_fingerprint(...)`
  - DB connection adapter
  - structured report payloads
- Produces:
  - `create_saved_report(conn, payload: dict) -> dict`
  - `list_saved_reports(conn, account_id: int | None = None) -> list[dict]`
  - `get_saved_report(conn, report_id: int) -> dict | None`
  - `delete_saved_report(conn, report_id: int) -> bool`
  - `current_fingerprint_for_range(conn, account_id: int, date_from: str, date_to: str) -> str`
  - `decorate_stale_status(conn, reports: list[dict]) -> list[dict]`

- [ ] **Step 1: Write the failing round-trip persistence test**

Create a fixture report payload containing:

- account/date range
- version fields
- fingerprint
- `source_metrics` dict
- `diagnosis` dict
- `action_plan` list
- summary metadata

Assert `create_saved_report` followed by `get_saved_report` returns decoded Python JSON objects, not JSON strings.

- [ ] **Step 2: Write the failing duplicate test**

```python
def test_duplicate_report_is_not_silently_created(conn, payload):
    first = create_saved_report(conn, payload)
    second = create_saved_report(conn, payload)
    assert second["id"] == first["id"]
    assert second["duplicate"] is True
    assert conn.execute("select count(*) from smoking_gun_reports").fetchone()[0] == 1
```

- [ ] **Step 3: Write stale-detection tests**

Cover both cases:

1. same underlying source rows → `is_stale is False`
2. mutate one source trade P&L or execution payload → `is_stale is True`

Also add the Review Focus case with two reports sharing the same account/date range and assert the range fingerprint is calculated once per distinct range inside one list operation.

- [ ] **Step 4: Implement repository helpers**

Rules:

- serialize JSON explicitly for SQLite
- accept already-decoded dict/list values from Postgres JSONB
- list query selects only summary columns, never `source_metrics_json`, `diagnosis_json`, or `action_plan_json`
- list output sorts newest first
- duplicate insert returns the existing row with `duplicate=True`
- detail output returns `duplicate=False`
- stale detection compares stored fingerprint with current canonical fingerprint for the exact account/date range
- if the underlying range no longer exists, `is_stale=True` and `stale_reason="source-data-missing"`

- [ ] **Step 5: Run repository tests**

Run:

```bash
cd backend
pytest tests/test_smoking_gun_library.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/smoking_gun_library.py backend/tests/test_smoking_gun_library.py
git commit -m "feat: add Smoking Gun report repository"
```

---

### Task 4: Add Focused Report-Library API and On-Demand Exports

**Files:**
- Create: `backend/smoking_gun_exports.py`
- Create: `backend/tests/test_smoking_gun_exports.py`
- Modify: `backend/smoking_gun_library.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/test_smoking_gun_library.py`

**Interfaces:**
- Consumes: repository helpers from Task 3.
- Produces HTTP routes:
  - `GET /api/smoking-gun-reports?account_id=<id>`
  - `GET /api/smoking-gun-reports/{report_id}`
  - `POST /api/smoking-gun-reports`
  - `DELETE /api/smoking-gun-reports/{report_id}`
  - `GET /api/smoking-gun-reports/{report_id}/report.html`
  - `GET /api/smoking-gun-reports/{report_id}/trade-ledger.csv`

- [ ] **Step 1: Write failing API tests**

Using FastAPI's test client pattern already used by the project, assert:

- list returns metadata only and does not contain `source_metrics`
- detail contains decoded `source_metrics`, `diagnosis`, and `action_plan`
- unknown id returns 404
- duplicate POST returns the existing report with `duplicate: true`
- no-data/invalid account payload returns 400 rather than inserting a bogus report
- delete returns 204 and subsequent detail returns 404

- [ ] **Step 2: Run API tests and verify failure**

Run:

```bash
cd backend
pytest tests/test_smoking_gun_library.py -k "api or duplicate or delete" -v
```

Expected: FAIL because routes are absent.

- [ ] **Step 3: Add request validation and routes**

Keep route code thin. Define a Pydantic request model with required:

- `account_id: int`
- `title: str`
- `date_from: str`
- `date_to: str`
- `report_version: str`
- `analytics_engine_version: str`
- `behavior_version: str`
- `analysis_provider: str | None`
- `analysis_model: str | None`
- `data_fingerprint: str`
- `source_metrics: dict`
- `diagnosis: dict | None`
- `action_plan: list | dict | None`
- `primary_edge: str | None`
- `primary_leak: str | None`

Before insert, recompute the current fingerprint from journal trades and reject with HTTP 409 if it does not equal the supplied fingerprint. This prevents a stale ChatGPT payload from being saved as current through the app API.

Existing `GET /api/smoking-gun-report` and `GET /api/smoking-gun-diagnosis` remain unchanged.

- [ ] **Step 4: Write failing export tests**

In `backend/tests/test_smoking_gun_exports.py` assert:

- HTML contains escaped report title, DATA / DIAGNOSIS / FIX sections, scoreboard values, and no external Plotly bundle
- HTML escapes ticker/title/diagnosis text rather than executing markup
- CSV header is exactly:
  `trade_group,date,ticker,instrument_type,side,entry_time,exit_time,hold_sec,entry_size,gross_pnl,commissions,net_pnl,hold_bucket,size_bucket`
- CSV rows reconcile to stored ledger trade count/net P&L

- [ ] **Step 5: Implement exports**

In `backend/smoking_gun_exports.py` expose:

```python
def render_saved_report_html(report: dict) -> str: ...
def render_trade_ledger_csv(report: dict) -> str: ...
```

HTML rules:

- fully standalone
- dark professional layout
- responsive
- no remote JS dependency
- use semantic sections/tables and lightweight CSS bars
- use `<details>` for dense secondary sections
- include report version/fingerprint and stale status in the header
- never re-run calculations during export

CSV must use `source_metrics["trade_ledger"]`.

- [ ] **Step 6: Add export routes**

Return:

- HTML: `text/html; charset=utf-8` with attachment filename `smoking-gun-<date_from>-<date_to>.html`
- CSV: `text/csv; charset=utf-8` with attachment filename `smoking-gun-ledger-<date_from>-<date_to>.csv`

- [ ] **Step 7: Run backend feature tests**

Run:

```bash
cd backend
pytest tests/test_smoking_gun_library.py tests/test_smoking_gun_exports.py tests/test_performance_report.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/main.py backend/smoking_gun_library.py backend/smoking_gun_exports.py backend/tests/test_smoking_gun_library.py backend/tests/test_smoking_gun_exports.py
git commit -m "feat: add Smoking Gun report library API"
```

---

### Task 5: Add Frontend API Client and Report Library Shell

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/SmokingGunLibrary.js`
- Modify: `frontend/src/components/SmokingGunReport.js`
- Modify: `frontend/src/app.integration.test.js`

**Interfaces:**
- Consumes backend routes from Task 4.
- Produces:
  - `smokingGunLibraryApi.list(params)`
  - `smokingGunLibraryApi.get(id)`
  - `smokingGunLibraryApi.remove(id)`
  - `smokingGunLibraryApi.downloadHtml(id)`
  - `smokingGunLibraryApi.downloadLedger(id)`
  - Smoking Gun submodes: `live` and `library`

- [ ] **Step 1: Add failing frontend API mocks and navigation test**

Extend the existing API mock in `frontend/src/app.integration.test.js` so the new client is mockable.

Add a test that opens Reports → Smoking Gun and asserts a secondary tablist with:

- `Live Analytics`
- `Report Library`

The live view remains the default for backward compatibility.

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
cd frontend
CI=true npm test -- --runInBand app.integration.test.js
```

Expected: FAIL because the secondary navigation is absent.

- [ ] **Step 3: Add the API client**

In `frontend/src/api.js`:

```javascript
export const smokingGunLibraryApi = {
  list: (params) => api.get('/api/smoking-gun-reports', { params }),
  get: (id) => api.get(`/api/smoking-gun-reports/${id}`),
  remove: (id) => api.delete(`/api/smoking-gun-reports/${id}`),
  downloadHtml: (id) => api.get(`/api/smoking-gun-reports/${id}/report.html`, { responseType: 'blob' }),
  downloadLedger: (id) => api.get(`/api/smoking-gun-reports/${id}/trade-ledger.csv`, { responseType: 'blob' }),
};
```

- [ ] **Step 4: Add the two-mode shell**

In `SmokingGunReport.js`, place a compact secondary segmented control above the existing live report.

Rules:

- `Live Analytics` renders the current component behavior.
- `Report Library` renders `<SmokingGunLibrary accountId={accountId} />`.
- switching modes does not refetch live analytics unnecessarily.
- no existing chart/table behavior is removed.

- [ ] **Step 5: Build metadata-only library cards**

`SmokingGunLibrary.js` must render:

- title
- date range
- generated timestamp
- trade count
- net P&L
- primary edge
- primary leak
- report version
- status chip: `CURRENT` or `SOURCE CHANGED`

Primary actions:

- Open Report
- Download HTML

Secondary overflow/actions:

- Download Trade Ledger
- Delete Report

Empty state copy:

`No saved Smoking Gun reports yet. Generate one from ChatGPT to build your audit history.`

- [ ] **Step 6: Add and pass library-shell integration tests**

Cover:

- metadata only list rendering
- newest-first order supplied by API is preserved
- stale report gets `SOURCE CHANGED`
- empty state
- loading/error state does not collapse the page
- delete requires an explicit confirmation interaction

Run:

```bash
cd frontend
CI=true npm test -- --runInBand app.integration.test.js
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api.js frontend/src/components/SmokingGunReport.js frontend/src/components/SmokingGunLibrary.js frontend/src/app.integration.test.js
git commit -m "feat: add Smoking Gun report library"
```

---

### Task 6: Build the Professional Saved-Report Terminal

**Files:**
- Create: `frontend/src/components/SmokingGunSavedReport.js`
- Create: `frontend/src/components/SmokingGunTerminal.css`
- Modify: `frontend/src/components/SmokingGunLibrary.js`
- Modify: `frontend/src/components/SmokingGunReport.js`
- Modify: `frontend/src/app.integration.test.js`

**Interfaces:**
- Consumes a detail payload returned by `smokingGunLibraryApi.get(id)`.
- Produces a native saved-report view with no recalculation and no AI call.

- [ ] **Step 1: Write failing professional-layout integration test**

Open a saved report and assert the visible hierarchy contains, in order:

1. Command Header
2. Executive Scoreboard
3. Two Traders
4. Edge Map / Hold Time
5. Position Size
6. Daily P&L
7. Daily Stop Lab
8. Ticker Ranking
9. Behavioral Forensics
10. Time Analysis
11. Scenario Model
12. Diagnosis
13. Mechanical Action Plan

Also assert:

- `SOURCE CHANGED` warning is present when `is_stale=true`
- diagnosis renders after deterministic evidence
- unsupported behavior evidence can render `Not supported as a leak`
- thin sample marker renders when supplied by the deterministic payload

- [ ] **Step 2: Run the test and verify failure**

Run:

```bash
cd frontend
CI=true npm test -- --runInBand app.integration.test.js
```

Expected: FAIL because the detail view is absent.

- [ ] **Step 3: Implement `SmokingGunSavedReport`**

The component receives:

```javascript
function SmokingGunSavedReport({ report, onBack, onDownloadHtml, onDownloadLedger })
```

It must display stored values only; do not derive new P&L, stop levels, behavior impacts, or labels in React.

Use existing Recharts only for visualization of already-aggregated arrays.

First viewport:

- compact command header
- net P&L
- win rate
- profit factor
- avg winner
- avg loser
- reward/risk
- max drawdown
- active days
- best/worst day

When a metric is unavailable in the saved payload, render an em dash with an explanatory tooltip rather than zero.

- [ ] **Step 4: Implement trader-grade styling**

In `SmokingGunTerminal.css`:

- scope all selectors under `.sg-terminal`
- use existing CSS variables from the journal
- compact 11–14px metadata/table typography
- restrained green/red result colors
- amber only for stale/thin evidence warnings
- no gradients except existing chart fills
- fixed-width numeric alignment via tabular numerals
- responsive card grids using `repeat(auto-fit, minmax(...))`
- mobile breakpoint keeps header/actions usable at 360px width
- tables use scroll containers only when cards cannot sensibly stack
- no animation longer than existing app transitions

- [ ] **Step 5: Add mobile/accessibility regression assertions**

Add tests for:

- report subnav and buttons have accessible names
- report detail has a single top-level heading
- back button restores library without losing list state
- download buttons remain available in detail
- no section depends on hover to expose critical data

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
cd frontend
CI=true npm test -- --runInBand
npm run build
```

Expected: all tests PASS and production build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/SmokingGunSavedReport.js frontend/src/components/SmokingGunTerminal.css frontend/src/components/SmokingGunLibrary.js frontend/src/components/SmokingGunReport.js frontend/src/app.integration.test.js
git commit -m "feat: build professional Smoking Gun report terminal"
```

---

### Task 7: Document the ChatGPT ↔ Trading Journal Contract

**Files:**
- Create: `docs/smoking-gun-report-contract.md`

**Interfaces:**
- Consumes the schema/functions implemented in Tasks 1–4.
- Produces the stable instructions future ChatGPT sessions need to read source data and write a valid report without a duplicate broker upload.

- [ ] **Step 1: Document the read contract**

Specify that connected analysis reads from `journal.trades` and must filter by:

- `account_id`
- inclusive `date_from`
- inclusive `date_to`

Document the exact canonical fields used by `build_source_fingerprint`.

- [ ] **Step 2: Document the write contract**

Document every `journal.smoking_gun_reports` column and the JSON shapes expected for:

- `source_metrics_json`
- `diagnosis_json`
- `action_plan_json`
- `export_manifest_json`

Document that direct connected writes must:

- use the user's authorized Supabase connection
- never expose or request a service-role key in chat
- compute the canonical fingerprint from current source data immediately before save
- use `status='complete'` only after the full report payload exists
- respect the uniqueness key
- never overwrite an older report in place

- [ ] **Step 3: Document the one-line user workflow**

Include examples:

- `Generate Smoking Gun for September.`
- `Generate YTD Smoking Gun.`
- `Generate Smoking Gun for SPY and QQQ only.`

Clarify that symbol-filtered variants require the report title and diagnosis to disclose the narrower population; the saved report's source contract must include the applied filter metadata.

- [ ] **Step 4: Commit**

```bash
git add docs/smoking-gun-report-contract.md
git commit -m "docs: define connected Smoking Gun report contract"
```

---

### Task 8: Apply Supabase Schema, Run Security Checks, and Verify the Connected Workflow

**Files:**
- No new product files unless verification exposes a bug.
- Use the migration generated in Task 1.

**Interfaces:**
- Consumes all prior tasks.
- Produces a verified production-compatible schema and an end-to-end saved-report flow.

- [ ] **Step 1: Re-check current Supabase documentation/changelog before applying**

Per the Supabase skill, fetch the current changelog and relevant migration/RLS docs immediately before deployment. Confirm no breaking change affects private schemas, RLS, or Postgres migration behavior.

- [ ] **Step 2: Apply the migration to the connected Supabase project**

Apply the exact reviewed migration to project `ppsljqaaanpkksxbpalk`.

Do not expose `journal` to the Data API and do not grant `anon`/`authenticated` direct table access.

- [ ] **Step 3: Verify the deployed table**

Run read-only introspection and assert:

- `journal.smoking_gun_reports` exists
- RLS is enabled
- required indexes/unique constraint exist
- `anon` and `authenticated` have no direct table privileges
- `journal.schema_migrations` contains `20260926_002_smoking_gun_reports`

- [ ] **Step 4: Run Supabase advisors**

Run security and performance advisors.

Expected:

- no new report-table security error
- no missing FK/index warning for `smoking_gun_reports.account_id`

If an advisor reports a new issue from this migration, fix it before continuing.

- [ ] **Step 5: Run the full local verification suite**

Backend:

```bash
cd backend
pytest -v
```

Frontend:

```bash
cd frontend
CI=true npm test -- --runInBand
npm run build
```

Expected: PASS.

- [ ] **Step 6: Verify against the connected journal dataset without modifying trades**

Using the existing account's stored trades:

- read the selected source rows directly from Supabase
- build the same deterministic fingerprint as the backend
- generate a report payload from those rows
- verify trade count and total net P&L reconcile to `journal.trades`
- save one completed report row through the authorized connected workflow
- query it back
- verify Report Library API/detail can read it
- verify HTML and ledger downloads render from the saved snapshot

Do not alter or delete source trades during this smoke test.

- [ ] **Step 7: Verify duplicate and stale behavior safely**

- Attempt to save the same fingerprint/version again and verify no duplicate row is created.
- Test stale detection using an isolated local/test database fixture rather than changing production trading history.

- [ ] **Step 8: Whole-feature verification**

Confirm all success criteria from the spec:

- one broker import only
- ChatGPT can read journal data directly
- report can be saved back
- library lists it
- detail opens quickly
- downloads work
- deterministic values reconcile
- stale/duplicate states are explicit
- mobile layout remains usable

- [ ] **Step 9: Commit any verification-only fixes**

If verification required code changes:

```bash
git add <changed files>
git commit -m "fix: harden connected Smoking Gun workflow"
```

If no fixes were needed, do not create an empty commit.
