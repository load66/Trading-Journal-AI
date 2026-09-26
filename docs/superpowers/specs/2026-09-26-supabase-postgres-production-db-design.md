# Supabase Postgres Production Database — Design

**Date:** 2026-09-26  
**Repository:** `load66/Trading-Journal-AI`  
**Branch:** `feature/web-deployment-v1`

## Intent

Replace Turso as the hosted trading database with the user's existing Supabase Postgres project while preserving local SQLite development and the current API/frontend contracts.

This reduces the hosted architecture from four providers to three and makes the journal's trading data directly accessible through the connected Supabase integration for future analysis workflows.

## Approved architecture

### Hosted

```text
GitHub Pages (React)
        |
        | Supabase Auth JWT
        v
Render Free (FastAPI)
        |
        +--> Supabase Postgres / journal schema
        +--> Supabase private Storage / diary
        +--> Anthropic / Alpaca
```

### Local

```text
React localhost -> FastAPI localhost -> local SQLite + local uploads
```

Local development remains the default and requires no Supabase database credentials.

## Database placement and security

Trading tables live in a dedicated non-exposed Postgres schema named `journal`, not `public`.

Reasons:
- the FastAPI backend connects directly to Postgres and does not require Supabase Data API exposure;
- a private schema reduces accidental REST/Data API exposure;
- the existing FastAPI auth boundary remains the sole user-facing data authorization layer;
- ChatGPT's connected Supabase management integration can still inspect/query the schema for user-requested journal analysis and maintenance.

No browser code receives a Postgres connection string or database password.

## Runtime connection

Production uses a single environment variable:

`SUPABASE_DB_URL`

For Render Free, use Supabase's Shared Pooler (Supavisor) in **session mode, port 5432**, because Render is IPv4-only and the free project's direct Postgres endpoint is IPv6. Session mode is appropriate for a persistent FastAPI container.

The application uses Psycopg 3.

## Compatibility boundary

The existing application contains many SQLite-style SQL calls. Rewriting every route is unnecessary and risky. Introduce a Postgres connection/cursor compatibility adapter that preserves the existing route-level interface.

The adapter will:
- translate qmark placeholders (`?`) to Psycopg placeholders (`%s`);
- preserve mapping and positional row access used by `sqlite3.Row`;
- expose `execute`, `executemany`, `executescript`, `commit`, `rollback`, and `close`;
- provide SQLite-compatible `lastrowid` semantics by reading Postgres `lastval()` after inserts that use identity/serial sequences;
- set the Postgres search path to `journal, public`.

Provider-specific SQL that cannot be safely translated generically is changed explicitly:
- `datetime('now')` -> portable/current-timestamp form;
- `strftime('%Y', date)` -> provider-aware year expression;
- `INSERT OR REPLACE` daily-summary cache writes -> explicit `ON CONFLICT ... DO UPDATE`.

## Schema

Production Postgres tables mirror the existing local SQLite logical schema:
- accounts
- trades
- diary_entries
- trade_analysis
- trade_tags
- daily_summaries
- settings
- custom_setups
- library_items
- library_aliases
- schema_migrations

Postgres uses generated identity/bigserial primary keys and `TIMESTAMPTZ` for creation/import timestamps where the app treats them as timestamps. Existing trade/date fields remain text-compatible where application code slices/compares ISO dates.

Foreign keys and unique constraints remain equivalent to the existing application.

## Migrations

The application's migration runner remains idempotent for SQLite.

For Postgres:
- schema bootstrap is explicit and idempotent;
- column existence checks use `information_schema.columns`;
- migration history is stored in `journal.schema_migrations`;
- real migration errors propagate and fail startup;
- no fallback to local SQLite is allowed when `DATABASE_MODE=postgres`.

The Supabase project receives the same schema through a tracked Supabase migration so the live cloud schema is reviewable and reproducible.

## Configuration

Remove production Turso settings:
- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`
- `turso_serverless`

Add:
- `DATABASE_MODE=postgres`
- `SUPABASE_DB_URL`

Keep:
- `DATABASE_MODE=sqlite`
- `DATABASE_PATH`

for local development.

## Error behavior

- Missing `SUPABASE_DB_URL` in Postgres mode: startup/config failure.
- Postgres unavailable: request/startup fails visibly; never fall back to ephemeral SQLite.
- Unsupported SQL construct in the compatibility layer: fail with the original database error rather than silently rewriting incorrectly.
- Existing production generic 500 sanitization remains unchanged.

## Testing

Backend tests must prove:
1. SQLite remains the default.
2. Postgres mode requires `SUPABASE_DB_URL`.
3. qmark parameters are translated correctly without changing parameter values.
4. rows support both `row['column']` and `row[index]`.
5. `lastrowid` compatibility is deterministic for inserts.
6. Postgres schema bootstrap uses Postgres-valid DDL.
7. migration column discovery works for both SQLite and Postgres.
8. daily-summary upserts no longer depend on SQLite `INSERT OR REPLACE`.
9. yearly KPI filtering uses provider-aware SQL.
10. the complete existing broker-import/reimport suite remains green.

After repo tests pass, create/apply the `journal` schema migration to the connected Supabase project and verify tables plus security/performance advisors.

## Deferred

- converting local development from SQLite to Postgres;
- exposing journal tables through Supabase Data API;
- multi-user/RLS tenancy;
- normalized executions table;
- Schwab importer;
- automated trade-analysis feature itself.
