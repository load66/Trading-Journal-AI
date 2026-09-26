# Private Web Deployment

This runbook provisions the private, free-first hosted version of Trading Journal AI.

## Architecture

- GitHub Pages: React frontend
- Render Free: stateless FastAPI API
- Supabase Auth: single-user email/password login
- Supabase Postgres: durable trading database in the private `journal` schema
- Supabase Storage: private diary files

Local development remains SQLite + local uploads and does not require cloud authentication.

## 1. Supabase

The hosted app uses one Supabase project for authentication, Postgres, and private diary storage.

1. In Authentication, create the owner's email/password user.
2. Record that user's UUID. This becomes `AUTHORIZED_USER_ID`.
3. Disable public user signup after the owner account exists.
4. Create a private Storage bucket named `diary`.
5. Apply the committed migrations under `supabase/migrations/`.
6. Verify the `journal` schema exists and browser roles have no direct access.
7. Record:
   - project URL -> `SUPABASE_URL`
   - browser publishable key -> GitHub variable `REACT_APP_SUPABASE_PUBLISHABLE_KEY`
   - secret key -> Render secret `SUPABASE_SECRET_KEY`

The Supabase secret key is server-only. Never place it in GitHub Pages variables or any `REACT_APP_*` value.

### Postgres connection for Render

The backend connects directly to Postgres with psycopg. Set Render's `DATABASE_URL` to a Supabase Postgres connection string.

For a persistent backend that needs IPv4 connectivity, use the **Supavisor session-mode** connection from the Supabase **Connect** panel. Its shape is:

```text
postgresql://postgres.<project-ref>:<database-password>@<session-pooler-host>:5432/postgres
```

The exact connection string and database password are secrets. Enter them directly in Render; never paste them into frontend variables or commit them.

## 2. Render

Create/deploy the repository using the root `render.yaml` Blueprint.

Set the secret values requested by the Blueprint:

- `SUPABASE_URL`
- `AUTHORIZED_USER_ID`
- `DATABASE_URL`
- `SUPABASE_SECRET_KEY`
- `ANTHROPIC_API_KEY` if Brain/AI features are wanted
- `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` if market charts are wanted

The Blueprint sets production mode, authentication required, Postgres database mode, Supabase storage mode, the private bucket name, and the GitHub Pages CORS origin.

After deploy:

1. Verify `/health` returns `{"status":"ok"}`.
2. Copy the Render HTTPS service URL.
3. Verify an unauthenticated `/api/` request is rejected.
4. Verify the owner can authenticate before proceeding to Pages.

## 3. GitHub repository variables

In repository Actions variables, set these public build-time values:

- `REACT_APP_API_URL` = the Render HTTPS service URL
- `REACT_APP_SUPABASE_URL` = the Supabase project URL
- `REACT_APP_SUPABASE_PUBLISHABLE_KEY` = the Supabase browser publishable key

These values are intentionally browser-visible. Never put `DATABASE_URL`, Supabase secret keys, Anthropic keys, Alpaca secrets, or passwords in a `REACT_APP_*` value.

## 4. GitHub Pages

Configure Pages to deploy from **GitHub Actions**.

The `Deploy GitHub Pages` workflow:

1. validates all required public variables;
2. runs frontend tests;
3. builds with `PUBLIC_URL=/Trading-Journal-AI`;
4. publishes `frontend/build`.

Expected URL:

`https://load66.github.io/Trading-Journal-AI/`

## 5. Verification

Before considering the hosted journal ready:

- `/health` is public and healthy;
- unauthenticated API requests return 401;
- a valid token for a different user returns 403;
- the owner can sign in and load accounts;
- refresh preserves the session;
- Sign out returns to login;
- a trade created online remains after a Render restart/spin-down;
- a diary image remains available after a Render restart/spin-down;
- Supabase `journal` tables retain RLS and no direct `anon`/`authenticated` grants;
- localhost still works with cloud variables absent;
- no real credentials exist in tracked repository files.

## Upgrade path

The frontend/API contracts do not depend on the free hosting tier. Render can be upgraded to always-on compute without changing the application. Supabase can be upgraded independently without changing the FastAPI API contract.
