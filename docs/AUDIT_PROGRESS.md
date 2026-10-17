# Audit progress

Active agent: codex — Plaid fast sync — codex/plaid-fast-sync

Public-safe fields only: versions, commit hashes, PR links, sanitized issue titles, test status.

## Handoff

- Plaid fast sync backend draft PR #228 remains unmerged: existing-Item-only ES256 key verification, strict non-billable allowlist, persisted per-call 24/day cap, 3-hour Item deadline and 5-minute on-demand guard
- Additional backend work committed: owner-authorized nonblocking sync-now endpoint and global-lease single-Item sync; manual reviews and other Item cursor/deadline data are preserved in synthetic regression tests
- Backend test CI 39076566800 PASS and 39076624842 PASS; build and latest checkpoint verification pending; no live provider calls in tests
- Blocked Categories v2 frontend PR #157: Railway exact-commit success for backend e6d9ce72a130a882ef16e493fa80884b4a6fb4eb not verified
- Next step: implement verified public Plaid webhook with secure burst coalescing and existing-Item webhook registration; finish sync-now tests, frontend, and deployment verification. No merge of draft PR until full green CI.

- In progress: Plaid fast sync backend PR https://github.com/load66/Trading-Journal-AI/pull/228 (draft, not deployed)
- Implemented on branch: ES256 webhook JWT/body verifier and short-lived key cache, exact non-billable allowlist, pure 3-hour polling and 5-minute freshness policy, rolling 24-call budget, persistent Trial-ledger call reservations, 3-hour per-Item deadline
- Regression status: verifier test failed before implementation on CI 39075832179 and passed after on CI 39076021727; policy suite passed CI 39076148049; integrated reservation/deadline suite awaiting final CI
- Remaining before deploy: authenticated-owner single-Item sync, verified public webhook route, burst coalescing, existing-Item registration, owner sync-now, error flags, concurrency tests, frontend and full browser verification
- Release gate: Categories v2 frontend PR #157 remains open because Railway has not reported SUCCESS for backend amendment e6d9ce72a130a882ef16e493fa80884b4a6fb4eb
- Next step: review CI for fast-sync backend reservation/deadline tests, implement scoped sync and public webhook with mocks, reverify Railway before any frontend merge

- Queue: Categories v2 backend amendment PR #227 merged at e6d9ce72a130a882ef16e493fa80884b4a6fb4eb; Railway exact-commit verification pending
- Queue: frontend Categories v2 PR #157 tested and open, merge withheld pending backend production verification
- In progress: Plaid fast sync, backend branch codex/plaid-fast-sync; existing-Item-only, nonbillable endpoint verification and isolated regression tests
- Next step: implement signed webhook verification and scoped Item synchronization, then verify CI; never deploy backend before tests

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
