# Supabase Postgres Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unfinished Turso production path with Supabase Postgres while preserving the existing local SQLite workflow and all current journal behavior.

**Architecture:** Local development stays on SQLite. Hosted FastAPI uses a server-side psycopg connection to Supabase Postgres with `journal` as a private application schema. A focused DB-API compatibility layer preserves the application's existing qmark-parameter SQL and mapping-row expectations, while production schema DDL is owned by a versioned Supabase migration rather than being created ad hoc at runtime.

**Tech Stack:** Python 3, FastAPI, sqlite3, psycopg 3, Supabase Postgres/Supavisor, Supabase MCP, pytest, GitHub Actions, Render.

**Spec:** `docs/superpowers/specs/2026-09-26-web-deployment-v1-design.md`

## Global Constraints

- Local development must continue to work with `DATABASE_MODE=sqlite` and no cloud database credentials.
- Hosted mode uses `DATABASE_MODE=postgres` and one server-side `DATABASE_URL`; it must never fall back to local SQLite.
- Render should use Supavisor **session mode** when an IPv4-compatible persistent backend connection is required.
- No database password or connection string may enter the React bundle or Git repository.
- FastAPI remains the only application CRUD boundary; browser roles do not receive direct trading-table CRUD access.
- All application tables in Supabase live in the private `journal` schema, have RLS enabled as defense in depth, and are not exposed through permissive browser policies.
- Preserve existing broker import/reimport behavior, Library behavior, diary behavior, reports, and localhost tests.
- Do not add an ORM or mix Schwab-importer/AI-coaching feature work into this migration.
- Remove Turso code/config/docs from the production path.
- Unexpected schema/configuration problems must fail visibly rather than silently creating or switching databases.

## Review Focus

- SQL containing a literal/question-mark character inside a quoted string must not have that character rewritten as a psycopg placeholder; the owning database-adapter task includes a regression test.
- Insert paths that currently depend on SQLite `lastrowid` must return the correct identity under Postgres without `lastval()`; the owning adapter/call-site task includes tests.
- Postgres startup before the Supabase schema migration exists must fail with a clear schema-version error instead of creating partial tables; the schema-verification task includes a test.
- Library initialization must not execute SQLite-only `executescript`/DDL against Postgres; the schema-verification task includes a test.
- Supabase `anon` and `authenticated` roles must not have direct CRUD access to journal tables even though RLS is enabled; the Supabase verification task explicitly tests privileges and RLS state.

---

### Task 1: Reconcile the database configuration and Postgres adapter

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/database.py`
- Modify: `backend/tests/test_database_modes.py`
- Modify: `backend/requirements.txt` only if dependency cleanup is needed

**Interfaces:**
- Produces: `Settings.database_url: str`, `get_db(settings=None)`, `PostgresConnectionAdapter`, `translate_qmark_sql(sql: str) -> str`, `insert_and_get_id(conn, sql: str, params=()) -> int`.
- Consumes: `DATABASE_MODE`, `DATABASE_PATH`, `DATABASE_URL`.

- [ ] **Step 1: Update the failing configuration tests**
  - Assert `DATABASE_MODE=postgres` with missing `DATABASE_URL` raises `ConfigError`.
  - Assert `DATABASE_MODE=turso` is rejected.
  - Assert SQLite remains the default.

- [ ] **Step 2: Add failing SQL-adapter tests**
  - `SELECT ... WHERE id=?` becomes `... id=%s` with unchanged bound parameters.
  - A quoted literal such as `SELECT '?' AS marker, id FROM trades WHERE id=?` keeps the quoted `?` and rewrites only the parameter placeholder.
  - Wrapped rows support integer index, name index, and `dict(row)`.
  - Postgres connect sets `search_path TO journal, public`.
  - Postgres insert ID uses `RETURNING id`, not `SELECT lastval()`.

- [ ] **Step 3: Run targeted tests and confirm RED**
  - Run: `cd backend && pytest tests/test_database_modes.py -v`
  - Expected: failures against the branch's current split Turso/Postgres implementation.

- [ ] **Step 4: Implement the configuration contract**
  - Rename the current hosted DB setting to `database_url`.
  - Read only `DATABASE_URL` for Postgres mode.
  - Keep SQLite configuration unchanged.
  - Remove Turso URL/token fields and Turso mode handling.

- [ ] **Step 5: Implement the Postgres compatibility layer**
  - Use `psycopg.connect(settings.database_url, autocommit=False)`.
  - Wrap cursor rows in the existing mapping-compatible `DBAPIRow`.
  - Implement quote-aware qmark-to-`%s` translation.
  - Set `search_path TO journal, public` once per Postgres connection.
  - Do not implement SQLite PRAGMA behavior in Postgres mode.

- [ ] **Step 6: Implement `insert_and_get_id`**
  - SQLite: execute original INSERT and return `cursor.lastrowid`.
  - Postgres: append/require `RETURNING id`, fetch the returned id, and return it.
  - Do not use `lastval()`.

- [ ] **Step 7: Run targeted tests and confirm GREEN**
  - Run: `cd backend && pytest tests/test_database_modes.py -v`
  - Expected: 0 failures.

- [ ] **Step 8: Commit**
  - Commit message: `refactor: add Supabase Postgres database adapter`.

### Task 2: Make runtime schema initialization engine-aware

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/library.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/test_database_modes.py`
- Add/modify focused Library tests if required by existing test layout

**Interfaces:**
- Produces: `verify_postgres_schema(conn) -> None`, engine-safe `init_db(settings=None)`, engine-safe `init_library_tables(conn)`.
- Consumes: Task 1's connection adapter and `DATABASE_MODE`.

- [ ] **Step 1: Add failing schema-verification tests**
  - Postgres mode does not execute SQLite `CREATE TABLE ... AUTOINCREMENT`, `datetime('now')`, or PRAGMA statements.
  - Missing `journal.schema_migrations`/required schema version raises a clear configuration/runtime error.
  - A valid expected schema version passes without running DDL.
  - SQLite `init_db` remains idempotent and still applies local migrations.

- [ ] **Step 2: Add a failing Library compatibility test**
  - Calling `init_library_tables` with a Postgres connection does not call SQLite `executescript`.
  - It verifies/uses the already-migrated `library_items` and `library_aliases` tables instead.

- [ ] **Step 3: Run focused tests and confirm RED**
  - Run: `cd backend && pytest tests/test_database_modes.py -v` plus the focused Library test module.

- [ ] **Step 4: Split local schema creation from hosted schema verification**
  - SQLite path keeps current schema creation plus ordered local migrations.
  - Postgres path performs connection + expected-version/table verification only.
  - Define one exact application schema version constant used by code and the Supabase migration.

- [ ] **Step 5: Make Library initialization engine-safe**
  - SQLite keeps its existing local table creation behavior.
  - Postgres treats the tables as migration-owned and only verifies availability.

- [ ] **Step 6: Replace all production insert-ID call sites**
  - Modify the four `backend/main.py` insert paths that currently use `lastrowid` to call Task 1's `insert_and_get_id`.
  - Preserve API payloads and transaction boundaries.

- [ ] **Step 7: Run focused tests and the backend regression suite**
  - Run: `cd backend && pytest tests/test_database_modes.py -v && pytest tests -q`
  - Expected: 0 failures.

- [ ] **Step 8: Commit**
  - Commit message: `refactor: separate sqlite bootstrap from postgres schema verification`.

### Task 3: Create and apply the Supabase journal schema migration

**Files:**
- Create after Supabase assigns the migration version: `supabase/migrations/<server-version>_trading_journal_schema.sql`
- No application-code change unless verification exposes a schema mismatch

**Interfaces:**
- Produces: private Supabase schema `journal` with the complete production schema and application schema version marker.
- Consumes: the schema/version contract from Task 2.

- [ ] **Step 1: Build one migration SQL definition**
  - Create private schema `journal`.
  - Create: `accounts`, `trades`, `diary_entries`, `trade_analysis`, `trade_tags`, `daily_summaries`, `settings`, `custom_setups`, `library_items`, `library_aliases`, and `schema_migrations`.
  - Use PostgreSQL identity columns and `timestamptz NOT NULL DEFAULT now()` where the SQLite schema currently uses auto-increment/date defaults.
  - Preserve current checks, uniqueness, foreign keys, indexes, and current migrated columns such as setup/MFE/MAE/exit-efficiency fields.
  - Insert the exact application schema version expected by Task 2.

- [ ] **Step 2: Add defense-in-depth database security to the migration**
  - Enable RLS on every application table.
  - Revoke `ALL` on schema/tables/sequences from `anon` and `authenticated`.
  - Do not create browser CRUD policies.
  - Keep backend access through the server-side Postgres connection.

- [ ] **Step 3: Apply the migration once through the connected Supabase project**
  - Use Supabase `apply_migration` with a descriptive snake_case migration name.
  - Immediately call `list_migrations` and capture Supabase's assigned migration version.

- [ ] **Step 4: Commit the exact applied SQL**
  - Create the repository migration file using the server-returned migration version; do not invent a timestamp/version.
  - Commit message: `db: create private Supabase journal schema`.

- [ ] **Step 5: Verify schema shape with Supabase**
  - `list_tables` for schema `journal` must show all expected tables.
  - Query `information_schema.columns` / `pg_indexes` for required columns, constraints, and indexes.
  - Query `pg_class.relrowsecurity` to confirm RLS is enabled on every application table.
  - Query grants to confirm `anon` and `authenticated` lack direct CRUD privileges.

- [ ] **Step 6: Run Supabase advisors**
  - Run security advisor and review every finding.
  - Run performance advisor and review every finding.
  - Fix any finding caused by this migration before proceeding.

- [ ] **Step 7: Run a rollback-safe data smoke test**
  - In one SQL transaction: insert a temporary account/trade using the production schema, select it back, then roll back.
  - Confirm the rows are absent after rollback.

### Task 4: Replace Turso deployment configuration with Supabase Postgres

**Files:**
- Modify: `render.yaml`
- Modify: `.env.example`
- Modify: `docs/deployment.md`
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-09-26-web-deployment-v1.md` only to mark it superseded by this migration plan if needed
- Modify: PR #1 description

**Interfaces:**
- Produces: one hosted DB secret, `DATABASE_URL`, and current deployment documentation.
- Consumes: existing Supabase Auth/Storage values and Task 3's migrated schema.

- [ ] **Step 1: Update Render Blueprint**
  - Set `DATABASE_MODE=postgres`.
  - Replace Turso variables with one secret `DATABASE_URL`.
  - Preserve `AUTH_REQUIRED=true`, `STORAGE_MODE=supabase`, CORS origin, and existing backend-only secrets.

- [ ] **Step 2: Update environment example**
  - Remove `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN`.
  - Add `DATABASE_URL=` with a server-only comment.
  - Keep local `DATABASE_MODE=sqlite` as the example default.

- [ ] **Step 3: Rewrite deployment runbook**
  - Remove the Turso provisioning step entirely.
  - Document Supabase Postgres session-pooler connection for an IPv4 persistent backend.
  - Explicitly say the database password/URL is a Render secret and must never be placed in a `REACT_APP_*` variable.
  - Preserve the existing Auth, private Storage, Render, and GitHub Pages steps.

- [ ] **Step 4: Update PR #1 description**
  - State that production persistence is Supabase Postgres, not Turso.
  - Record the verified migration/advisor status without exposing credentials.

- [ ] **Step 5: Commit**
  - Commit message: `deploy: switch hosted database to Supabase Postgres`.

### Task 5: Full verification and migration review

**Files:**
- Modify only files required by test/review findings

**Interfaces:**
- Consumes all previous tasks.
- Produces a merge-ready branch with no Turso dependency and verified Supabase schema.

- [ ] **Step 1: Search the branch for stale provider references**
  - Search tracked files for `Turso`, `turso_serverless`, `TURSO_DATABASE_URL`, and `TURSO_AUTH_TOKEN`.
  - Expected: no active production-code/config references; historical/superseded docs must be clearly marked or removed.

- [ ] **Step 2: Run complete backend verification**
  - Run: `cd backend && pytest tests -q`.
  - Expected: 0 failures.

- [ ] **Step 3: Run complete frontend verification**
  - Run: `cd frontend && npx craco test --watchAll=false`.
  - Expected: 0 failures.

- [ ] **Step 4: Run production frontend build**
  - Run with the required public hosted variables and `PUBLIC_URL=/Trading-Journal-AI`: `cd frontend && npm run build`.
  - Expected: exit 0.

- [ ] **Step 5: Verify Supabase again**
  - Confirm project healthy.
  - Confirm expected `journal` tables/version.
  - Confirm RLS and browser-role revocations.
  - Re-run security advisor after any schema fix.

- [ ] **Step 6: Review the complete PR diff against the approved spec**
  - Pay special attention to auth bypass, accidental public DB access, query translation, insert identities, transaction boundaries, Library initialization, and secret leakage.
  - Fix Critical/Important findings test-first.

- [ ] **Step 7: Fresh CI evidence**
  - Push final fixes and require current-head backend + frontend CI to pass before marking the PR ready.

- [ ] **Step 8: Do not merge yet if Render/GitHub variables are not provisioned**
  - Keep PR draft until `DATABASE_URL` and other deployment secrets/public variables are ready so a main-branch Pages deployment cannot publish a broken hosted app.
