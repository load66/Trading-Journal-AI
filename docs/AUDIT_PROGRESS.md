# Audit progress

Active agent: codex — verified backend release handoff — branch codex/finance-balances-handoff

Public-safe fields only: versions, commit hashes, PR links, sanitized issue titles, test status.

## Handoff

- Deployed: backend v3.4.94 merged to main at 3d29d2c815f4b8cdd5c8d5700f5115bd6d6e898b (Railway deploy status: to be verified)
- In progress: Release 1 frontend mortgage/loan balance fixes in load66/bonus-tracker
- Next step: verify merged backend v3.4.95 production deployment, then complete frontend regression CI and Pages release

## Release 1 (v3.4.94)

- Issue: P1 — Account-scoped category confirmation rules
- Issue: P1 — Prevent false-positive transfer matching
- Test status: pending verification

## Finance 3.4.95 mortgage payment classification

- Issue: P1 — Recognize mortgage servicer debits as housing, not issuer transfers
- Issue: P1 — Keep card-payment detection for non-housing movements
- Test status: PASS — Backend Tests and Frontend Build, CI run 39049563177
- PR: https://github.com/load66/Trading-Journal-AI/pull/220
- Deployment status: pending merge and Railway commit verification
