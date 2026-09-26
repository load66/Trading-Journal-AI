# Web Deployment Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Ship a private, single-user, free-first web deployment foundation for Trading Journal AI while preserving localhost behavior and existing trading logic.

**Architecture:** GitHub Pages serves the React SPA; Render Free runs stateless FastAPI; Supabase provides email/password identity and private diary-file storage; Turso provides the persistent SQLite-compatible cloud database. Production fails closed when security or persistence configuration is incomplete.

**Tech Stack:** React 19 / CRA+CRACO, Axios, FastAPI 0.141.1, Python 3.11, SQLite, turso-serverless 0.1.0, PyJWT 2.15.0 + crypto, Supabase Python 2.31.0, GitHub Actions/Pages, Render Blueprint.

**Spec:** docs/superpowers/specs/2026-09-26-web-deployment-design.md

## Global Constraints

- Do not change broker reconstruction, trading calculations, or Schwab importing in this phase.
- Preserve localhost defaults: auth disabled, SQLite file database, local diary files.
- Production must never silently fall back to disabled auth, local SQLite, or local durable storage.
- Protect every /api/* route in production; keep GET / public for Render health checks and allow CORS preflight.
- Only AUTHORIZED_USER_ID may use the production API, even if another valid Supabase user exists.
- Never expose server secrets through REACT_APP_* or committed files.
- GitHub Pages must build for /Trading-Journal-AI/ and localhost builds must remain unchanged.
- Existing tests remain green; all new behavior follows RED -> GREEN TDD.
- Exact dependency pins: turso-serverless==0.1.0, PyJWT[crypto]==2.15.0, supabase==2.31.0.

## Review Focus

1. Partial production configuration must refuse startup instead of silently using local auth/data/storage. Task 1.
2. Missing, invalid/expired, and valid-but-wrong-user JWTs must fail before route logic. Task 3.
3. A legacy SQLite DB with old columns but no migration ledger must initialize without duplicate-column errors/data loss. Task 2.
4. Remote diary storage must use signed private URLs and clean temporary files even when AI analysis fails. Task 4.
5. Expired frontend sessions must refresh before API calls; failed refresh must clear the session. Task 5.

---

### Task 1: Runtime configuration and fail-closed production contract

**Files:**
- Create: backend/runtime_config.py
- Create: backend/tests/test_runtime_config.py
- Modify: .env.example
- Modify: backend/main.py

**Interfaces:**
- Produces: RuntimeConfig, ConfigurationError, load_runtime_config(env=None) -> RuntimeConfig.
- Consumes: environment variables defined by the spec.

- [ ] **Step 1: Write failing runtime-config tests**
Test local defaults, valid production config, missing required production settings, and forbidden production fallbacks AUTH_MODE=disabled, DATABASE_MODE=sqlite, STORAGE_MODE=local.

- [ ] **Step 2: Run RED**
Run: pytest backend/tests/test_runtime_config.py -v
Expected: FAIL because runtime_config does not exist.

- [ ] **Step 3: Implement RuntimeConfig**
Use a frozen dataclass. Normalize modes; validate enum values and required production fields. Call validation during FastAPI lifespan before database/storage initialization.

- [ ] **Step 4: Expand .env.example**
Document local/hosted values and public-vs-secret boundaries with blank secret values.

- [ ] **Step 5: Verify GREEN**
Run: pytest backend/tests/test_runtime_config.py backend/tests -v
Expected: PASS.

- [ ] **Step 6: Commit**
Commit: feat: add fail-closed runtime configuration

### Task 2: SQLite/Turso database boundary and explicit migrations

**Files:**
- Create: backend/tests/test_database_backends.py
- Modify: backend/database.py
- Modify: backend/requirements.txt

**Interfaces:**
- Consumes: RuntimeConfig database mode/path/Turso URL/token.
- Produces: unchanged get_db(), init_db(), row_to_dict(row); remote rows support row[0], row['column'], keys(), and dict conversion.

- [ ] **Step 1: Write failing database tests**
Cover local selection, fake Turso selection, row compatibility, legacy migration adoption, idempotent init, and surfaced unexpected migration errors.

- [ ] **Step 2: Run RED**
Run: pytest backend/tests/test_database_backends.py -v
Expected: FAIL on missing backend/migration behavior.

- [ ] **Step 3: Implement DB adapter**
Keep native sqlite3 locally. Wrap Turso DB-API cursor rows using cursor.description and delegate execute/executemany/commit/rollback/close/lastrowid.

- [ ] **Step 4: Implement schema_migrations**
Inspect PRAGMA table_info before legacy ALTERs; adopt already-present columns; raise unexpected failures.

- [ ] **Step 5: Pin turso-serverless==0.1.0**

- [ ] **Step 6: Verify GREEN**
Run: pytest backend/tests/test_database_backends.py backend/tests -v
Expected: PASS.

- [ ] **Step 7: Commit**
Commit: feat: add Turso database backend and migrations

### Task 3: Single-user Supabase JWT protection

**Files:**
- Create: backend/auth.py
- Create: backend/tests/test_auth.py
- Modify: backend/main.py
- Modify: backend/requirements.txt

**Interfaces:**
- Consumes: auth mode, Supabase URL, authorized user ID.
- Produces: SingleUserAuthMiddleware and verify_supabase_access_token(token, config) -> dict.

- [ ] **Step 1: Write failing auth tests**
Use a tiny FastAPI app/fake verifier: local bypass; health bypass; OPTIONS bypass; missing bearer 401; invalid token 401; wrong sub 403; owner token reaches route.

- [ ] **Step 2: Run RED**
Run: pytest backend/tests/test_auth.py -v
Expected: FAIL because auth module does not exist.

- [ ] **Step 3: Implement JWT verification/middleware**
Use PyJWT PyJWKClient at <SUPABASE_URL>/auth/v1/.well-known/jwks.json; accept RS256/ES256 only; validate issuer, audience authenticated, expiry, and subject.

- [ ] **Step 4: Protect /api/***
Leave / public and keep localhost CORS plus configured production origin.

- [ ] **Step 5: Sanitize production 500 responses**
Log server-side; return only Internal server error in production while keeping local diagnostics.

- [ ] **Step 6: Pin PyJWT[crypto]==2.15.0**

- [ ] **Step 7: Verify GREEN**
Run: pytest backend/tests/test_auth.py backend/tests -v
Expected: PASS.

- [ ] **Step 8: Commit**
Commit: feat: protect hosted API with single-user auth

### Task 4: Private diary storage abstraction

**Files:**
- Create: backend/storage.py
- Create: backend/tests/test_storage.py
- Create: backend/tests/test_diary_storage.py
- Modify: backend/main.py
- Modify: backend/requirements.txt
- Modify: frontend/src/components/Diary.js

**Interfaces:**
- Consumes: storage mode, upload dir, Supabase URL/secret/bucket.
- Produces: get_diary_storage(config) with save, signed_url, delete, local_path.

- [ ] **Step 1: Write failing storage tests**
Cover local save/delete/path; fake Supabase upload/delete/signed URL; safe filename path traversal defense; private signed URL behavior.

- [ ] **Step 2: Write failing diary integration tests**
Cover remote upload storing object path only; signed URL on list; temp cleanup on AI success/failure; best-effort object deletion.

- [ ] **Step 3: Run RED**
Run: pytest backend/tests/test_storage.py backend/tests/test_diary_storage.py -v
Expected: FAIL because storage abstraction does not exist.

- [ ] **Step 4: Implement local + Supabase backends**
Never use public object URLs. Normalize upload names using basename and conservative characters.

- [ ] **Step 5: Refactor diary upload/list/delete**
Preserve HEIC conversion. Use try/finally temp files for remote image analysis. Persist object before DB row and delete object if DB insert fails. Return signed image_url only as response data.

- [ ] **Step 6: Update Diary.js**
Prefer entry.image_url; otherwise retain local /uploads/<image_path> behavior.

- [ ] **Step 7: Pin supabase==2.31.0**

- [ ] **Step 8: Verify GREEN**
Run: pytest backend/tests/test_storage.py backend/tests/test_diary_storage.py backend/tests -v
Run: cd frontend && npx craco test --watchAll=false
Expected: PASS.

- [ ] **Step 9: Commit**
Commit: feat: add private cloud diary storage

### Task 5: Frontend email/password gate and bearer API client

**Files:**
- Create: frontend/src/auth.js
- Create: frontend/src/AuthGate.js
- Create: frontend/src/auth.test.js
- Create: frontend/src/AuthGate.test.js
- Modify: frontend/src/api.js
- Modify: frontend/src/index.js
- Modify: frontend/src/index.css

**Interfaces:**
- Produces: AUTH_ENABLED, signIn, signOut, getStoredSession, getAccessToken, subscribeAuth.
- Consumes: REACT_APP_AUTH_ENABLED, REACT_APP_SUPABASE_URL, REACT_APP_SUPABASE_PUBLISHABLE_KEY.
- Axios consumes getAccessToken() and sets Bearer only when a token exists.

- [ ] **Step 1: Write failing auth-client tests**
Mock fetch/localStorage: password sign-in request, persisted expiry, refresh-before-expiry, refresh failure clears session, sign-out clears session.

- [ ] **Step 2: Write failing AuthGate tests**
Auth disabled renders children; enabled/no session shows login and no signup; successful login unlocks children; logout returns to login.

- [ ] **Step 3: Run RED**
Run: cd frontend && npx craco test --watchAll=false src/auth.test.js src/AuthGate.test.js
Expected: FAIL because auth modules do not exist.

- [ ] **Step 4: Implement auth client/gate**
Use Supabase Auth HTTP endpoints directly. Refresh within 60 seconds of expiry. Emit same-tab/storage auth changes.

- [ ] **Step 5: Add Axios bearer interceptor**
Await getAccessToken before API calls; a production 401 clears invalid session and notifies the gate.

- [ ] **Step 6: Wrap root**
index.js renders <AuthGate><App /></AuthGate>; direct App integration tests remain independent.

- [ ] **Step 7: Style accessible login/logout**
Use existing design tokens, visible labels, role=alert, and no registration control.

- [ ] **Step 8: Verify GREEN**
Run: cd frontend && npx craco test --watchAll=false
Run: cd frontend && PUBLIC_URL=/Trading-Journal-AI REACT_APP_AUTH_ENABLED=true REACT_APP_API_URL=https://api.example.test REACT_APP_SUPABASE_URL=https://example.supabase.co REACT_APP_SUPABASE_PUBLISHABLE_KEY=test-public-key npm run build
Expected: PASS.

- [ ] **Step 9: Commit**
Commit: feat: add private frontend login gate

### Task 6: GitHub Pages and Render deployment definitions

**Files:**
- Create: .github/workflows/deploy-pages.yml
- Create: render.yaml
- Create: docs/DEPLOYMENT.md
- Create: backend/tests/test_deployment_contract.py
- Modify: README.md

**Interfaces:**
- Consumes public GitHub vars: REACT_APP_API_URL, REACT_APP_SUPABASE_URL, REACT_APP_SUPABASE_PUBLISHABLE_KEY.
- Produces Pages artifact/deploy workflow, Render Blueprint, exact provisioning guide.

- [ ] **Step 1: Write failing deployment-contract tests**
Assert free Render backend root, production modes, sync:false secrets, health path /; Pages PUBLIC_URL, auth enabled, official Pages actions, and absence of server secret names.

- [ ] **Step 2: Run RED**
Run: pytest backend/tests/test_deployment_contract.py -v
Expected: FAIL because deployment files do not exist.

- [ ] **Step 3: Add Pages workflow**
Use configure-pages@v5, upload-pages-artifact@v4, deploy-pages@v4; grant pages:write and id-token:write.

- [ ] **Step 4: Add render.yaml**
Use free Python service, backend root, pip build, Uvicorn start, public health path, production modes, sync:false secrets.

- [ ] **Step 5: Write docs/DEPLOYMENT.md**
Exact order: Supabase owner/bucket/signup lock, Turso DB/token, Render Blueprint/secrets, GitHub variables/Pages. Include rollback and existing-local-data notes. Do not claim external resources exist until provisioned.

- [ ] **Step 6: Update README**
Link deployment guide and state local-first remains default until cloud config exists.

- [ ] **Step 7: Verify GREEN**
Run: pytest backend/tests/test_deployment_contract.py -v
Expected: PASS.

- [ ] **Step 8: Commit**
Commit: ci: add free private web deployment

### Task 7: Full regression verification and security review

**Files:** Modify only if verification exposes a spec-covered defect.

**Interfaces:** Consumes Tasks 1-6 and produces a green PR-ready branch.

- [ ] **Step 1: Run complete backend suite**
Run: pytest backend/tests -v
Expected: 0 failures.

- [ ] **Step 2: Run complete frontend suite**
Run: cd frontend && npx craco test --watchAll=false
Expected: 0 failures.

- [ ] **Step 3: Run production frontend build**
Run with PUBLIC_URL=/Trading-Journal-AI, auth enabled, and non-secret placeholder production URLs/keys.
Expected: exit 0.

- [ ] **Step 4: Secret/fallback scan**
Expected: no committed secret values and no production local fallbacks.

- [ ] **Step 5: Review whole branch against spec and Review Focus**
Critical/Important findings get one RED->GREEN fix pass plus full suites. Minor findings are recorded.

- [ ] **Step 6: Commit verified fixes if needed**
Commit: fix: address web deployment review findings