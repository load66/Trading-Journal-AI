# Web Deployment v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a secure, private, free-first web deployment path for Trading Journal AI while preserving existing localhost behavior.

**Architecture:** GitHub Pages serves the React frontend; Render Free serves a stateless FastAPI API; Supabase provides single-user email/password authentication and private diary-file storage; Turso Cloud provides the persistent SQLite-compatible production database. Local development continues to use sqlite3 and local uploads through explicit environment modes.

**Tech Stack:** React 19, Axios, Supabase JS, FastAPI, PyJWT/cryptography, sqlite3, turso_serverless, Supabase Storage REST/Python client, GitHub Actions, GitHub Pages, Render Blueprint.

**Spec:** `docs/superpowers/specs/2026-09-26-web-deployment-v1-design.md`

## Global Constraints

- Production is private and single-user.
- Public signup is not exposed by this application.
- `/api/*` requires authentication in `AUTH_MODE=supabase`; `/` health remains public.
- The backend must additionally require `sub == ALLOWED_USER_ID`.
- Local development remains supported with `AUTH_MODE=disabled`, `DATABASE_MODE=sqlite`, and local storage.
- Production database mode is explicit Turso; missing credentials must fail closed.
- Production storage mode is explicit Supabase; Render filesystem is never a durable source of truth.
- Server secrets never appear in `REACT_APP_*` variables or committed files.
- Existing broker parsers/calculations are not refactored in this plan.
- No Charles Schwab importer work is included.
- GitHub Pages project path is `/Trading-Journal-AI/`.
- Production CORS origin is `https://load66.github.io`.
- Error details are sanitized in production.
- Changes are implemented test-first and existing tests must remain green.

## Review Focus

1. Browser CORS preflight (`OPTIONS`) must not be rejected by auth middleware before CORS can handle it; Task 1 tests this explicitly.
2. An authenticated Supabase user other than `ALLOWED_USER_ID` must receive 403, not journal data; Task 1 tests this explicitly.
3. Existing SQLite databases that already contain legacy migration columns must adopt migration tracking without duplicate-column failure; Task 2 tests this explicitly.
4. Private diary images must never fall back to a public Render filesystem URL in production; Task 3 tests storage-mode selection and authenticated image access.
5. A frontend 401 must clear the authenticated session and return to the login gate instead of leaving a partially rendered journal; Task 4 tests this explicitly.

---

### Task 1: Production Configuration and Single-User API Authentication

**Files:**
- Create: `backend/config.py`
- Create: `backend/auth.py`
- Create: `backend/tests/test_auth.py`
- Modify: `backend/main.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Produces: `Settings.from_env() -> Settings`
- Produces: `authenticate_bearer(authorization: str | None, settings: Settings) -> dict`
- Produces: `protect_api_request(request: Request, call_next) -> Response`
- Later tasks consume `Settings` for database/storage/error-mode selection.

- [ ] **Step 1: Write failing auth/config tests**
  - `test_disabled_auth_allows_api_request`
  - `test_supabase_mode_rejects_missing_bearer`
  - `test_supabase_mode_rejects_invalid_token`
  - `test_supabase_mode_allows_configured_subject`
  - `test_supabase_mode_forbids_other_subject`
  - `test_options_preflight_bypasses_auth`
  - `test_health_endpoint_bypasses_auth`
  - `test_supabase_mode_requires_url_and_allowed_user`

- [ ] **Step 2: Run backend tests and verify the new tests fail**
  - Run through CI: `pytest backend/tests/test_auth.py -v`
  - Expected: failures because `config.py` / `auth.py` and middleware behavior do not exist.

- [ ] **Step 3: Implement configuration and auth**
  - `Settings` parses `APP_ENV`, `AUTH_MODE`, `SUPABASE_URL`, `ALLOWED_USER_ID`, `SUPABASE_JWT_AUDIENCE`, `FRONTEND_ORIGINS`, database/storage settings.
  - Use PyJWT JWKS verification with issuer `<SUPABASE_URL>/auth/v1`, audience default `authenticated`, expiration validation, and an explicit algorithm allowlist.
  - Middleware protects `/api/*` only, skips `OPTIONS`, and leaves `/` public.
  - Add `PyJWT[crypto]` dependency.

- [ ] **Step 4: Run Task 1 tests**
  - Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**
  - Message: `feat: add single-user API authentication`

### Task 2: SQLite/Turso Database Boundary and Tracked Migrations

**Files:**
- Modify: `backend/database.py`
- Create: `backend/tests/test_database_modes.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `get_db()` with the existing application-facing connection API.
- Produces: mapping-compatible remote rows supporting `row["column"]`, `row[index]`, `dict(row)`.
- Produces: `apply_migrations(conn) -> None`.

- [ ] **Step 1: Write failing database tests**
  - `test_sqlite_mode_keeps_named_rows`
  - `test_turso_mode_requires_url_and_token`
  - `test_turso_mode_uses_serverless_driver`
  - `test_remote_rows_support_name_index_and_dict_conversion`
  - `test_existing_legacy_column_is_adopted_into_schema_migrations`
  - `test_migration_error_propagates`
  - `test_init_db_is_idempotent`

- [ ] **Step 2: Run Task 2 tests and verify expected failures**
  - Expected: fail because Turso mode, mapping rows, and tracked migrations do not exist.

- [ ] **Step 3: Implement database boundary**
  - Keep raw `sqlite3.Connection` in local mode.
  - Use `turso_serverless.connect(url, auth_token=...)` for production remote mode.
  - Add a minimal compatibility cursor/row wrapper only where the remote driver needs it; do not change route/business SQL.
  - Apply WAL only to local SQLite.
  - Preserve foreign-key initialization where supported.
  - Replace blanket migration exception swallowing with `schema_migrations` and explicit column-existence adoption.
  - Add `turso_serverless` dependency.

- [ ] **Step 4: Run Task 2 tests plus existing import/reimport tests**
  - Run: `pytest backend/tests/test_database_modes.py backend/tests/test_broker_imports.py backend/tests/test_generic_csv.py backend/tests/test_reimport.py -v`
  - Expected: all pass.

- [ ] **Step 5: Commit**
  - Message: `feat: add Turso database mode and tracked migrations`

### Task 3: Durable Private Diary Storage

**Files:**
- Create: `backend/storage.py`
- Create: `backend/tests/test_storage.py`
- Modify: `backend/main.py`
- Modify: `backend/requirements.txt`
- Modify: `frontend/src/components/Diary.js`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `StorageBackend.save(key, data, content_type) -> str`
- Produces: `StorageBackend.read(key) -> StoredObject`
- Produces: `StorageBackend.delete(key) -> None`
- Produces: authenticated `GET /api/diary/{entry_id}/image`.
- Existing `diary_entries.image_path` stores the opaque object key.

- [ ] **Step 1: Write failing storage/diary tests**
  - `test_local_storage_round_trip`
  - `test_supabase_storage_requires_server_credentials`
  - `test_supabase_storage_uses_private_bucket_client`
  - `test_production_storage_never_selects_local_filesystem`
  - `test_diary_image_endpoint_requires_existing_image`
  - `test_diary_delete_removes_storage_object`
  - `test_diary_upload_rejects_oversize_payload`

- [ ] **Step 2: Run Task 3 tests and verify expected failures**
  - Expected: fail because storage abstraction/image endpoint do not exist.

- [ ] **Step 3: Implement storage abstraction and diary integration**
  - Local mode writes/reads/deletes under `UPLOAD_DIR`.
  - Supabase mode uses a private bucket and server-only service credential.
  - Upload HEIC conversion remains supported; production may use a temporary local file only for the duration of AI image analysis, then deletes it.
  - Add authenticated image endpoint returning correct content type.
  - Delete stored object when diary entry is deleted.
  - Add a configurable diary-upload byte limit.
  - Stop mounting `/uploads` in production Supabase-storage mode; keep it for local mode only.
  - Update Diary UI to request image data through authenticated API and create/revoke object URLs.

- [ ] **Step 4: Run storage tests plus relevant frontend tests/build**
  - Backend expected: all storage tests pass.
  - Frontend expected: Diary rendering/build remains green.

- [ ] **Step 5: Commit**
  - Message: `feat: add private durable diary storage`

### Task 4: Frontend Authentication Gate and Bearer API Client

**Files:**
- Create: `frontend/src/authClient.js`
- Create: `frontend/src/AuthContext.js`
- Create: `frontend/src/components/LoginScreen.js`
- Create: `frontend/src/auth.integration.test.js`
- Modify: `frontend/src/index.js`
- Modify: `frontend/src/App.js`
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/components/AppHeader.js`
- Modify: `frontend/src/index.css`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Produces: `isAuthEnabled() -> boolean`
- Produces: `getAccessToken() -> Promise<string | null>`
- Produces: `AuthProvider` / `useAuth()`.
- Axios request interceptor consumes `getAccessToken()`.
- Axios response interceptor signs out on 401 when Supabase auth is enabled.

- [ ] **Step 1: Write failing frontend auth tests**
  - `renders login gate when supabase auth has no session`
  - `renders journal shell after authenticated session resolves`
  - `sign in submits email and password`
  - `api interceptor attaches bearer access token`
  - `401 response signs out and returns to login gate`
  - `disabled local auth renders journal without Supabase configuration`

- [ ] **Step 2: Run auth tests and verify expected failures**
  - Run: `npx craco test --watchAll=false frontend/src/auth.integration.test.js` or CRA-equivalent path from `frontend`.
  - Expected: fail because auth client/context/login gate do not exist.

- [ ] **Step 3: Implement frontend auth**
  - Add `@supabase/supabase-js`.
  - Only initialize Supabase in `REACT_APP_AUTH_MODE=supabase`.
  - Restore session and subscribe to auth changes.
  - Show a professional email/password login card with no signup action.
  - Attach bearer tokens centrally in Axios.
  - Sign out on backend 401.
  - Add Sign Out action to the header only when auth is enabled.

- [ ] **Step 4: Run full frontend tests and build**
  - Run: `npx craco test --watchAll=false`
  - Run: `npm run build`
  - Expected: tests and build pass.

- [ ] **Step 5: Commit**
  - Message: `feat: add private frontend authentication`

### Task 5: Production Error Hardening and Upload Safety

**Files:**
- Create: `backend/tests/test_production_hardening.py`
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: `Settings.app_env` from Task 1.
- Produces: sanitized production 500 responses.
- Produces: reusable upload-size validation for CSV and diary endpoints.

- [ ] **Step 1: Write failing hardening tests**
  - `test_production_500_does_not_leak_exception_message_or_type`
  - `test_development_500_can_expose_debug_detail`
  - `test_csv_import_rejects_oversize_payload`
  - `test_invalid_diary_extension_remains_400`

- [ ] **Step 2: Run and verify failures**
  - Expected: production exception leak and missing CSV size guard are detected.

- [ ] **Step 3: Implement hardening**
  - Log detailed exceptions server-side.
  - Production response is a generic error body.
  - Development can retain diagnostic detail.
  - Add explicit configurable max bytes for CSV uploads and reuse diary limit validation.

- [ ] **Step 4: Run backend full suite**
  - Run: `pytest backend/tests -v`
  - Expected: all backend tests pass.

- [ ] **Step 5: Commit**
  - Message: `security: harden production errors and uploads`

### Task 6: GitHub Pages, Render Blueprint, Environment Contract, and Deployment CI

**Files:**
- Create: `.github/workflows/deploy-pages.yml`
- Create: `render.yaml`
- Create: `docs/WEB_DEPLOYMENT.md`
- Modify: `.env.example`
- Modify: `frontend/package.json` only if Pages path needs package-level configuration
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: production env names from Tasks 1-4.
- Produces: Pages deployment from `main`.
- Produces: Render Blueprint for stateless FastAPI.
- Produces: operator checklist for Supabase/Turso/Render/GitHub configuration.

- [ ] **Step 1: Add deployment-contract checks before deployment configuration**
  - Add a lightweight backend test that production settings fail closed when required auth/database/storage variables are missing.
  - Add a frontend build-mode test/check ensuring Supabase public values are required only when auth mode is enabled.
  - Expected initial failure until deployment config/env contract is complete.

- [ ] **Step 2: Add GitHub Pages workflow**
  - Build `frontend` with `PUBLIC_URL=/Trading-Journal-AI`.
  - Inject only public frontend configuration from GitHub Actions variables/secrets appropriate for browser exposure.
  - Use current official Pages actions and `github-pages` environment permissions.

- [ ] **Step 3: Add Render Blueprint**
  - One free Python web service.
  - Build: install `backend/requirements.txt`.
  - Start: Uvicorn on `0.0.0.0:$PORT` from backend context.
  - Secret values remain `sync: false` / dashboard-provided and never hardcoded.

- [ ] **Step 4: Document one-time provider setup**
  - Exact Supabase: create owner user, disable public signups, private storage bucket, capture project URL/public key/service key/user UUID.
  - Exact Turso: create database and scoped auth token.
  - Exact Render: connect repo Blueprint and enter backend secrets.
  - Exact GitHub Pages: enable Actions deployment and add public build configuration.
  - Include free-tier cold-start/persistence expectations and future paid upgrade path.

- [ ] **Step 5: Run complete verification**
  - Backend: `pytest backend/tests -v`
  - Frontend: `npx craco test --watchAll=false`
  - Frontend: `npm run build`
  - Inspect workflow YAML and Render Blueprint for secret leakage.
  - Expected: all tests/build green and no secrets committed.

- [ ] **Step 6: Commit**
  - Message: `deploy: add secure free web deployment foundation`
