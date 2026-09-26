# Private Web Deployment v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the existing journal as a private, free-first web application without breaking localhost development.

**Architecture:** GitHub Pages hosts the React build; FastAPI remains the server-side API; Supabase provides email/password identity; production uses an explicit remote database adapter while local development remains SQLite. Hosted durable files are abstracted away from Render's ephemeral filesystem.

**Tech Stack:** React 19, Axios, FastAPI, SQLite-compatible DB API, Supabase Auth, GitHub Actions, Render-compatible Python deployment.

**Spec:** `docs/superpowers/specs/2026-09-26-private-web-deployment-v1.md`

## Global Constraints

- Preserve localhost behavior without requiring cloud credentials.
- Production is private and single-user.
- No privileged secret enters the frontend bundle or repository.
- Do not mix Schwab importer work into this branch.
- Unexpected production errors are sanitized.
- Hosted durable user data must not rely on Render's local filesystem.
- Changes are test-first for executable behavior.

## Review Focus

- Auth disabled locally must not accidentally require a token.
- Auth enabled with incomplete configuration must fail closed.
- A valid token for the wrong Supabase user must receive 403.
- Existing Library routes must be protected along with routes declared directly on the main app.
- GitHub Pages project-path builds must not break asset loading or localhost development.

---

### Task 1: Authentication boundary

**Files:**
- Create: `backend/auth.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/main.py`
- Modify: `backend/library.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `require_authenticated_user(...)` FastAPI dependency and startup auth configuration validation.
- Consumes: `AUTH_ENABLED`, `SUPABASE_URL`, `AUTHORIZED_USER_ID`.

- [ ] Write failing tests for local auth bypass, missing bearer token, invalid token, wrong authorized user, and valid authorized user.
- [ ] Run the focused tests and confirm they fail for missing auth behavior.
- [ ] Implement JWT verification and single-user authorization with cached JWKS retrieval and explicit configuration validation.
- [ ] Apply the dependency to all private API routes, including the Library router, while leaving only health endpoints public.
- [ ] Run focused auth tests and the complete backend suite.
- [ ] Commit.

### Task 2: Database connection boundary and migrations

**Files:**
- Create: `backend/db_adapter.py`
- Create: `backend/migrations.py`
- Create: `backend/tests/test_database_config.py`
- Modify: `backend/database.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `connect_database()` returning a DB-API-compatible connection; explicit schema migration runner.
- Consumes: `DATABASE_MODE`, `DATABASE_PATH`, remote database URL/token variables.

- [ ] Write failing tests for default local SQLite, explicit unsupported mode, missing remote configuration, row mapping compatibility, and migration failure visibility.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement the adapter with local SQLite as the default and an explicit remote provider implementation behind lazy imports.
- [ ] Replace silent ALTER exception swallowing with versioned/idempotent migrations that distinguish already-applied schema from real failures.
- [ ] Run focused tests and the complete backend suite.
- [ ] Commit.

### Task 3: Frontend authentication and API token injection

**Files:**
- Create: `frontend/src/auth.js`
- Create: `frontend/src/components/Login.js`
- Create: `frontend/src/components/Login.test.js`
- Create/Modify: frontend API tests
- Modify: `frontend/src/App.js`
- Modify: `frontend/src/api.js`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Produces: authenticated app gate, session lifecycle, Axios bearer-token interceptor.
- Consumes: `REACT_APP_SUPABASE_URL`, `REACT_APP_SUPABASE_ANON_KEY`.

- [ ] Write failing tests proving unauthenticated hosted users see login, authenticated users reach the app, and API requests receive the current access token.
- [ ] Run focused frontend tests and confirm failures.
- [ ] Add the Supabase browser client and login/session/logout integration.
- [ ] Keep local development cloud-auth-free unless explicitly configured.
- [ ] Run focused tests, full frontend tests, and build.
- [ ] Commit.

### Task 4: Private diary storage boundary

**Files:**
- Create: `backend/storage.py`
- Create: `backend/tests/test_storage.py`
- Modify: `backend/main.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: storage service for save/delete/read-or-sign diary image operations.
- Consumes: local upload path or configured private object-storage credentials.

- [ ] Write failing tests for local storage, hosted configuration failure, private object naming, and deletion behavior.
- [ ] Run focused tests and confirm failures.
- [ ] Implement local filesystem storage plus hosted private object-storage adapter.
- [ ] Route diary upload/delete/image retrieval through the storage service.
- [ ] Run focused tests and complete backend suite.
- [ ] Commit.

### Task 5: Production configuration and error hardening

**Files:**
- Create: `backend/config.py`
- Create: `backend/tests/test_production_config.py`
- Modify: `backend/main.py`
- Modify: `.env.example`
- Create: `render.yaml`

**Interfaces:**
- Produces: validated runtime configuration and Render deployment manifest.
- Consumes: auth/database/storage/CORS environment variables from prior tasks.

- [ ] Write failing tests for incomplete production configuration, allowed production CORS origin, public health response, and sanitized unexpected 500 responses.
- [ ] Run focused tests and confirm failures.
- [ ] Implement validation, production-safe exception handling/logging, and dedicated `/health`.
- [ ] Add a Render-compatible stateless service manifest.
- [ ] Run focused tests and complete backend suite.
- [ ] Commit.

### Task 6: GitHub Pages deployment

**Files:**
- Create: `.github/workflows/deploy-pages.yml`
- Modify: `frontend/package.json`
- Modify: `README.md`
- Create: `docs/deployment.md`

**Interfaces:**
- Produces: tested static build artifact and GitHub Pages deployment workflow.
- Consumes: public frontend build variables and backend URL.

- [ ] Add a test/build check that verifies the production homepage/base path is `/Trading-Journal-AI/` while localhost remains unchanged.
- [ ] Run frontend tests/build before workflow changes.
- [ ] Configure CRA project-path deployment and a Pages workflow gated by tests/build.
- [ ] Document all external provisioning steps and exact environment-variable names without secret values.
- [ ] Run full frontend tests/build and inspect generated asset paths.
- [ ] Commit.

### Task 7: CI, security regression, and final verification

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: documentation only if verification exposes a mismatch.

**Interfaces:**
- Consumes all prior task interfaces.
- Produces merge-ready verification evidence.

- [ ] Extend CI to exercise new backend/frontend security and deployment tests.
- [ ] Run the entire backend test suite.
- [ ] Run the entire frontend test suite.
- [ ] Run a production frontend build.
- [ ] Search tracked content for credential patterns and verify no real secrets are committed.
- [ ] Review branch diff against the design spec and fix Critical/Important findings test-first.
- [ ] Open a pull request to `main` with architecture, verification, external provisioning requirements, and deferred work documented.
