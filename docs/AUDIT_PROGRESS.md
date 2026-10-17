# Audit progress

Active agent: none

Public-safe fields only: versions, commit hashes, PR links, sanitized issue titles, test status.

## Handoff

- Deployed: backend v3.4.95, main commit 3f4e0980c3a758cf00340704ffbdbab5d762537c (Railway SUCCESS)
- In progress: SimpleFIN add-bank backend v3.4.97 release candidate, PR #223
- Next step: after full CI merge PR #223, verify Railway SUCCESS on exact merge commit, then frontend PR #155

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
