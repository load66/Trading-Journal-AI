# Web Deployment v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Trading Journal AI deployable as a private, single-user, free-first web app while preserving existing localhost behavior.

**Architecture:** GitHub Pages hosts the React frontend, Render Free hosts stateless FastAPI, Supabase Auth protects the app and private diary storage, and Turso persists SQLite-compatible trading data. Local development remains React + FastAPI + local SQLite/uploads.

**Tech Stack:** React 19, Axios, Supabase JS, FastAPI, PyJWT/cryptography, SQLite, turso_serverless, Supabase Storage REST, GitHub Actions, Render Blueprint.

**Spec:** `docs/superpowers/specs/2026-09-26-web-deployment-v1-design.md`

## Global Constraints

- Preserve current localhost behavior when hosted-mode environment variables are absent.
- Hosted API must fail closed when authentication configuration is incomplete.
- No service-role key, database token, Anthropic key, Alpaca secret, or password may enter the frontend bundle or repository.
- The public health endpoint remains unauthenticated; journal/API/file data is protected.
- Existing broker-import reconstruction behavior must not change.
- No Schwab importer, PostgreSQL migration, major UI redesign, or unrelated monolith refactor in this branch.
- Use TDD for behavior changes and run existing backend and frontend suites before integration.

## Review Focus

- Expired/invalid/wrong-user Supabase JWTs must never reach journal endpoints.
- Hosted configuration must never silently fall back to local SQLite or local persistent uploads.
- Local mode must continue working without Supabase/Turso credentials.
- Diary upload failures must not leave database rows pointing to missing remote objects.
- GitHub Pages production build must use the repository subpath and must not publish if required public variables are absent.

---

### Task 1: Backend authentication boundary and production-safe errors

**Files:**
- Create: `backend/auth.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/main.py`
- Modify: `backend/requirements.txt`
- Modify: `.env.example`

**Interfaces:**
- Produces: `auth_required() -> bool`, `verify_access_token(token: str) -> dict`, and FastAPI middleware/dependency behavior protecting journal routes.
- Consumes: `SUPABASE_URL`, `AUTHORIZED_USER_ID`, `AUTH_REQUIRED`, optional `APP_ENV`.

- [ ] **Step 1: Write failing auth tests**
  - local mode permits an API request without a bearer token;
  - hosted auth mode returns 401 when missing/malformed;
  - valid claims for wrong `sub` return 403;
  - valid authorized claims pass;
  - root health route remains public;
  - hosted 500 payload is sanitized.

- [ ] **Step 2: Run auth tests and confirm RED**
  - Run: `cd backend && pytest tests/test_auth.py -v`
  - Expected: failures because auth module/middleware does not exist.

- [ ] **Step 3: Implement auth module and route boundary**
  - Use PyJWT remote JWKS verification for asymmetric Supabase JWTs.
  - Validate issuer `<SUPABASE_URL>/auth/v1`, audience `authenticated`, expiration, and exact `AUTHORIZED_USER_ID`.
  - Protect `/api/*` and diary-file serving endpoints, not `/`.
  - Preserve unauthenticated localhost mode unless `AUTH_REQUIRED=true`.
  - Sanitize generic 500 responses when `APP_ENV=production`.

- [ ] **Step 4: Run auth tests and confirm GREEN**
  - Run: `cd backend && pytest tests/test_auth.py -v`
  - Expected: all pass.

- [ ] **Step 5: Run existing backend suite**
  - Run: `cd backend && pytest tests -q`
  - Expected: 0 failures.

### Task 2: Database adapter and explicit migrations

**Files:**
- Modify: `backend/database.py`
- Create: `backend/tests/test_database_modes.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: existing `get_db()`, `init_db()`, `row_to_dict()` callers.
- Produces: SQLite-compatible `get_db()` selected by `DATABASE_MODE`; ordered schema migration runner.

- [ ] **Step 1: Write failing database-mode tests**
  - default mode opens SQLite at `DATABASE_PATH`;
  - `DATABASE_MODE=turso` requires URL/token and calls the Turso connection factory;
  - missing Turso credentials raise rather than falling back locally;
  - result rows support existing name-based access and `dict(row)` conversion;
  - migrations are recorded and re-running is idempotent;
  - a migration error propagates.

- [ ] **Step 2: Run targeted tests and confirm RED**
  - Run: `cd backend && pytest tests/test_database_modes.py -v`

- [ ] **Step 3: Implement database adapter**
  - Keep `sqlite3` default.
  - Add `turso_serverless` hosted mode.
  - Normalize row behavior to the mapping interface expected throughout the codebase.

- [ ] **Step 4: Replace silent migrations**
  - Add `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`.
  - Convert current ALTER operations into ordered migrations.
  - Fresh schema and upgraded schema must converge to the same column set.

- [ ] **Step 5: Run database tests and backend suite**
  - Run: `cd backend && pytest tests/test_database_modes.py -v && pytest tests -q`
  - Expected: 0 failures.

### Task 3: Storage abstraction and protected diary files

**Files:**
- Create: `backend/storage.py`
- Create: `backend/tests/test_storage.py`
- Modify: `backend/main.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `save_file(name: str, data: bytes, content_type: str | None) -> str`, `read_file(name: str) -> tuple[bytes, str]`, `delete_file(name: str) -> None`.
- Consumes: `STORAGE_MODE`, `UPLOAD_DIR`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`.

- [ ] **Step 1: Write failing storage tests**
  - local mode writes/reads/deletes under `UPLOAD_DIR`;
  - Supabase mode uses mocked authenticated Storage REST calls;
  - hosted mode missing storage credentials fails closed;
  - path traversal names are rejected/normalized;
  - remote upload failure prevents diary DB insertion.

- [ ] **Step 2: Run targeted tests and confirm RED**
  - Run: `cd backend && pytest tests/test_storage.py -v`

- [ ] **Step 3: Implement storage abstraction**
  - Local adapter uses filesystem.
  - Supabase adapter uses private bucket with backend service-role authorization.
  - Do not expose the bucket publicly.

- [ ] **Step 4: Refactor diary upload/serve/delete**
  - Keep HEIC conversion.
  - Use a temporary file only while Anthropic image analysis needs a file path.
  - Persist through the storage adapter before inserting the diary row.
  - Add authenticated `GET /api/diary/{entry_id}/file`.
  - Delete stored file when a diary entry is deleted.
  - Frontend will use the protected API route instead of `/uploads/`.

- [ ] **Step 5: Run storage tests and backend suite**
  - Run: `cd backend && pytest tests/test_storage.py -v && pytest tests -q`
  - Expected: 0 failures.

### Task 4: Frontend authentication gate and bearer-token API client

**Files:**
- Create: `frontend/src/auth.js`
- Create: `frontend/src/components/Login.js`
- Create: `frontend/src/auth.test.js`
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/App.js`
- Modify: `frontend/src/components/AppHeader.js`
- Modify: `frontend/src/components/Diary.js`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Produces: `AUTH_REQUIRED`, Supabase client/session helpers, `AuthGate` behavior, authenticated Axios requests.
- Consumes: `REACT_APP_AUTH_REQUIRED`, `REACT_APP_SUPABASE_URL`, `REACT_APP_SUPABASE_PUBLISHABLE_KEY`, `REACT_APP_API_URL`.

- [ ] **Step 1: Write failing frontend tests**
  - auth-disabled mode renders app without Supabase config;
  - auth-required/no-session renders login only;
  - successful email/password login renders journal;
  - sign out returns to login;
  - Axios requests include current bearer token;
  - API 401 triggers session sign-out.

- [ ] **Step 2: Run targeted frontend tests and confirm RED**
  - Run: `cd frontend && npx craco test --watchAll=false auth.test.js`

- [ ] **Step 3: Implement auth client and login gate**
  - Add `@supabase/supabase-js`.
  - No signup UI.
  - Preserve local bypass when auth is disabled.
  - Add accessible login states/errors and explicit sign-out.

- [ ] **Step 4: Protect diary image retrieval**
  - Replace direct `/uploads/<path>` URLs with authenticated fetch/blob URLs from `/api/diary/{id}/file`.
  - Revoke object URLs when cards unmount/change.

- [ ] **Step 5: Run frontend tests/build**
  - Run: `cd frontend && npx craco test --watchAll=false && npm run build`
  - Expected: tests pass and build exits 0.

### Task 5: GitHub Pages and Render deployment configuration

**Files:**
- Create: `.github/workflows/deploy-pages.yml`
- Create: `render.yaml`
- Create: `scripts/validate-frontend-env.js`
- Modify: `frontend/package.json`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Consumes GitHub repository variables: `REACT_APP_API_URL`, `REACT_APP_SUPABASE_URL`, `REACT_APP_SUPABASE_PUBLISHABLE_KEY`.
- Produces deterministic Pages build/deploy and Render Blueprint metadata.

- [ ] **Step 1: Add a failing environment-validation test/command**
  - Running validator without required hosted variables exits non-zero.
  - Running with all required values exits zero.

- [ ] **Step 2: Implement Pages workflow**
  - Trigger on `main` push and workflow dispatch.
  - Least-privilege `contents: read`, `pages: write`, `id-token: write`.
  - Build from `frontend/`.
  - Set `REACT_APP_AUTH_REQUIRED=true`.
  - Validate public variables before build.
  - Deploy official Pages artifact.

- [ ] **Step 3: Implement Render Blueprint**
  - Python web service, free plan, `backend` root.
  - Build from requirements.
  - Start with `uvicorn main:app --host 0.0.0.0 --port $PORT`.
  - Set safe non-secret defaults only.

- [ ] **Step 4: Verify workflow/config syntax and frontend build**
  - Run local validator both fail/pass paths.
  - Run: `cd frontend && npm run build`
  - Expected: build exit 0 with hosted variables populated.

### Task 6: Deployment runbook and whole-branch verification

**Files:**
- Create: `docs/DEPLOYMENT.md`
- Modify: `README.md`

**Interfaces:**
- Produces the exact one-time external provisioning checklist for Supabase, Turso, Render, and GitHub Pages.
- Consumes all environment-variable names created by Tasks 1-5.

- [ ] **Step 1: Document Supabase provisioning**
  - Create project/user.
  - Disable public signup.
  - Ensure asymmetric signing keys.
  - Create private `diary` bucket.
  - Record project URL, publishable key, service-role key, and authorized user UUID.

- [ ] **Step 2: Document Turso provisioning**
  - Create Turso database and auth token.
  - Record URL/token.
  - Include migration/startup verification.

- [ ] **Step 3: Document Render provisioning**
  - Connect the repo/Blueprint.
  - Enter secrets and production origins.
  - Verify public health then authenticated API.

- [ ] **Step 4: Document GitHub Pages provisioning**
  - Set Pages source to GitHub Actions.
  - Add the three public repository variables.
  - Run/deploy workflow and verify project URL.

- [ ] **Step 5: Whole-branch verification**
  - Run: `cd backend && pytest tests -q`
  - Run: `cd frontend && npx craco test --watchAll=false`
  - Run: `cd frontend && npm run build`
  - Inspect branch diff against `main` for secrets and unrelated changes.
  - Expected: all commands exit 0; no secret values committed.
