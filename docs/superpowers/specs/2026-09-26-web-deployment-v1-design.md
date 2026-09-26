# Web Deployment v1 Design

**Date:** 2026-09-26  
**Repository:** `load66/Trading-Journal-AI`  
**Branch:** `feature/web-deployment-v1`

## Purpose

Turn the existing local-first Trading Journal AI into a secure, private, web-accessible personal application while preserving local development and avoiding a premature rewrite of the trading engine.

The production target is free-first:

- React frontend hosted by GitHub Pages.
- FastAPI backend hosted on Render Free.
- Turso Cloud as the persistent SQLite-compatible production database.
- Supabase Auth for a single private email/password account.
- Supabase Storage for private diary screenshots/uploads that must persist.
- Anthropic/Alpaca credentials remain server-side only.

The application must remain usable locally with the existing SQLite file workflow.

## Success Criteria

1. `https://load66.github.io/Trading-Journal-AI/` can serve the React application from GitHub Pages.
2. Production displays a login gate before journal data is accessible.
3. Public self-signup is not part of the application UI.
4. The backend rejects unauthenticated requests to journal APIs.
5. The backend accepts only the configured Supabase user ID even if other Supabase accounts exist.
6. Local development continues to work without Supabase, Turso, or Render credentials when auth is explicitly disabled for local mode.
7. Production database data survives Render restarts, redeploys, and free-tier spin-down.
8. Diary images are not stored on Render's ephemeral filesystem in production.
9. Anthropic, Alpaca, Turso auth tokens, Supabase service credentials, and any other secrets are never embedded in the React bundle or committed to GitHub.
10. Existing broker parsing, trade calculations, analytics, reports, and local SQLite behavior are not intentionally changed by this deployment work.
11. Backend and frontend tests continue to run in CI.
12. Deployment configuration fails clearly when required production environment variables are missing instead of silently running insecurely.

## Non-Goals

This version does not:

- add the dedicated Charles Schwab importer;
- redesign the journal UI;
- normalize the executions schema;
- migrate the app to PostgreSQL;
- split the large FastAPI or CSV-parser modules beyond focused deployment/auth boundaries;
- add multi-user tenancy;
- archive raw broker CSV files permanently;
- make the backend always-on on the free Render tier.

Those are separate follow-up changes.

## Architecture

### Production

```text
GitHub repository
    |
    +-- GitHub Actions: CI + Pages build/deploy
    |
    +-- GitHub Pages: React SPA
           |
           | Supabase email/password session
           | Authorization: Bearer <access token>
           v
       Render Free: FastAPI
           |
           +-- Supabase JWT verification + exact allowed-user check
           +-- Turso Cloud: trading database
           +-- Supabase Storage: private diary images
           +-- Anthropic / Alpaca: server-side integrations
```

### Local development

```text
React localhost
    |
    v
FastAPI localhost
    |
    +-- sqlite3 -> trading_journal.db
    +-- local uploads directory
    +-- optional Anthropic / Alpaca keys
```

Local behavior is selected explicitly through environment configuration. Production must not silently fall back to local SQLite or unauthenticated mode.

## Authentication

### Frontend

Use `@supabase/supabase-js` with only the public Supabase project URL and publishable/anon key. These values are expected to be visible in the browser and are not server secrets.

Create an authentication provider responsible for:

- restoring the existing Supabase session;
- email/password sign-in;
- sign-out;
- exposing the access token to the API layer;
- rendering the journal only after a valid session exists.

Do not expose a signup screen in the application.

### Backend

Create a focused auth module instead of placing verification logic throughout `main.py`.

Production API requests under `/api/*` must require a bearer token. The verifier must:

1. reject a missing bearer token;
2. verify the Supabase JWT cryptographically using the project's published signing keys / supported Supabase verification path;
3. validate expiration and issuer;
4. read the `sub` claim;
5. require `sub == ALLOWED_USER_ID`;
6. return 401 for invalid/missing authentication and 403 for an authenticated but unauthorized user.

The health endpoint remains unauthenticated so Render can probe the service.

Local mode may disable auth only with an explicit setting such as `AUTH_MODE=disabled`. Production configuration uses `AUTH_MODE=supabase`.

CORS must allow localhost in development and the exact GitHub Pages origin in production. Do not use wildcard origins with credentials.

## Database

### Local driver

Keep Python's built-in `sqlite3` and `DATABASE_PATH` behavior for local development and existing tests.

### Production driver

Use Turso's remote DB-API driver for a stateless web server. The implementation should preserve the current call style used throughout the application:

- `conn.execute(...)`;
- cursors with `fetchone()` / `fetchall()`;
- `commit()`;
- `close()`;
- row access by both index and column name where the existing application relies on it.

Introduce a narrow compatibility boundary in `backend/database.py` rather than rewriting every route.

Production selection is explicit, for example:

```text
DATABASE_MODE=sqlite
DATABASE_PATH=trading_journal.db
```

or:

```text
DATABASE_MODE=turso
TURSO_DATABASE_URL=...
TURSO_AUTH_TOKEN=...
```

When `DATABASE_MODE=turso`, missing Turso credentials are a startup/configuration error. Do not fall back to a local file.

SQLite-only pragmas must be applied only when supported/appropriate.

### Migrations

Replace the current blanket `except Exception: pass` migration pattern with explicit schema migration tracking.

Add a `schema_migrations` table containing applied migration identifiers. Each migration is idempotent and executed once. Failures must propagate so production cannot silently start with a partially upgraded schema.

Initial migration tracking must preserve compatibility with an existing local database whose columns may already exist. Baseline/adoption logic may inspect existing columns before marking the corresponding migration applied.

## File Storage

### Local

Retain the existing local `uploads` directory for development.

### Production

Use a private Supabase Storage bucket for diary images. Storage access goes through the backend; the browser should not receive a Supabase service-role credential.

Introduce a storage abstraction with local and Supabase implementations.

The diary database record stores an opaque storage key rather than relying on a Render filesystem path. Reading an image should use an authenticated backend endpoint or a short-lived signed URL generated by the backend.

Temporary broker CSV uploads are not durable assets. Parse them without permanently archiving them in production unless a later feature explicitly adds an import archive.

## API Client

Keep the existing `REACT_APP_API_URL` behavior.

Add an Axios request interceptor that retrieves the current Supabase access token and sends:

```text
Authorization: Bearer <token>
```

Production builds require `REACT_APP_API_URL`, `REACT_APP_SUPABASE_URL`, and `REACT_APP_SUPABASE_ANON_KEY`.

Local development with auth disabled must remain possible without Supabase frontend values.

Handle 401 by returning the user to the login state rather than letting screens fail independently.

## GitHub Pages

Add a dedicated deployment workflow that:

1. runs from `main` after code is merged;
2. installs frontend dependencies with `npm ci`;
3. builds with the Pages project path `/Trading-Journal-AI/`;
4. uses the configured public API/Supabase build variables;
5. uploads the build artifact using GitHub Pages' supported deployment actions;
6. deploys through the `github-pages` environment.

The React app currently uses state-driven navigation instead of path routing, so no SPA route-rewrite layer is required for internal page changes.

No server secret may be stored in a `REACT_APP_*` variable.

## Render

Add a `render.yaml` Blueprint for one free Python web service.

The service:

- builds from `backend/requirements.txt`;
- starts Uvicorn on `0.0.0.0:$PORT`;
- uses `backend` as the runtime root or an equivalent explicit command;
- receives all secret values through Render environment configuration;
- has no persistent disk dependency;
- exposes the unauthenticated health endpoint.

Render's local filesystem is treated as disposable.

## Configuration

Update `.env.example` to clearly separate public frontend variables from backend secrets.

Backend production variables include at minimum:

- `AUTH_MODE=supabase`
- `SUPABASE_URL`
- `SUPABASE_ANON_KEY` or publishable key only if required for server verification
- `ALLOWED_USER_ID`
- `DATABASE_MODE=turso`
- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`
- Supabase Storage server credential if the chosen storage API requires one
- `SUPABASE_STORAGE_BUCKET`
- `FRONTEND_ORIGINS=https://load66.github.io`
- existing `ANTHROPIC_API_KEY` / Alpaca variables when those features are enabled.

Frontend public variables include:

- `REACT_APP_API_URL`
- `REACT_APP_SUPABASE_URL`
- `REACT_APP_SUPABASE_ANON_KEY`
- `PUBLIC_URL=/Trading-Journal-AI` or equivalent build-time Pages base configuration.

## Error Handling and Security Hardening

The existing global exception handler currently returns the exception string and Python type. In production, return a generic 500 response and log the detailed exception server-side. Development may retain more detail through an explicit mode.

Uploads must be validated for allowed content/type and size where the current endpoints accept user files.

The backend must never trust a user ID supplied by the frontend for authorization. Identity comes only from the verified token.

Supabase service-role credentials, Turso tokens, Anthropic keys, and Alpaca secrets must never be included in frontend code, Pages configuration, test fixtures, or committed files.

## Tests

Backend tests must cover:

- auth disabled local mode;
- missing bearer token in Supabase mode;
- malformed/invalid token;
- valid token for the configured user;
- valid token for a different user;
- health endpoint without auth;
- database configuration validation;
- SQLite compatibility remains intact;
- Turso connection adapter behavior using fakes/mocks rather than real credentials;
- migration tracking and migration failure propagation;
- storage backend selection and private storage behavior through mocks;
- production exception sanitization.

Frontend tests must cover:

- unauthenticated login gate;
- successful authenticated shell rendering;
- access-token attachment to API requests;
- 401 session handling/sign-out behavior;
- local auth-disabled behavior.

CI must continue to run backend tests plus frontend build/tests.

## Deployment and Rollout

1. Build all code on `feature/web-deployment-v1`.
2. Keep production deployment off until CI is green.
3. Open a PR to `main`.
4. Configure Supabase:
   - create project;
   - create the owner's email/password user;
   - disable public signups;
   - create private diary bucket.
5. Configure Turso database and token.
6. Configure Render using `render.yaml` and secret environment variables.
7. Configure GitHub repository Pages plus public build variables/secrets required by the workflow.
8. Merge only after verification.
9. Validate login, API auth, database persistence, image persistence, imports, dashboard, reports, diary, and AI connectivity from both desktop and mobile.
10. Preserve localhost as a supported development mode.

External service creation/credential entry is intentionally separated from source code because these actions require the owner's authenticated provider accounts.

## Upgrade Path

When the project proves useful, moving from free Render to an always-on paid Render service should require no architecture rewrite. The API remains stateless and the database/storage remain external.

If future scale or feature requirements justify PostgreSQL, migrate through the database boundary rather than rewriting route/business logic first.
