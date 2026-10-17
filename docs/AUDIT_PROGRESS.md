# Audit progress

Active agent: codex — Release 2 Accuracy & Reliability — branch codex/finance-accuracy

Public-safe fields only: versions, commit hashes, PR links, sanitized issue titles, test status.

## Handoff

- Deployed: backend v3.4.95, main commit 3f4e0980c3a758cf00340704ffbdbab5d762537c (Railway SUCCESS)
- In progress: Release 2 Finance v3.4.96 backend accuracy PR #222, frontend PR #154
- Next step: finish full CI and browser gates; merge backend first, verify Railway, then frontend Pages production audit

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
- Test status: Backend 631 tests pass; frontend Finance, UI, cloud and bonus tests pass; CI browser/release completion pending
- Deployment: pending complete CI, merge, Railway and GitHub Pages verification
