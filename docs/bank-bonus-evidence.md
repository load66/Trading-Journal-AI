# BonusTracker evidence service — reliability and review upgrade

The fixed public endpoint uses only public discovery titles and approved official bank documents. It has no request parameters, journal database access or account-history input. Existing authentication and calculations are unchanged.

## Source coverage

Four product profiles: Wells Fargo Everyday Checking, U.S. Bank business checking (distinguishing Business Essentials and Platinum), U.S. Bank Smartly checking, and Chase Total Checking. Other products remain unsupported. The registry identifies bank documents; it contains no manually maintained bonus amounts, requirements or offers. A profile match does not establish that an RSS post refers to the same promotion. Headline amount mismatches require review.

All redirects must remain on approved HTTPS hosts. Fees must be fetched as PDFs, not HTML fallback pages. Responses are bounded to 2 MB and PDFs to 100 unencrypted pages. fonttools supports embedded-font decoding. Product-first excerpts are limited to 9,000 characters per document; full normalized text validates quotations.

## Provider reliability and accounting

One shared background job starts on a public read at most once per hour per process. Default cycle budget: four new extractions, reduced to two after observed throttling. BANK_AI_CYCLE_LIMIT can set 1–8. Default daily accounted-token budget: 100,000 UTC; BANK_AI_DAILY_TOKEN_BUDGET can set 1,000–1,000,000. These are conservative app controls, not provider quotas or invoices. Successful calls use provider-reported usage; failed calls retain a conservative character-based reservation. Quota headers expose remaining token capacity when available. No key or organization identifiers are returned.

Groq makes at most two requests per extraction. Temporary 429/5xx failures with Retry-After up to 15 seconds get one retry; longer waits are deferred. Long 429 waits stop other Groq calls in the same cycle. Network failures get one bounded retry. HTTP timeout: 35 seconds. An already configured Anthropic connection may be used as fallback; no provider key is introduced by this release.

Queue order favors unattempted work and then oldest attempts. Public snapshots preserve last-attempt times, retry times and daily accounting across process restarts; memory preserves newer state between publications. Unchanged source hashes and exact offer identities reuse revalidated quotations without AI calls. Incomplete results are retried after 24 hours. Extractor version changes invalidate the old cache. Failed documents are fetched once per cycle, then reused as failures.

## Evidence and changes

Fourteen known fields cover bonus, expiration, availability, eligibility, qualifying activity, funding/hold wording, promo/enrollment codes, payout, maintenance fees, waivers, closure fees, keep-open/forfeiture, companion accounts and companion fees. Each value must appear in its quotation; each quotation must appear in its fetched source. Fee fields require the fee PDF. Keeping an account open and bonus forfeiture can appear in promotion terms. No dates or eligibility are inferred.

Before/after comparisons use grounded field wording for the same offer fingerprint. Changes persist for a seven-day UI review window with their original timestamp. A changed quote or extraction does not prove changed bank policy. No saved entry is updated. New/changed discovery posts may wait a publication cycle because the service reads the previously published feed.

## Verification

Tests cover retry caps and waiting periods, token reservations and daily limits, cache freshness, queue fairness, document-failure reuse, approved host isolation, exact quotation provenance, product matching, version invalidation, same-offer change comparisons and the public-only AI request contract. Run the complete backend suite before deployment. Live provider and independently re-fetched source checks are required before claiming production verification.
