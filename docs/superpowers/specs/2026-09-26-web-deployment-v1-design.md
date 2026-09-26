# Web Deployment v1 Design

**Date:** 2026-09-26  
**Repository:** `load66/Trading-Journal-AI`  
**Branch:** `feature/web-deployment-v1`

## Intent

Turn the existing local-first Trading Journal AI into a private, single-user web application that can be opened from phone or desktop while preserving the existing localhost workflow.

The first deployment must be free-first, avoid unnecessary rewrites, protect trading data and API credentials, and leave a low-friction upgrade path to paid always-on infrastructure later.

## Success criteria

1. The React frontend can be deployed to `https://load66.github.io/Trading-Journal-AI/`.
2. The hosted frontend requires email/password authentication before rendering journal content.
3. Public signup is not exposed by the application; the deployment is intended for one pre-created Supabase user.
4. Every protected FastAPI route rejects missing, expired, invalid, wrong-project, or wrong-user access tokens.
5. The backend remains usable locally without authentication unless local auth is explicitly enabled.
6. Hosted trading data survives backend restarts and free-tier spin-downs.
7. Diary images survive backend restarts and remain private.
8. Anthropic, Alpaca, Turso, and storage secrets never enter the browser bundle or repository.
9. Existing broker import, trade reconstruction, reports, diary analysis, and localhost tests remain intact.
10. The cloud database/storage layer can later move to paid infrastructure without redesigning the application.

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
                    Turso database   Supabase private storage
                    trading data     diary files
                              \
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

Local development remains the default when cloud environment variables are absent.

## Technology decisions

### Frontend: GitHub Pages

GitHub Pages hosts only the static React build. It never receives server-side secrets. The React build uses a project base path for `/Trading-Journal-AI/` and receives public configuration through GitHub repository variables:

- `REACT_APP_API_URL`
- `REACT_APP_AUTH_REQUIRED=true`
- `REACT_APP_SUPABASE_URL`
- `REACT_APP_SUPABASE_PUBLISHABLE_KEY`

The Supabase publishable/anon key is intentionally browser-visible; service-role keys are never used client-side.

### Authentication: Supabase Auth

Use email/password sign-in only. The application contains no signup UI.

The React app obtains a Supabase access token and attaches it to API requests. The backend verifies the JWT against the Supabase project JWKS with:

- valid cryptographic signature;
- issuer `<SUPABASE_URL>/auth/v1`;
- audience `authenticated`;
- non-expired token;
- subject exactly matching `AUTHORIZED_USER_ID`.

This second subject check makes the API single-user even if another account exists in the Supabase project.

Hosted mode fails closed if required auth configuration is missing. Local mode remains unauthenticated unless `AUTH_REQUIRED=true`.

### Database: SQLite locally, Turso remotely

The existing SQLite SQL and schema are preserved.

A database adapter chooses the backend from configuration:

- default/local: Python `sqlite3`;
- hosted: remote Turso DB-API connection using `turso_serverless`.

Hosted mode must not depend on a writable local SQLite file on Render.

Required hosted variables:

- `DATABASE_MODE=turso`
- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`

The rest of the application continues to consume a DB-API style connection through `get_db()`.

### Database migrations

Replace catch-all, silent `ALTER TABLE` exception handling with an explicit `schema_migrations` table and ordered migration functions.

Requirements:

- migrations are idempotent;
- already-applied versions are skipped;
- a real migration error aborts startup rather than being swallowed;
- a fresh database creates the complete current schema and records migration versions;
- existing SQLite databases migrate without losing data.

### Diary file storage

Introduce a storage abstraction:

- local mode: current `uploads/` filesystem behavior;
- hosted mode: private Supabase Storage bucket.

Required hosted variables:

- `STORAGE_MODE=supabase`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET=diary`

The service-role key is backend-only.

Diary uploads are read into memory, HEIC conversion remains supported, and AI image analysis uses a temporary local file for the duration of the request. Persistent hosted copies go to the private bucket.

The API exposes an authenticated diary-file endpoint rather than making the bucket public. This endpoint returns file bytes only after normal API authentication.

Deleting diary entries also deletes the associated stored object/file where practical.

### Render backend

Render hosts the existing FastAPI service on free compute.

The repository includes a `render.yaml` Blueprint that:

- uses `backend/` as the runtime root;
- installs `backend/requirements.txt`;
- starts Uvicorn on Render's `$PORT`;
- configures only non-secret defaults in source;
- leaves secret values for the Render environment.

The backend is stateless in hosted mode. Free-tier sleep/cold start is accepted for v1.

### GitHub Pages workflow

A dedicated workflow builds and deploys `frontend/` to GitHub Pages on pushes to `main` and manual dispatch.

The workflow:

- uses least-privilege Pages permissions;
- installs with `npm ci`;
- validates required hosted public variables;
- runs the production build;
- uploads the build artifact;
- deploys with the official Pages action.

Normal CI remains separate and continues to run backend tests plus frontend build/tests.

## API security boundary

Authentication is applied to API and diary-file routes while health checks remain public for hosting diagnostics.

CORS:

- localhost remains allowed for development;
- hosted origin is explicitly supplied through `FRONTEND_ORIGINS`;
- wildcard origins are not used;
- Authorization headers are allowed.

Generic 500 responses are sanitized in hosted mode so exception classes/messages are not leaked to the browser. Detailed exceptions remain visible in server logs.

## Frontend authentication behavior

When hosted auth is required:

1. Load the existing Supabase session.
2. If no session exists, show the login screen only.
3. Sign in with email/password.
4. Render the journal after a valid session exists.
5. Axios retrieves the current access token immediately before protected requests.
6. A 401 response signs the user out and returns to the login screen.
7. Provide an explicit Sign out action in the authenticated UI.

When `REACT_APP_AUTH_REQUIRED` is not true, the frontend behaves exactly like the current local application.

## Secrets and public configuration

Never commit:

- Supabase service-role key;
- Turso auth token;
- Anthropic key;
- Alpaca secrets;
- passwords;
- database snapshots containing trading data.

Public configuration that may appear in the frontend build:

- hosted API URL;
- Supabase project URL;
- Supabase publishable/anon key.

Update `.env.example` with placeholders and comments that distinguish public browser values from backend secrets.

## Failure behavior

- Missing hosted auth configuration: backend startup fails closed with a clear server-side error.
- Invalid/missing bearer token: HTTP 401.
- Valid token for another user: HTTP 403.
- Turso unavailable: request fails; no fallback to ephemeral local data in hosted mode.
- Supabase storage unavailable during a diary upload: do not write a database row that points to a nonexistent object.
- AI analysis failure after a successful storage/database write: retain the diary entry and return `analysis_error`, matching current behavior.
- GitHub Pages build missing required variables: deployment workflow fails instead of publishing a broken login shell.

## Testing strategy

Backend tests cover authentication, database mode selection, migration behavior, storage behavior through mocks, public health, and sanitized hosted errors. Frontend tests cover the login gate, local bypass, login/logout, bearer-token attachment, and 401 sign-out. Existing suites remain mandatory.

## Deployment/provisioning boundary

Repository code can be completed automatically. External account-level provisioning still requires credentials that do not exist in GitHub:

1. Supabase project: create the single user, disable public signup, create private `diary` bucket, collect URL/publishable key/service-role key/user UUID.
2. Turso database: create database/token and collect URL/token.
3. Render service: connect repository/Blueprint and set secrets/environment variables.
4. GitHub Pages: configure the repository to use GitHub Actions if not already enabled and add required public repository variables.

The repository includes an exact deployment runbook for those one-time actions.

## Deferred work

Not part of this deployment branch:

- Schwab CSV adapter;
- PostgreSQL migration;
- major UI redesign;
- multi-user tenancy/RBAC;
- background jobs;
- paid always-on hosting;
- normalized executions table;
- broad backend module refactor.
