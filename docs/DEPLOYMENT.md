# Private Free-First Web Deployment

This guide deploys the existing journal without changing its local-first defaults.

## Target architecture

- Frontend: GitHub Pages at `https://load66.github.io/Trading-Journal-AI/`.
- API: Render Free running the FastAPI service from `backend/`.
- Identity: Supabase email/password Auth, restricted again by backend to one owner user ID.
- Trading data: Turso remote database through `turso_serverless`.
- Diary files: a private Supabase Storage bucket with short-lived signed URLs.

Render is stateless in this design. Do not put the SQLite database or permanent uploads on Render's local filesystem.

## 1. Create the Supabase owner and private bucket

1. Create a Supabase project on the Free plan.
2. In Authentication, create the one owner user with the email/password you will use for the journal.
3. Copy that user's UUID. It becomes `AUTHORIZED_USER_ID` on Render.
4. Disable new-user signup in the Supabase Auth settings after the owner exists.
5. Create a Storage bucket named `trading-journal-diary` and keep the bucket private.
6. Copy the project URL and the browser publishable key. These are the only Supabase values allowed in the React build.
7. Copy a server secret key for Render. Never place this key in a GitHub Actions variable or any `REACT_APP_*` variable.

## 2. Create the Turso database

With the current Turso CLI:

```bash
turso db create trading-journal --tursodb
turso db show --url trading-journal
turso db tokens create trading-journal
```

Save the exact URL output as `TURSO_DATABASE_URL` and the generated token as `TURSO_AUTH_TOKEN`. The backend creates and migrates its schema on startup.

### Optional: move existing local journal data

Do this only after the empty hosted database has started successfully once and after making a backup of `trading_journal.db`.

```bash
cp trading_journal.db trading_journal.backup.db
sqlite3 trading_journal.db .dump > trading_journal.sql
turso db shell trading-journal < trading_journal.sql
```

Validate account counts, trade counts, diary counts, and several known P&L rows before treating the hosted database as authoritative. Keep the local backup until the hosted journal has been verified from both phone and desktop.

## 3. Create the Render API

Use Render's Blueprint flow against this repository. The root `render.yaml` defines the free Python service. Render will prompt for every `sync: false` value.

Required values:

```text
AUTHORIZED_USER_ID=<Supabase owner UUID>
SUPABASE_URL=<Supabase project URL>
SUPABASE_SECRET_KEY=<server secret key>
TURSO_DATABASE_URL=<Turso database URL>
TURSO_AUTH_TOKEN=<Turso database token>
```

Optional existing integrations:

```text
ANTHROPIC_API_KEY=
APCA_API_KEY_ID=
APCA_API_SECRET_KEY=
```

The Blueprint supplies the safe production modes and `FRONTEND_ORIGINS=https://load66.github.io`. Do not override production to `AUTH_MODE=disabled`, `DATABASE_MODE=sqlite`, or `STORAGE_MODE=local`; startup intentionally rejects those fallbacks.

After deployment, open the Render service root URL. `GET /` must return the health response. A direct request to an `/api/*` URL without a bearer token must return 401.

## 4. Configure GitHub Pages public build variables

In GitHub repository Settings > Secrets and variables > Actions > Variables, create:

```text
REACT_APP_API_URL=https://<your-render-service>.onrender.com
REACT_APP_SUPABASE_URL=https://<your-project>.supabase.co
REACT_APP_SUPABASE_PUBLISHABLE_KEY=<browser publishable key>
```

These are browser-visible values by design. Do not add server secrets here.

Then in Settings > Pages, select GitHub Actions as the publishing source. The Pages workflow remains skipped until all three variables exist.

Run the `Deploy GitHub Pages` workflow (or merge/push an eligible frontend change to main). The production build sets:

```text
PUBLIC_URL=/Trading-Journal-AI
REACT_APP_AUTH_ENABLED=true
```

## 5. Verification checklist

1. Open the Pages URL in a private/incognito browser. Only the login screen should render.
2. Confirm there is no signup/registration action.
3. Confirm a bad password fails and the owner password succeeds.
4. Confirm dashboard/trades/reports load after login.
5. Import a small known broker CSV and record the resulting trade count/P&L.
6. Let Render spin down, then reopen the app and confirm the imported data still exists after the cold start.
7. Upload a diary image. Confirm it renders in the journal but the database stores an object path rather than a public URL.
8. Sign out and verify API-backed data is no longer visible.
9. Test from a second device to confirm the data is cloud-persistent rather than browser-local.

## Free-tier behavior

Render Free may spin down when idle, so the first API request after inactivity can be noticeably slower. Turso and Supabase remain the data stores; a Render restart must not erase journal data.

## Rollback

- Frontend: disable the Pages deployment or revert the deployment commit.
- API: suspend/delete the Render service only after confirming Turso/Supabase contain the persistent data you want to keep.
- Data: keep the Turso database and Supabase bucket while troubleshooting; they are deliberately independent of Render.
- Local: the original SQLite/local-file workflow remains available by using the local default environment modes.

## Security rules

- Never commit `.env`, database files, broker exports, API keys, or Supabase server secret keys.
- Never prefix a secret with `REACT_APP_`; Create React App embeds those values into browser assets.
- Keep Supabase signup disabled after the owner account is created.
- Rotate a Turso/Supabase/API credential if it is ever pasted into source code, an issue, a PR, or a browser-visible variable.
