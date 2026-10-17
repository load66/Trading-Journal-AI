# Finance retirement — read-only inventory and owner gate

## Owner decision and implementation checkpoint (2026-10-17)

Owner confirmed all manual Plaid/SimpleFIN disconnect actions and an empty account list in chat, and explicitly approved removal of the Tracker's optional bank-activity review. **This removes the evidence-only dependency blocker; all manual Tracker features remain protected.** The retirement implementation deletes provider-backed Finance code and related tests, with one owner-authenticated cleanup route retained until the owner executes the R2 purge. No production data purge, provider API request or environment change was performed by the agent. See `docs/FINANCE_RETIREMENT_RUNBOOK.md` for the owner's guarded cleanup steps.

Status: **inventory only; no removal, revocation, R2 purge, deployment, or live provider call**. Owner decision date: 2026-10-17. Branch: `codex/remove-finance`.

Read first: `AGENTS.md` and `docs/AUDIT_PROGRESS.md` in both repositories. This document inventories **current default branches**, not old unmerged Finance PRs. The owner must complete the in-app steps and explicitly confirm them **in chat** before any Finance code-removal implementation or Finance R2 purge is considered. Separate explicit owner approval is required for the bonus-evidence dependency described below.

## Owner's required manual actions — STOP GATE

- [ ] In **Finances → Accounts**, select **Remove / Remove bank** for **every connected Plaid bank** and finish the confirmation dialogue for each. Check the application response; do not reconnect or create a new Plaid Item. These operations are performed **by the owner**, never by this agent.
- [ ] Disconnect **SimpleFIN**, and use its delete-history action **if present**. If no complete history-delete option is exposed, report that gap; do not assume history has been deleted. Revoke the upstream SimpleFIN Bridge authorization and check any subscription separately if applicable.
- [ ] Reload the app and confirm the connected account list is empty, including Plaid connections and SimpleFIN.
- [ ] Confirm completion of the above items **in chat**, and specify whether the separate **Tracker → Review bank activity** feature is authorized to retire. Do not presume consent to remove bank-bonus features.

**Important observed mismatch:** the current frontend `plaid-fallback.js` issues `DELETE /api/bonus-tracker/plaid/items/{handle}` **without** `delete_history=true`. The backend `bonus_plaid_trial.py` defaults `delete_history=False`: this revokes an Item but can **retain local transaction history**. Owner action (a) alone is therefore **not proof of complete Finance-data deletion**. The later audited, owner-run R2 cleanup must account for the remaining history. Similarly, SimpleFIN `DELETE /api/bonus-tracker/simplefin` writes a disconnected tombstone, so its R2 state key remains until the owner-run cleanup.

## Inventory: backend `load66/Trading-Journal-AI`

Classification: **Finance** = removable after the gate; **Shared/evidence** = provider-backed bonus-review dependency requiring separate owner decision; **Keep** = independent bank-bonus or trading-journal functionality.

| Path | Role | Dependency / handling |
| --- | --- | --- |
| `backend/bonus_simplefin.py` | **Shared/evidence** | Provider credentials, polling, activity, reviews, `cipher()`, SimpleFIN private routes. The Tracker's opt-in bonus-activity panel reads its response; no trading-journal dependency identified. Do not remove until evidence retirement is explicitly decided. |
| `backend/bonus_plaid_accounts.py` | **Shared/evidence** | Plaid activity, bonus observation processing, scheduled sync. Read by Tracker evidence; no trading-journal dependency identified. |
| `backend/bonus_plaid_trial.py` | **Shared/evidence + preserve ledger** | Plaid Link/revocation, quota and global slot ledger. The *stored* `bonus-tracker-plaid-trial/v1/global.enc` is permanently **excluded from purge**. No journal dependency identified. |
| `backend/bonus_plaid_sandbox.py` | **Finance** | Sandbox verification/startup background check only. No direct Tracker or journal dependency identified; remove startup call with module. |
| `backend/finance_account_management.py` | **Shared transitively** | Provider account exclusion/deletion and history management; imported by SimpleFIN/Plaid adapters. |
| `backend/finance_ai_categories.py`, `finance_category_rules.py`, `finance_income_rules.py`, `finance_plaid_enrichment.py` | **Shared transitively** | Provider activity classification and signals. Not part of canonical manual bonus eligibility or trading-journal calculations. |
| `backend/finance_history_archive.py` | **Shared transitively** | Encrypted provider activity archives; imported by adapters. |
| `backend/finance_preferences.py` | **Finance** | Private Finances preferences router. No direct Tracker or journal dependency identified. |
| `backend/main.py` | **Mixed: edit only Finance sections** | Router registrations for SimpleFIN, Plaid Trial, Plaid accounts and preferences; auth-cache treatment of those paths; `scheduled_simplefin_loop`, `scheduled_plaid_loop` and sandbox task. Preserve all journal routes, bonus-backup loop and bank-discovery/availability loops. |
| `backend/tests/test_bonus_simplefin.py`, `test_bonus_plaid_accounts.py`, `test_bonus_plaid_trial.py`, `test_bonus_plaid_sandbox.py` | **Finance/provider** | Synthetic tests of the adapters and trial; remove only with retired modules. |
| `backend/tests/test_bonus_plaid_evidence.py` | **Shared/evidence** | Tests provider signals for campaigns; owner decision required before retiring it. |
| `backend/tests/test_finance_account_management.py`, `test_finance_ai_categories.py`, `test_finance_category_rules.py`, `test_finance_golden_ledger.py`, `test_finance_history_archive.py`, `test_finance_income_rules.py`, `test_finance_merchant_rules.py`, `test_finance_p2p_case_regression.py`, `test_finance_plaid_enrichment.py`, `test_finance_preferences.py` | **Finance** | Finance-specific test suites; remove after owner gate and dependency resolution. |
| `backend/tests/fixtures/finance-categories-contract.json` | **Finance** | Category fixture; remove after gate. |
| `backend/tests/test_auth.py` | **Mixed: keep test file** | Contains Finance-private route middleware assertions; adapt just those assertions after route retirement. Other authentication tests protect the journal and must remain. |
| `docs/plaid-sandbox-verification.md`, `docs/plaid-trial-release.md` | **Finance/provider** | Retire after gate; documentation of historical plan must not trigger provider actions. |
| `docs/bank-bonus-evidence.md`, `backend/bank_verification.py`, `backend/bank_availability.py`, `backend/bonus_questions.py`, their tests | **Keep** | Public bank-offer verification/discovery/assistant features, **not** the private bank-activity review panel. Independent of Finance adapters. |
| `backend/bonus_backups.py`, `backend/tests/test_bonus_backups.py`, `backend/venza_cloud.py`, storage/auth infrastructure | **Keep** | BonusTracker cloud recovery, vehicle-data recovery, and shared storage/auth. Do not remove or purge; provider adapters *import* the backup module's owner/storage helpers, but the backup module does not depend on provider adapters. |
| `.github/workflows/ci.yml` | **Keep, audit** | Shared backend tests and journal build; do not delete whole workflow. Confirm remaining suite green. |
| Other `backend/tests/test_*.py`, `frontend/`, trading diary and journal routes | **Keep** | Not Finance modules; preserve without rewrites. |

## Inventory: frontend `load66/bonus-tracker`

| Paths | Role | Dependency / handling |
| --- | --- | --- |
| `finance-page.js`, `finance-page.css`, `finance-unified-core.js`, `finance-transfer-core.js`, `finance-insights-core.js`, `finance-overview-core.js`, `finance-reconcile-core.js` | **Finance** | Dashboard, accounts, transactions, charts, reconciliation, transfers, overview and insight presentation. The bonus-evidence panel currently calls `BTFinanceUnified.combine`, so `finance-unified-core.js` is **shared transitively** until evidence retirement is approved. |
| `bank-sync.js`, `bank-sync.css`, `plaid-fallback.js`, `plaid-fallback.css` | **Shared/evidence** | Bank-link UI and provider connections; Tracker's bonus-evidence panel opens `btOpenBankSync`. Remove only after owner decision and manual revocation. |
| `bonus-evidence.js`, `bonus-evidence.css`, `bonus-evidence-core.js` | **Shared/evidence — PROTECTED** | Opt-in **Tracker → Review bank activity** modal and read-only provider signals, with manual payout review handoff. Do **not** remove silently as part of Finance screen removal. |
| `app.js` | **Mixed: edit only targeted branches** | Finance tab is in the page content/nav and `btOpenTrackerEvidenceReview` mounts the protected Tracker modal. Preserve the rest of Tracker, T&C, taxes, archive, and canonical bonus engine. |
| `index.html` | **Mixed: edit references** | Finance CSS/JS, bank-sync/Plaid and evidence asset registrations; ensure no dangling scripts. |
| `sw.js` | **Mixed: edit cache list** | Cache name and Finance asset list; bump version in implementation and retain safe fallback for old cached tabs. |
| `tests/finance-account-management.test.js`, `finance-copy.test.js`, `finance-golden-ledger.test.js`, `finance-insights.test.js`, `finance-invariants.test.js`, `finance-overview.test.js`, `finance-reconcile.test.js`, `finance-session.test.js`, `finance-unified.test.js`, `finance-page.e2e.cjs` | **Finance** | Retire after gate; preserve independent tests. |
| `tests/bank-sync.e2e.cjs`, `tests/plaid-fallback.e2e.cjs`, `tests/simplefin-prod-health.cjs`, `tests/wait-deployed-simplefin.cjs` | **Finance/provider** | Retire after gate; never run live-provider production audits during this task. |
| `tests/bonus-evidence.test.js`, `tests/bonus-evidence.e2e.cjs` | **Shared/evidence — PROTECTED** | Explicit bank-bonus activity tests; owner decision required. |
| `tests/ci-units.cjs`, `tests/ci-browser/existing-harnesses.spec.cjs`, `tests/ci-scope.cjs`, `tests/ci-workflow.test.js`, `tests/browser-e2e.cjs` | **Mixed: edit targeted references only** | Suite and browser matrices include Finance/provider cases; retain bonus/cloud/tracker smoke checks. |
| `.github/workflows/simplefin-production-audit.yml` | **Finance/provider** | Remove after gate. `.github/workflows/verified-product-browser.yml` and `close-rules.yml` are shared and stay. |
| `docs/FINANCIAL_ACCOUNTS.md`, `docs/SIMPLEFIN.md`, `docs/SIMPLEFIN_ADD_BANK_EXPERIENCE.md` | **Finance/provider** | Retire or replace with a concise removal redirect after gate; preserve relevant security warnings in final owner instructions. |
| `docs/BONUS_EVIDENCE_BRIDGE.md` | **Shared/evidence — PROTECTED** | Documents Tracker activity review; owner decision required. |
| `cloud-backup*.js/.css`, `tests/cloud-*.test.js`, `financial-rules.js`, `bonus-requirements-progress.js`, `bank-bonuses*.js/.css`, Tracker/offer docs | **Keep** | Independent bank-bonus rules, cloud recovery and promotions; do not remove simply because filenames mention “financial”. |
| Remaining UI, archive, tests and assets | **Keep** | Not owned by Finance. |

### Explicit shared-feature decision needed

The **Tracker's optional, private “Review bank activity”** is a bank-bonus feature, not merely a Finances tab. It reads SimpleFIN/Plaid endpoints, uses `bonus-evidence*.js`, `finance-unified-core.js`, `bank-sync.js`, and the provider modules. It can surface possible deposits/payouts/fees without ever marking the canonical Tracker milestones completed. Removing all providers necessarily disables that review. An explicit owner decision is needed to retire **only this optional provider-backed review**, while retaining manual bonus payout confirmation, eligibility, calendar/fees and all Tracker records. Do not infer permission from the general Finance retirement instruction.

### R2 storage partition (read-only; no listing or deletion performed)

Static code identifies:

- Finance-only: `bonus-tracker-simplefin/v1/{owner}/state.enc`; `bonus-tracker-simplefin/v1/{owner}/history/`; `bonus-tracker-plaid/v1/{owner}/history/`; `bonus-tracker-plaid-trial/v1/{owner}/activity.enc`; `bonus-tracker-finances/v1/{owner}/preferences.enc`.
- **Never touch**: `bonus-tracker-plaid-trial/v1/global.enc` (irreversible slot ledger), `bonus-tracker-recovery/` (bank-bonus backups), journal databases/screenshots, vehicle recovery, and all unrelated prefixes.
- A **SimpleFIN pre-disconnect snapshot** was requested for cleanup, but its precise deployed key cannot be established from the current main-branch key constructors. Later implementation must discover and explicitly whitelist the exact prefix from reviewed evidence; do not guess a wildcard or delete an entire common parent.
- `SIMPLEFIN_ENCRYPTION_KEY` presently backs `cipher()` used by provider activity, preferences and the immutable slot ledger. Whether any retained runtime consumer needs it after removal must be reassessed; do not alter the Railway variable or decrypt retained ledger data.

### Browser-local Finance residue

The Finance page also persists **only UI preferences** in browser `localStorage` keys `bt_finance_hide_v2` and `bt_finance_tx_filter`. After owner confirmation, remove these two exact keys in migration (never clear shared Tracker, recovery or authentication storage). The ordinary cloud recovery key allowlist does not include these two keys; separately downloaded old full-browser backup files are outside the server-side R2 purge and require an owner-controlled decision.

### Owner-only future R2 purge design (NOT IMPLEMENTED OR RUN HERE)

After owner chat confirmation and dependency decision, implement one-time owner-authenticated cleanup. Require a dry-run listing with **counts and allowed key prefixes only**, bind the dry-run count to an exact plan digest, require a typed confirmation phrase, enforce allowlist and explicit denylist (particularly the global ledger), use conditional/idempotent deletion, count-only logs and a post-execution list verification. Never call Plaid/SimpleFIN or mutate live data from the agent. Preserve bonus backups, journal data, screenshots and ledger. The owner alone invokes this cleanup.

### Required release gates after owner confirmation

1. Backend removal PR: run **full pytest** and backend build, preserve journal and canonical bonus checks; merge only when green, then confirm Railway **SUCCESS** on the exact merge commit.
2. Frontend removal PR: run non-Finance unit suites and Playwright on Chromium, Firefox and WebKit at **320/375/390px**; verify no Finance navigation, broken links, or browser console errors; verify the bonus Tracker and trading journal continue to load. Bump application version and service-worker cache, and provide safe old-URL state.
3. Merge frontend **after** successful backend deployment, verify Pages and live `sw.js` version. Checkpoint on `codex/remove-finance` only; set `Active agent: none` on completion. No environment-variable changes or live provider calls at any stage.
