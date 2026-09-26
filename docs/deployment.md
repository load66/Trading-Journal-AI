# Private Web Deployment

This runbook provisions the free-first hosted version of Trading Journal AI.

## Architecture

- GitHub Pages: React frontend
- Render Free: stateless FastAPI API
- Turso: durable trading database
- Supabase Auth: single-user email/password login
- Supabase Storage: private diary files

Local development remains SQLite + local uploads and does not require cloud authentication.

## 1. Supabase

Create a Supabase project.

1. In Authentication, create the owner's email/password user.
2. Record that user's UUID. This becomes `AUTHORIZED_USER_ID`.
3. Disable public user signup after the owner account exists.
4. Create a private Storage bucket named `diary`.
5. Record:
   - project URL -> `SUPABASE_URL`
   - browser publishable key -> GitHub variable `REACT_APP_SUPABASE_PUBLISHABLE_KEY`
   - secret key -> Render secret `SUPABASE_SECRET_KEY`

The secret key is a server secret. Never place it in GitHub Pages variables or any `REACT_APP_*` value.

## 2. Turso

Create a database and a database auth token. Record:

- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`

These are Render secrets. Do not commit them.

## 3. Render

Create/deploy the repository using the root `render.yaml` Blueprint.

Set the secret values requested by the Blueprint:

- `SUPABASE_URL`
- `AUTHORIZED_USER_ID`
- `TURSO_DATABASE_URL`
- `TURSO_AUTH_TOKEN`
- `SUPABASE_SECRET_KEY`
- `ANTHROPIC_API_KEY` if Brain/AI features are wanted
- `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` if market charts are wanted

The Blueprint already sets production mode, auth required, Turso mode, Supabase storage mode, the private bucket name, and the GitHub Pages CORS origin.

After deploy, verify `/health` returns `{"status":"ok"}`. Copy the Render HTTPS service URL.

## 4. GitHub repository variables

In repository Actions variables, set these public build-time values:

- `REACT_APP_API_URL` = the Render HTTPS service URL
- `REACT_APP_SUPABASE_URL` = Supabase project URL
- `REACT_APP_SUPABASE_PUBLISHABLE_KEY` = Supabase browser publishable/anon key

These three values are intentionally browser-visible. Never use secret, Turso, Anthropic, Alpaca-secret, or password values here.

## 5. GitHub Pages

Configure Pages to deploy from **GitHub Actions**.

The `Deploy GitHub Pages` workflow:

1. validates all required public variables;
2. runs frontend tests;
3. builds with `PUBLIC_URL=/Trading-Journal-AI`;
4. publishes `frontend/build`.

Expected URL:

`https://load66.github.io/Trading-Journal-AI/`

## 6. Verification

Before considering the hosted journal ready:

- unauthenticated API request returns 401;
- wrong Supabase user returns 403;
- owner can sign in and load accounts;
- refresh preserves the session;
- Sign out returns to login;
- a trade created online remains after a Render restart/spin-down;
- a diary image remains available after a Render restart/spin-down;
- localhost still works with cloud variables absent;
- no real credentials exist in tracked repository files.

## Upgrade path

The frontend/API contracts do not depend on the free hosting tier. Render can be upgraded to always-on compute without changing the application. Database/storage providers can be migrated behind their existing boundaries later.
