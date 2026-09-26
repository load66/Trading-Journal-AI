# Trading Journal Web Deployment Design

**Date:** 2026-09-26  
**Repository:** `load66/Trading-Journal-AI`  
**Branch:** `feature/web-deployment-foundation`

## 1. Goal

Turn the existing local-first React + FastAPI trading journal into a private, single-user web application that can be opened from `https://load66.github.io/Trading-Journal-AI/` while preserving the existing localhost workflow and avoiding a premature rewrite of the trading/import logic.

The first web release must remain free-first, secure by default in production, and structured so moving to paid always-on hosting later does not require an application rewrite.

## 2. Success Criteria

- GitHub Pages serves the React frontend under the repository path `/Trading-Journal-AI/`.
- Local development continues to use `http://localhost:3010` + `http://localhost:8010` without requiring cloud credentials.
- The hosted frontend requires email/password login before rendering trading data.
- Public signup is not implemented by the frontend; the production Supabase project is configured with new-user signup disabled after the owner's account is created.
- Every production `/api/*` request requires a valid Supabase user access token.
- The backend additionally restricts access to one configured Supabase user ID.
- Trading data persists in Turso rather than Render's ephemeral filesystem.
- Diary screenshots/files persist in a private Supabase Storage bucket rather than Render's filesystem.
- Raw broker CSV imports remain transient request inputs; the app persists reconstructed trade data, not uploaded broker files.
- Anthropic, Alpaca, Turso, and Supabase secret credentials never enter the React bundle or repository.
- Render can be replaced later by a paid Render instance or another Python host without changing the public frontend contract.
- Existing backend and frontend tests continue to pass, with new regression tests for auth, database selection/migrations, and storage/deployment behavior.

## 3. Architecture

```text
GitHub repository
      |
      +--> GitHub Actions --> React build --> GitHub Pages
      |                                      |
      |                                      | HTTPS + Bearer JWT
      |                                      v
      +---------------------------------> Render Free / FastAPI
                                             |
                      +----------------------+----------------------+
                      |                      |                      |
                      v                      v                      v
                  Turso DB          Supabase Auth/Storage      Anthropic/Alpaca
```

### 3.1 Frontend

The React application remains a static SPA. It uses runtime/build configuration for:

- `REACT_APP_API_URL`
- `REACT_APP_AUTH_ENABLED`
- `REACT_APP_SUPABASE_URL`
- `REACT_APP_SUPABASE_PUBLISHABLE_KEY`

No secret key is exposed to the browser.

The frontend implements a small Supabase-compatible auth client using the documented Auth HTTP endpoints instead of adding a new npm dependency. It stores the session locally, refreshes the access token before expiry, and attaches `Authorization: Bearer <token>` to API requests. Local development automatically bypasses the login gate when auth is disabled.

### 3.2 Backend authentication

Production uses `AUTH_MODE=supabase`. Local development defaults to `AUTH_MODE=disabled`.

A dedicated auth module protects every `/api/*` request while allowing CORS preflight and the public health endpoint. It verifies the Supabase access token using the project's JWKS endpoint, validates issuer/audience/expiry, then requires:

```text
claims["sub"] == AUTHORIZED_USER_ID
```

Production startup fails closed if auth is disabled or required auth configuration is missing.

### 3.3 Database

Local mode remains standard Python `sqlite3` against `DATABASE_PATH`.

Production uses:

- `DATABASE_MODE=turso`
- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`

The backend uses the current `turso_serverless` DB-API driver for direct HTTP access from stateless Render.

A narrow compatibility adapter preserves the row behavior the application already relies on: indexed access, named-column access, `.keys()`, `dict(row)`, `fetchone()`, `fetchall()`, `lastrowid`, commits, rollbacks, and close.

### 3.4 Schema migrations

The current pattern of swallowing every migration exception is replaced.

A `schema_migrations` table records applied migration IDs. Column migrations inspect existing table metadata before applying `ALTER TABLE`, allowing existing local databases to be adopted safely. Unexpected migration failures surface during startup instead of leaving a partially migrated database silently running.

### 3.5 Diary storage

Storage is selected independently from the database:

- local: `STORAGE_MODE=local` with `UPLOAD_DIR`
- hosted: `STORAGE_MODE=supabase`

Production uses:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `SUPABASE_STORAGE_BUCKET=trading-journal-diary`

The bucket is private.

For diary uploads, the backend:
1. validates and reads the upload;
2. converts HEIC/HEIF to JPEG when needed;
3. keeps a temporary local file only long enough for AI image analysis;
4. uploads the final bytes to private Supabase Storage;
5. stores only the storage object path in `diary_entries.image_path`;
6. returns short-lived signed image URLs when diary entries are listed.

Local mode preserves the existing `/uploads` behavior.

Deleting diary entries also deletes their persisted storage objects on a best-effort basis without allowing a storage cleanup failure to corrupt relational data deletion.

### 3.6 Error handling

Production 500 responses are sanitized. Internal exception type/message details are logged server-side but are not returned to unauthenticated/public clients. Existing explicit 4xx validation messages remain useful.

### 3.7 CORS

Production allows only the configured frontend origin:

`https://load66.github.io`

The localhost regex remains supported for local development. Credentials are not used for browser API authentication; Bearer tokens are used instead.

## 4. Deployment

### 4.1 GitHub Pages

A dedicated workflow builds `frontend/` with:

- `PUBLIC_URL=/Trading-Journal-AI`
- configured API/Auth values from GitHub repository variables

It deploys `frontend/build` through the official Pages artifact/deploy actions.

The workflow supports `workflow_dispatch`. Automatic deployment occurs from `main` only when required repository variables are configured.

### 4.2 Render

A root `render.yaml` defines one free Python web service:

- root directory: `backend`
- build: `pip install -r requirements.txt`
- start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- public health check: `/`
- `APP_ENV=production`
- `AUTH_MODE=supabase`
- `DATABASE_MODE=turso`
- `STORAGE_MODE=supabase`
- `FRONTEND_ORIGINS=https://load66.github.io`

Secrets are declared with `sync: false` and are entered during initial Render Blueprint setup rather than committed.

### 4.3 External setup boundary

Code can prepare the deployment completely, but the following require account-level credentials/consent outside GitHub:

- create/connect a Render service;
- create a Supabase project, owner user, and private bucket;
- disable new-user signup after owner creation;
- create a Turso database and token;
- enter secrets into Render;
- add public frontend configuration values to GitHub repository variables;
- select GitHub Actions as the Pages publishing source if not already enabled.

These are explicit provisioning steps, not application-code gaps.

## 5. Environment Contract

### Backend local defaults

```text
APP_ENV=local
AUTH_MODE=disabled
DATABASE_MODE=sqlite
DATABASE_PATH=trading_journal.db
STORAGE_MODE=local
UPLOAD_DIR=uploads
```

### Backend production required

```text
APP_ENV=production
AUTH_MODE=supabase
AUTHORIZED_USER_ID=<supabase user uuid>
SUPABASE_URL=<project url>
SUPABASE_SECRET_KEY=<server secret key>
SUPABASE_STORAGE_BUCKET=trading-journal-diary
DATABASE_MODE=turso
TURSO_DATABASE_URL=<remote database url>
TURSO_AUTH_TOKEN=<database token>
STORAGE_MODE=supabase
FRONTEND_ORIGINS=https://load66.github.io
```

Optional existing server-side secrets remain:

```text
ANTHROPIC_API_KEY=
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
ALPACA_DATA_FEED=iex
```

### Frontend production

```text
REACT_APP_AUTH_ENABLED=true
REACT_APP_API_URL=https://<render-service>.onrender.com
REACT_APP_SUPABASE_URL=<project url>
REACT_APP_SUPABASE_PUBLISHABLE_KEY=<public browser key>
PUBLIC_URL=/Trading-Journal-AI
```

## 6. Security Invariants

- Production never starts with `AUTH_MODE=disabled`.
- Production never falls back from Turso to a local SQLite file after a configuration error.
- Production never falls back from Supabase storage to local durable storage after a configuration error.
- Only the configured Supabase user ID is authorized.
- Public signup UI is absent.
- Secret/server keys are never prefixed with `REACT_APP_`.
- Raw CSV data is not written to GitHub or retained on Render.
- Private diary files are accessed through time-limited signed URLs.
- A malformed/expired/wrong-user token receives 401/403 before route logic executes.

## 7. Compatibility / Non-Goals

This phase does not:

- redesign the journal UI;
- add the Schwab broker adapter;
- normalize the executions JSON schema;
- migrate to PostgreSQL;
- split the large FastAPI router into a full service architecture;
- change trading calculations or broker reconstruction rules.

Those remain separate follow-up projects after the hosted foundation is stable.

## 8. Verification

Backend tests must cover:

- default local SQLite mode;
- production config fails closed when auth/database/storage settings are missing;
- Turso connection selection and row compatibility through a fake driver;
- migration adoption of an existing SQLite database;
- valid owner token vs missing/invalid/wrong-user token;
- local vs Supabase storage selection;
- signed diary URL decoration.

Frontend tests must cover:

- auth-disabled local mode renders the app;
- auth-enabled mode shows login when no session exists;
- successful login persists a session and unlocks the app;
- API client attaches the access token;
- token refresh path is used before expiry;
- logout clears the session.

Deployment verification must include:

- existing backend pytest suite;
- existing frontend test suite;
- frontend production build;
- GitHub Pages workflow syntax/build;
- Render Blueprint structure;
- no committed secret values.

## 9. Rollout

1. Merge code only after CI is green.
2. Provision Supabase, Turso, and Render.
3. Create the single owner user and private diary bucket.
4. Populate Render secrets and deploy the backend.
5. Confirm `/` health and authenticated API behavior.
6. Set GitHub Pages repository variables and run the Pages workflow.
7. Verify login and journal access from a phone and a desktop.
8. Import a small known CSV and confirm persistence across Render spin-down/redeploy.
9. Upload a diary image and confirm private signed-image rendering.
10. Only then begin the Schwab importer project.
