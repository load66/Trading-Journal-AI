# Web Deployment v1 Design — Supabase Postgres Revision

**Date:** 2026-09-26  
**Repository:** `load66/Trading-Journal-AI`  
**Branch:** `feature/web-deployment-v1`  
**Status:** Revised architecture pending implementation-plan review

## Intent

Turn Trading Journal AI into a private, single-user web application that is available from phone and desktop, preserves the existing localhost workflow, stays free-first, and keeps the user's trading data accessible for future in-app and ChatGPT-assisted analysis.

This revision replaces the previously selected Turso production database with **Supabase Postgres**. Supabase now provides authentication, the production trading database, and private diary-file storage in one platform.

## Success criteria

1. React deploys to `https://load66.github.io/Trading-Journal-AI/`.
2. Hosted users must authenticate with the pre-created Supabase email/password account.
3. FastAPI rejects missing, invalid, expired, wrong-project, or wrong-user access tokens.
4. Local development continues to use SQLite with no cloud credentials unless hosted mode is explicitly enabled.
5. Hosted trading data persists in Supabase Postgres and survives Render restarts/spin-downs.
6. Diary images persist in the private Supabase `diary` bucket.
7. Trading tables are not exposed for browser CRUD; FastAPI remains the application data boundary.
8. Supabase, Anthropic, Alpaca, and database credentials never enter the browser bundle or Git repository.
9. Existing import/reconstruction/report/diary behavior remains functionally compatible.
10. The connected Supabase project can be queried directly through ChatGPT for future journal analytics and automation work.
11. No Turso dependency or Turso provisioning remains in the production architecture.

## Architecture

### Hosted path

```text
GitHub repository
       |
       +--> GitHub Actions --> GitHub Pages (React)
                                  |
                                  | Supabase email/password session
                                  | Authorization: Bearer <access token>
                                  v
                              Render Free
                              FastAPI API
                               /       \
                              /         \
                Supabase Postgres    Supabase Storage
                trading data        private diary files
                       |
                       +--> Anthropic / Alpaca
                            server-side secrets only
```

### Local path

```text
React localhost:3010
       |
       v
FastAPI localhost:8010
       |
       v
SQLite trading_journal.db
local uploads/
```

Local remains the default when hosted environment variables are absent.

## Technology decisions

### Frontend: GitHub Pages

GitHub Pages hosts only the static React build. Browser-visible configuration is limited to:

- `REACT_APP_API_URL`
- `REACT_APP_AUTH_REQUIRED=true`
- `REACT_APP_SUPABASE_URL`
- `REACT_APP_SUPABASE_PUBLISHABLE_KEY`

No database password, Supabase secret key, Anthropic key, or Alpaca secret enters the React bundle.

### Authentication: Supabase Auth

Email/password login only. No signup UI.

The React app obtains a Supabase access token and sends it to FastAPI. FastAPI verifies:

- cryptographic signature through the project JWKS;
- issuer `<SUPABASE_URL>/auth/v1`;
- audience `authenticated`;
- expiry;
- `sub` exactly matching the configured `AUTHORIZED_USER_ID`.

This remains a single-user application even if another Supabase account is ever created.

### Production database: Supabase Postgres

Production uses PostgreSQL in the existing Supabase project. Local development continues to use SQLite.

Runtime selection:

- `DATABASE_MODE=sqlite` → Python `sqlite3`;
- `DATABASE_MODE=postgres` → `psycopg` connection to Supabase Postgres.

Hosted configuration uses one server-side connection string:

- `DATABASE_URL`

For Render/free hosting, use the **Supavisor session-mode connection string** when the runtime network is IPv4-only. Supabase currently recommends session mode for persistent IPv4 application backends. Direct connections remain suitable where IPv6 is available.

The backend must never silently fall back to local SQLite when `DATABASE_MODE=postgres`.

### SQL compatibility boundary

The current application contains substantial SQLite-oriented SQL. The migration must preserve behavior without rewriting every route at once.

A database compatibility layer will:

- normalize rows to mapping/dict behavior;
- translate DB-API parameter placeholders from the application's existing `?` convention to PostgreSQL `%s` for parameterized statements;
- use PostgreSQL-safe identity/default syntax in the production schema;
- replace SQLite-only schema inspection (`PRAGMA table_info`) with PostgreSQL catalog/information-schema queries in Postgres mode;
- keep transaction semantics explicit: commit on success, rollback on failure;
- provide an explicit insert-id helper using `RETURNING id` in Postgres rather than depending on SQLite `lastrowid`;
- reject unsupported SQLite-only SQL explicitly instead of silently producing different behavior.

Do not introduce an ORM in v1. A focused compatibility layer is lower-risk and keeps the existing business logic recognizable.

### Schema design

Supabase Postgres gets the same logical entities already used locally:

- `accounts`
- `trades`
- `diary_entries`
- `trade_analysis`
- `trade_tags`
- `daily_summaries`
- `settings`
- `custom_setups`
- `schema_migrations`

Use native PostgreSQL identity columns, timestamps/defaults, constraints, and indexes while preserving current API-visible values and relationships.

The production schema is created through a committed, reviewable Supabase migration. Application startup may verify schema version, but it must not perform broad ad-hoc DDL on every request.

### Supabase table security

The React app does **not** query trading tables directly through Supabase's Data API.

Defense-in-depth requirements:

- enable RLS on all application tables in an exposed schema;
- revoke `anon` and `authenticated` table privileges unless a future feature explicitly requires browser-side access;
- create no permissive browser RLS policies in v1;
- backend access uses the server-side Postgres connection, not the browser publishable key;
- never authorize based on user-editable `user_metadata`.

The FastAPI JWT boundary remains the primary application authorization layer.

### Diary file storage

Local mode uses the existing filesystem abstraction. Hosted mode uses the private Supabase `diary` bucket.

Hosted variables:

- `STORAGE_MODE=supabase`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SUPABASE_STORAGE_BUCKET=diary`

The Supabase secret key is backend-only.

Files are retrieved through authenticated FastAPI routes. The bucket stays private.

### Render backend

Render runs stateless FastAPI.

The Blueprint must:

- set `APP_ENV=production`;
- set `AUTH_REQUIRED=true`;
- set `DATABASE_MODE=postgres`;
- set `STORAGE_MODE=supabase`;
- request `DATABASE_URL` instead of Turso variables;
- keep all secret values outside source control;
- deploy only after CI checks pass.

The backend must not depend on Render's filesystem for durable database or diary data.

## Production schema migration strategy

There is currently no production trading data in Supabase, so this is a clean bootstrap rather than a live data migration.

Implementation sequence:

1. create/test the Postgres compatibility layer against isolated tests;
2. create a Supabase migration representing the current logical schema;
3. apply it to the connected Supabase project;
4. enable RLS and revoke browser-role table access;
5. run Supabase security/performance advisors;
6. run test queries through the Supabase connector;
7. only then configure Render to use the Postgres connection string.

Existing local SQLite files are not automatically uploaded. A separate import/migration utility can be added later if historical local journal data needs to be transferred.

## Failure behavior

- Missing Postgres configuration in hosted mode: startup fails closed.
- Database connection failure: request fails; no fallback to local SQLite.
- Schema-version mismatch: startup/health diagnostics expose a server-side configuration error without leaking credentials.
- Invalid/missing auth token: HTTP 401.
- Valid token for another user: HTTP 403.
- Supabase Storage upload failure: do not create a diary row pointing to a missing object.
- Unexpected production exception: return a generic 500 body; log details server-side.
- GitHub Pages missing required public variables: deployment fails instead of publishing a broken app.

## Testing strategy

Backend tests must cover:

- SQLite remains the default;
- Postgres mode requires `DATABASE_URL`;
- no Turso configuration remains;
- placeholder translation preserves bound parameters;
- mapping-row behavior is compatible;
- insert ID behavior works in both engines;
- schema introspection works in both engines;
- migration/version errors propagate;
- existing broker-import/reimport tests remain green;
- auth/storage/production-error tests remain green.

Integration verification must include:

- Supabase schema exists with expected columns/constraints/indexes;
- RLS is enabled on application tables;
- browser roles do not have direct CRUD privileges;
- Supabase security advisor is reviewed;
- Supabase performance advisor is reviewed;
- Render can connect using the server-side Postgres URL;
- a created trade survives a backend restart.

Frontend auth/deployment behavior remains unchanged from the approved v1 design.

## Future trade-analysis automation

Using Supabase Postgres for trading data intentionally creates a clean foundation for future analysis.

A later feature may:

- compute deterministic trade metrics server-side after import;
- persist analysis snapshots and rule-adherence results;
- expose user-triggered or event-triggered AI coaching inside the journal;
- allow ChatGPT, through the connected Supabase integration, to query journal performance directly.

That feature is **not** mixed into this database-provider migration. The database migration only creates the foundation for it.

## Deployment/provisioning boundary

Already provisioned in Supabase:

- project;
- single private user;
- public signup disabled;
- private `diary` bucket with 10 MB object limit;
- project URL and publishable key available through the connected Supabase integration.

Still needed after code/schema work:

1. obtain/configure the server-side Supabase Postgres connection string in Render;
2. configure remaining Render secrets;
3. add GitHub Pages public variables;
4. merge PR only after full verification;
5. verify the live site and persistence.

## Removed from the previous design

The following are no longer part of v1:

- Turso database;
- `turso_serverless`;
- `TURSO_DATABASE_URL`;
- `TURSO_AUTH_TOKEN`;
- Turso provisioning documentation.

## Deferred work

- Schwab CSV adapter;
- automatic trade-analysis/coaching subsystem;
- multi-user tenancy/RBAC;
- major UI redesign;
- paid always-on hosting;
- normalized executions-table refactor;
- historical local-SQLite-to-cloud import utility unless needed before launch.
