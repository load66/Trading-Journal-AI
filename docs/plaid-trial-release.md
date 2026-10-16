# Plaid Trial fallback operations

Provider order: SimpleFIN, then explicitly acknowledged Plaid fallback, then manual entry.
Never apply for full Production access: this permanently removes Trial eligibility.

## Release controls
Both `PLAID_TRIAL_ENABLED` and `PLAID_TRIAL_PIPELINE_READY` must be true, with
verified plan, baseline and configured server credentials. The encrypted global
R2 ledger records the whole Plaid team lifetime usage, independently of user,
server restarts, deployments and environment baseline changes. Baseline zero
was confirmed from the user's Trial Dashboard before activation.

Link-token issuance and public-token exchange both check capacity. Conditional
R2 writes reserve capacity before provider calls, preventing concurrent oversubscription.
Only a successful exchange increments used; no deletion flow decrements it.
Issued and ambiguous reservations never expire automatically: Link creates an
Item before the callback reaches the backend. Reconcile an interrupted attempt
against Plaid Dashboard lifetime usage before changing any reservation manually.
Never reduce a successful lifetime count. Keep Dashboard access limited to the
account owner; no application can stop an operator upgrading outside this app.

## Account activity
Discovery reads account metadata; only explicitly monitored accounts retain
transaction history. Transactions use atomic paginated cursors and one daily
provider budget per owner, including failed attempts. Deselection removes saved
transactions, campaign mappings and reviews. Scheduled reads never create Items.
Raw credentials and provider identifiers never reach the browser. Private API
responses are no-store, owner authenticated and encrypted at rest.

Campaign mapping uses the exact Tracker entry, bank, opening date and optional
verified product. Review receipts are revision-bound and never mark bonus
requirements or payouts complete. Changed and removed transactions lose their
receipts. SimpleFIN and Plaid are selected separately in Finances and Tracker;
the app does not combine potentially overlapping provider totals.

## Verification and rollback
Backend quota, authorization, persistence, revisions and consent tests run with
synthetic fixtures. Chromium, Firefox and WebKit browser tests cover mobile
controls, mapping and persisted evidence review without real bank login.
The separate Sandbox diagnostic verifies actual provider accounts, transaction
sync and cleanup without consuming a Production slot.

Deploy backend and frontend before activation. Confirm the exact main commit
and Pages asset hashes, then activate the two switches. To disable new linking
and provider reads set `PLAID_TRIAL_ENABLED=false`; preserve the ledger and key.
A real institution login remains the user's explicit in-app action. Provider
institution support, consent or OAuth prerequisites may still vary by bank.
