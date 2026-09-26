# Private Web Deployment v1 — Design

**Date:** 2026-09-26  
**Status:** Approved  
**Branch:** `feature/web-deployment-v1`

## Goal

Make Trading Journal AI securely accessible from the web while preserving the existing localhost workflow and keeping initial hosting cost at $0.

## Decisions

- Frontend: React static build deployed to GitHub Pages at the project path.
- API: existing FastAPI application deployed to a free Python web host (Render is the initial target).
- Authentication: Supabase email/password. Public signup is disabled after the owner's account is created.
- Authorization: every protected API request must carry a valid Supabase access token; production additionally restricts access to one configured Supabase user id.
- Trading database: keep SQLite locally. Introduce a database adapter boundary so production can use a remote SQLite-compatible service (Turso) without converting the application to PostgreSQL.
- File storage: diary images use private object storage in production; local development continues to use the existing upload directory.
- Secrets: server-side only. No Anthropic, Alpaca, Turso, service-role, or other privileged credentials may be embedded in the React bundle or committed to Git.
- Deployment must remain independently testable. Do not mix Schwab importer changes into this branch.

## Runtime modes

### Local

```
React dev server -> FastAPI localhost -> local SQLite + local uploads
```

Local development must continue to work without Supabase/Turso credentials unless authentication is explicitly enabled.

### Hosted

```
GitHub Pages
    |
    | Supabase access token
    v
FastAPI (Render)
    |-- Turso / remote SQLite-compatible database
    |-- private object storage for diary images
    |-- Anthropic / Alpaca using server-side environment variables
```

The backend is stateless with respect to durable user data.

## Authentication and authorization

1. The frontend owns login/logout/session UI using Supabase Auth.
2. Axios attaches `Authorization: Bearer <access_token>` to API requests.
3. FastAPI validates JWT signature and standard claims using the configured Supabase issuer/JWKS.
4. When `AUTHORIZED_USER_ID` is configured, the JWT `sub` must equal it.
5. Health endpoints required by hosting may remain unauthenticated and must expose no private data.
6. API routes, including Library routes and uploaded/private media access, are protected in hosted mode.
7. Production startup fails closed if auth is enabled but required auth configuration is incomplete.
8. CORS is limited to configured production origins plus localhost development origins.

## Database boundary

The existing code calls SQLite directly in many places. v1 introduces a small connection factory without changing application-level SQL unnecessarily.

- `DATABASE_MODE=sqlite` remains the default.
- Local SQLite preserves `sqlite3.Row`-style mapping behavior expected by current code.
- Remote mode is selected explicitly and configured entirely from environment variables.
- Database setup/migrations become explicit and observable. New migration failures must not be silently swallowed.
- A later migration to PostgreSQL must be possible without changing frontend/API contracts.

## Upload/storage boundary

Broker CSVs are temporary processing inputs and are not durable user storage by default.

Diary images:
- local mode: current filesystem behavior;
- hosted mode: private object storage;
- access is through authenticated application logic or signed/private object access, never a public bucket.

## Frontend deployment

- Build must support GitHub Pages project path `/Trading-Journal-AI/`.
- `REACT_APP_API_URL` is build-time configuration and is not secret.
- Supabase public URL/anon key may be build-time frontend configuration; privileged keys never are.
- GitHub Pages deployment runs only after the frontend build/tests succeed.
- Local `npm start` behavior remains unchanged.

## Production hardening

- Do not return raw exception class names or internal exception strings for unexpected 500 errors in production.
- Log server-side details; return a generic error body to the browser.
- Add a dedicated health endpoint suitable for hosting probes.
- Validate production configuration at startup.
- Never persist the production database or uploaded private images on Render's ephemeral filesystem.

## External provisioning

The repository can contain deployment manifests, environment-variable documentation, and workflows, but external services require owner-controlled accounts/projects and credentials. No credentials are invented or committed.

Required external values will be documented as a checklist:
- Supabase project URL, anon key, issuer/JWKS configuration, owner user id, private storage bucket.
- Turso database URL/token (or equivalent remote SQLite values selected by the adapter).
- Render service configuration and environment variables.
- GitHub Pages build variables/secrets required by the frontend.

## Verification

Before merge:
- backend tests pass;
- auth tests prove missing/invalid/wrong-user tokens are rejected in hosted mode and local mode remains usable;
- database adapter tests prove local SQLite compatibility and configuration failure behavior;
- frontend tests prove login gating and auth header attachment;
- frontend production build succeeds for the GitHub Pages base path;
- CI validates both backend and frontend;
- no secret values exist in committed files.

## Deferred from v1

- Schwab importer.
- PostgreSQL migration.
- multi-user accounts/roles.
- large UI redesign.
- paid always-on hosting.
- normalized execution-table refactor.
