# One-time Finance R2 retirement — owner operations

This operation is **not automatic**. The owner confirmed provider revocations and the decision to retire Tracker's optional bank-activity review in chat on 2026-10-17. No agent is authorized to invoke the cleanup API.

The new `/api/bonus-tracker/finance-retirement` endpoint remains available solely so the owner can remove residual stored Finance records after the backend is deployed. It requires the same authenticated, authorized owner bearer session as BonusTracker cloud recovery; deployments without required authentication **cannot use it**.

## Owner steps (execute personally)

1. From an authenticated client, request **GET** `/api/bonus-tracker/finance-retirement/dry-run` and inspect `counts`, `total` and `blocked`. **No objects are deleted by GET.** No object keys, account names or transaction content are returned.
2. If `blocked` is nonzero, stop and request a code-level review of the unknown owner-scoped keys. Never broaden prefixes by hand.
3. To proceed, send **POST** `/api/bonus-tracker/finance-retirement/execute` with the same owner's authenticated header and JSON body: `{"phrase":"DELETE FINANCE DATA","count":<dry-run total>,"digest":"<dry-run digest>"}`. The typed phrase must match exactly; the server re-lists and verifies the count **and** object-version fingerprint before deleting any match. This action is permanent and requires your explicit action.
4. The POST checks each object's ETag again, deletes only the allowlisted Finance records, and lists the same owner-specific Finance partitions afterward. It reports `deleted`, `remaining`, `blocked` and `verified`. Repeat the dry run and confirm `total=0` and `blocked=0`.

The route intentionally does not call SimpleFIN or Plaid. It does not expose or log R2 object names, bank details, amounts, token values or account IDs.

## Scope and exclusions

Allowlisted, owner-scoped objects: SimpleFIN state and monthly history, Plaid monthly history, per-owner Plaid activity, Finance preferences and an explicitly named SimpleFIN pre-disconnect subfolder if present. Unknown legacy snapshot locations are **not** guessed or wildcard-deleted; report any unexpected historical partition for a separately reviewed allowlist update.

Permanently excluded: **Plaid Trial global lifetime slot ledger**, cloud recovery backups, official bank-offer verification data, screenshots, trading-journal database and files, vehicle recovery and every other owner's data. The encrypted ledger persists in R2 even though the Finance provider API is removed. Do not rotate or remove `SIMPLEFIN_ENCRYPTION_KEY` or any other Railway environment variable as part of this release.

A prior Plaid in-app Remove action may have revoked the provider Item while leaving local history. This owner-run R2 cleanup covers that retained owner history without contacting Plaid. The SimpleFIN disconnect tombstone is likewise matched and removed only upon owner execution.

Browser-local retired Finance UI preferences are removed by the new BonusTracker frontend. Personal previously exported backup files remain under the owner's control and are not reachable by the cleanup route. The cloud recovery service remains intact and is intentionally not part of this purge.
