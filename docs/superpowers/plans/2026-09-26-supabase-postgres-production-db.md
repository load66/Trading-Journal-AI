# Supabase Postgres Production Database — Implementation Plan

**Spec:** `docs/superpowers/specs/2026-09-26-supabase-postgres-production-db-design.md`

## Global constraints

- Preserve local SQLite behavior.
- No production DB credentials in GitHub.
- Hosted mode uses Supabase Postgres only; no Turso fallback.
- Keep route/API contracts unchanged.
- Use TDD for executable behavior.
- Do not mix Schwab importer or automatic trade-analysis features into this change.

### Task 1 — Configuration contract

Files:
- Modify `backend/tests/test_database_modes.py`
- Modify `backend/config.py`
- Modify `backend/requirements.txt`

Steps:
- Write failing tests for `DATABASE_MODE=postgres`, missing `SUPABASE_DB_URL`, and removal of Turso-only configuration.
- Run focused backend tests and confirm RED.
- Implement settings changes and replace Turso dependency with pinned Psycopg 3 binary distribution.
- Run focused tests and full backend suite.
- Commit.

### Task 2 — Postgres DB-API compatibility adapter

Files:
- Modify `backend/database.py`
- Modify `backend/tests/test_database_modes.py`

Steps:
- Write failing unit tests for qmark translation, row dual-access, commit/rollback/close delegation, executescript, and lastrowid behavior using small fake DB-API objects.
- Confirm RED.
- Implement `PostgresConnectionAdapter` / cursor compatibility with lazy Psycopg connection creation.
- Configure search path to `journal, public`.
- Confirm focused GREEN and run full backend suite.
- Commit.

### Task 3 — Portable schema and migrations

Files:
- Modify `backend/database.py`
- Modify `backend/library.py`
- Modify `backend/tests/test_database_modes.py`

Steps:
- Write failing tests proving Postgres schema DDL has no SQLite-only AUTOINCREMENT/datetime/PRAGMA constructs and that Postgres column discovery uses information_schema.
- Confirm RED.
- Add provider-specific schema statements and migration metadata.
- Make library-table initialization use provider-portable DDL through the connection boundary.
- Run focused and full backend tests.
- Commit.

### Task 4 — Runtime SQL portability

Files:
- Modify `backend/main.py`
- Modify relevant backend tests

Steps:
- Write failing tests for daily-summary upsert SQL and yearly KPI year filtering under Postgres mode.
- Confirm RED.
- Replace `INSERT OR REPLACE` with explicit `ON CONFLICT DO UPDATE`.
- Replace provider-specific yearly-date expression through a database helper.
- Ensure import upsert timestamp is portable.
- Replace SQLite-specific integrity exception catch with a cross-provider database integrity exception tuple/type.
- Run focused and complete backend suite.
- Commit.

### Task 5 — Deployment configuration and documentation

Files:
- Modify `.env.example`
- Modify `render.yaml`
- Modify `docs/deployment.md`
- Modify canonical web deployment spec/plan references where they still name Turso.

Steps:
- Remove Turso variables and add `SUPABASE_DB_URL`.
- Document Supavisor session-mode/5432 requirement for Render.
- Keep DB password/URL server-only.
- Run repository secret scan and backend/frontend CI.
- Commit.

### Task 6 — Supabase cloud schema

External target:
- connected Supabase project `trading-journal-ai`

Steps:
- Apply a non-destructive migration creating the private `journal` schema and current tables/indexes.
- Verify tables in `journal`.
- Verify `public` still contains no journal tables.
- Run Supabase security and performance advisors.
- Fix any Critical/Important advisor findings introduced by this migration.
- Execute a transaction-safe smoke test (insert/select/delete isolated test row) and leave no test data behind.

### Task 7 — Final branch verification

- Run full backend CI.
- Run frontend test/build CI.
- Review PR diff specifically for leftover Turso references and accidental credentials.
- Update PR description to Supabase-only database architecture.
- Keep PR draft until Render/GitHub external deployment values are configured.
