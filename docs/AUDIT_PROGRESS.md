# Audit progress

Active agent: ChatGPT/Codex · terms archive legacy churn evidence · codex/terms-archive-legacy-churn-fields

Public-safe fields only: versions, commit hashes, PR links, sanitized issue titles, test status.

## Handoff

- Immutable BonusTracker historical T&C R2 archive: PR https://github.com/load66/Trading-Journal-AI/pull/239 (`codex/bonus-terms-immutable-r2`), new owner-authenticated append-only SHA-256 verified terms records separate from rotating backups, with read-only listing/detail, strict source-field allowlist, and isolated synthetic tests. Backend suite (410 tests) and journal frontend build/tests PASS on https://github.com/load66/Trading-Journal-AI/actions/runs/39127627819. No real owner data, credentials, providers or recovery snapshots changed. Next: merge #239 after final head CI, verify Railway production exact commit, then frontend #188.

- Railway usage HTTP 403 transport fix (`codex/railway-usage-user-agent-fix`): sets a fixed application User-Agent and JSON Accept header on outgoing read-only Railway GraphQL calls while preserving account token authentication, timeout and no-secret logging. Synthetic regression verifies the actual urllib request and existing diagnostic source tests remain. No variable or financial data changes. Next: PR CI, merge passing exact code, verify deployed commit and live auth source.

- Railway hosting authentication diagnostics PR #237 (`codex/railway-auth-layer-diagnostics-v368`): disambiguates HTTP 401, HTTP 403 and GraphQL permission errors at the initial `me` check, returning and logging only fixed source codes; no token, remote text, private data or env-var writes. Synthetic tests for all three paths and information safety added. PR CI run 39120018235 PASS (backend suite and journal frontend build/tests). Next: merge after exact-head CI, confirm Railway SUCCESS for merged commit and inspect sanitized live auth source; do not presume token fixed until live verification.

- v3.6.7 Railway usage step diagnosis (branch `claude/usage-step-diagnosis-v367`): each Railway field queried separately; only a failed `me { id }` token check reports token_unauthorized, later denials report permission_denied with the fixed field name; denied/failed estimatedUsage keeps verified month-to-date cost as partial with projection null ("Projection not available from Railway"); workspace scope falls back to project scope on denial; failures cached 2 minutes; `?refresh=1` bypasses cache once per minute per owner; startup probe logs fixed step/code identifiers only. Full backend pytest 402 passed; new regressions fail on 99e1a1d. Next: confirm Railway exact-commit SUCCESS, then frontend release sending `?refresh=1` from Try again and showing permission_denied/projectionNote.

- v3.6.6 Railway usage contract: PR #234 branch `codex/usage-precision-partial-v366`, CI 39093491729 PASS. Safe reason codes, verified partial metrics, known subtotal and genuine-zero distinction; no Railway token, environment variable or stored data changed. Next: merge backend; verify Railway exact-commit SUCCESS before frontend release.

- Hosting usage workspace repair: Railway usage now resolves the workspace containing the configured project and uses the official workspace-scoped usage/estimate queries while filtering every row back to the project. Synthetic regression coverage and full backend suite: 387 passed. Release candidate ready for PR/CI; deploy backend before frontend v3.6.3.\n
- Hosting usage GraphQL compatibility: PR https://github.com/load66/Trading-Journal-AI/pull/232 now uses project-scoped Railway resource metrics and Railway-provided estimatedUsage; unavailable projections remain null, never zero. CI https://github.com/load66/Trading-Journal-AI/actions/runs/39084713593 PASS (backend and journal frontend). Pending merge and Railway exact-commit verification; no token or variables changed.

- BonusTracker v3.6.0 Hosting & usage backend: PR https://github.com/load66/Trading-Journal-AI/pull/231 adds owner-only read-only endpoint and one-hour caching, mock-only Railway/R2 tests, and cloud snapshot threshold allowlist. CI run https://github.com/load66/Trading-Journal-AI/actions/runs/39083721730 PASS (backend tests and journal build). No Railway token or variables changed. Pending merge and Railway exact-commit production verification; frontend remains gated until that succeeds.

- Release checkpoint (2026-10-17): Finance backend retired via PR #229, merge `c491332a7e4f51def3cd4486b7d204b9ab2c13e5`; Railway production `api` deployment SUCCESS on exact commit (deployment `aae2f683-7d78-4765-b2b2-aac9f4a08a6a`).
- Backend main CI https://github.com/load66/Trading-Journal-AI/actions/runs/39078861129 PASS: full pytest 377 passed; trading-journal frontend build/tests PASS.
- Frontend companion PR https://github.com/load66/bonus-tracker/pull/162 merged; its main-branch CI and GitHub Pages build/deploy passed on merge `28d110b3330da4d0d3e6a8b00c3984ba0f248e6e`.
- Owner separately authorized retirement of the optional provider-backed Tracker activity review. Canonical bank-bonus tracker, cloud recovery, vehicle recovery, journal and permanent Plaid quota ledger preserved.
- Remaining owner-only task: execute authenticated Finance R2 dry-run/typed-confirmation cleanup per `docs/FINANCE_RETIREMENT_RUNBOOK.md`. The agent DID NOT execute it; no Railway variables were modified or provider API calls made.
- Verification gap: live Pages `sw.js` fetch could not be completed from this runtime; main-branch Pages deployment is green. No production storage deletion has been asserted.
- Status: implementation/release complete; no further agent-owned tasks. Historical handoff entries below are superseded.


- Owner superseding decision (2026-10-17): abandon Finance/Plaid enhancement queue; Finance-removal inventory and guarded owner-action sequence on `codex/remove-finance`.
- Phase: owner confirmed manual Plaid/SimpleFIN revocations and separately authorized retiring Tracker bank-activity review in chat (2026-10-17). Backend removal plus owner-only purge API implemented on branch; **R2 purge was not executed**.
- PRs abandoned without merge: #228, #210, #200, #193, #191, #187, #186.
- Next step: verify final full pytest/backend build, merge backend and confirm Railway SUCCESS on exact commit. Then frontend full units/browser release; owner alone may invoke guarded R2 purge.


- Categories v2 backend v3.5.0: PR #224 merged; Railway SUCCESS on ed0d5314e7e5adfb367466b361995728c2ea3d7d
- Case-insensitive P2P regression: PR #227, failing-before CI 39072698485, passing backend/build CI 39072838689
- Handoff: P2P backend release candidate ready for merge/Railway exact-commit verification; frontend PR #157 CI 39072801255 PASS with browser matrix
- Next step: verify PR #227 Railway deployment, then merge and audit frontend PR #157 on Pages; follow with codex/plaid-fast-sync

- In progress: Categories v2 P2P case regression follow-up on codex/finance-categories-v2-p2p-fix; backend v3.5.0 deployed, frontend PR #157 awaiting browser CI
- Next step: verify failing regression, apply conservative matcher fix, green CI, backend Railway exact-commit check, frontend PR #157 release

- Deployed baseline: SimpleFIN backend v3.4.98, merge 26dc4b2076f89157d6fe8356a90eaa244ac59cc3 (Railway SUCCESS)
- Release candidate: Finance Categories v2 backend v3.5.0 for issue #156 on codex/finance-categories-v2
- Backend PR: https://github.com/load66/Trading-Journal-AI/pull/224
- Frontend PR: https://github.com/load66/bonus-tracker/pull/157
- Test status: focused Finance and recovery suites 210 PASS; broad local suite 653 PASS with three unchanged platform checks pending Linux CI
- Safety status: newly verified recovery data wins timestamp ties and retained copies remain bounded
- Next step: push the backend candidate, verify full CI, merge, and confirm Railway SUCCESS on the exact merge commit

## Release 1 (v3.4.94)

- Issue: P1 — Account-scoped category confirmation rules
- Issue: P1 — Prevent false-positive transfer matching
- Test status: PASS — CI run 39046859631

## Finance 3.4.95 mortgage payment classification

- Issue: P1 — Recognize mortgage servicer debits as housing, not issuer transfers
- Issue: P1 — Keep card-payment detection for non-housing movements
- Test status: PASS — Backend Tests and Frontend Build, CI runs 39049563177 and 39049746533
- PR: https://github.com/load66/Trading-Journal-AI/pull/220
- Deployment status: SUCCESS — Railway deployment 4947f9c2-b3e7-45d7-af5d-13d3fa8dc790

## Release 2 — v3.4.96 Finance accuracy

- Backend PR: https://github.com/load66/Trading-Journal-AI/pull/222
- Frontend PR: https://github.com/load66/bonus-tracker/pull/154
- Issue: https://github.com/load66/bonus-tracker/issues/151
- Issue: P1 — Plaid balances missing persisted observation history
- Issue: P0 — Unverified totals lack runtime invariant checks
- Issue: P1 — Sync errors can conceal missing accounts or reviews
- Issue: P1 — Cross-provider ledger lacks pinned cents baseline
- Test status: Backend 631 tests and build PASS (CI 39053785663); frontend four unit areas and Chromium/Firefox/WebKit PASS (CI 39053638068); final doc-only CI pending
- Deployment: pending complete CI, merge, Railway and GitHub Pages verification

## SimpleFIN add-bank (v3.4.97)

- Issue: https://github.com/load66/bonus-tracker/issues/153
- Backend PR: https://github.com/load66/Trading-Journal-AI/pull/223
- Frontend PR: https://github.com/load66/bonus-tracker/pull/155
- Issue: Guided bank discovery suppressed by cooldown and manual-only return flow
- Issue: Additional-bank Sync selection needs explicit owner Save
- Test status: Synthetic cooldown, quota, history preservation and first-sync tests PASS; backend CI 39056081142 PASS
- Deployment: Pending backend squash merge and exact Railway commit verification
