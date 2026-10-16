# Plaid Sandbox connectivity verification

This optional startup diagnostic checks provider credentials and Sandbox API
connectivity. It does not verify the complete BonusTracker UI, campaign mapping,
evidence decisions, bank coverage or qualification of any bonus requirement.
It must never set the Production pipeline-ready or linking-enabled flags.

The diagnostic is disabled unless `PLAID_SANDBOX_VERIFY_RUN` contains a short
operator-chosen run label made of letters, numbers, underscores and hyphens.
It uses the existing `PLAID_CLIENT_ID` and separate `PLAID_SANDBOX_SECRET`.
It never reads `PLAID_SECRET`, imports the Production quota ledger, writes to
R2, or returns credentials or provider data through a public endpoint.

It runs in a background thread at service startup, uses the fixed
`https://sandbox.plaid.com` host, disables redirects and proxy environment
configuration, and allows only synthetic Item creation, token exchange,
account retrieval, transaction sync and removal of the synthetic Sandbox Item.
Requests time out after 12 seconds. Transaction polling/pagination is limited
to eight calls, with a short wait for asynchronous Sandbox history. Empty or
incomplete history is reported as pending, never passed. Cleanup failure is
reported as failed. There is no automatic retry of Item creation or exchange.

Read the `PLAID_SANDBOX_CHECK` log record for fixed status/stage names, counts
and cleanup status. No token, identifier, description, credential or provider
error payload is logged. Clear `PLAID_SANDBOX_VERIFY_RUN` after verification
to prevent another test on future service startups. A passed check confirms
only the Sandbox connectivity steps above; Production remains independently
locked until every release gate is satisfied.
