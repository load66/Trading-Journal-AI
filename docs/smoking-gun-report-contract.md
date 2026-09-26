# Smoking Gun Connected Report Contract

**Version:** 1  
**Date:** 2026-09-26  
**Applies to:** Trading Journal connected ChatGPT generation workflow

## Purpose

This document defines the stable contract between ChatGPT and the Trading Journal for generating and saving Smoking Gun reports without requiring a second broker-file upload.

The Trading Journal database is the canonical source of trade data. ChatGPT reads the selected population from the connected journal, performs the deep audit, and saves one structured report snapshot back to the private report library.

No source brokerage CSV is required in ChatGPT when the same trades are already present in the journal.

## Source Read Contract

Connected analysis reads from:

`journal.trades`

Required scope fields:

- `account_id`
- inclusive `date_from`
- inclusive `date_to`
- optional `filters`

Supported filters in report schema version 1:

```json
{
  "tickers": ["SPY", "QQQ"],
  "instrument_types": ["OPTION"]
}
```

Filter rules:

- ticker values are normalized to uppercase
- instrument-type values are normalized to uppercase
- arrays are canonicalized in stable sorted order
- unsupported filter keys are rejected
- an empty filter object means the full account/date-range population

The source rows used for fingerprinting contain exactly:

- `id`
- `account_id`
- `trade_group`
- `date`
- `ticker`
- `instrument_type`
- `side`
- `gross_pnl`
- `net_pnl`
- `commissions`
- `executions`
- `option_expiry`
- `option_strike`
- `option_type`
- `source`

The `executions` payload is parsed and canonicalized before hashing so JSON object key order does not change the fingerprint.

## Canonical Data Fingerprint

The canonical fingerprint is SHA-256 over a deterministic JSON payload containing:

```json
{
  "account_id": 1,
  "date_from": "2026-09-01",
  "date_to": "2026-09-30",
  "filters": {},
  "trades": []
}
```

Canonical trade rows are sorted by:

1. `account_id`
2. `date`
3. `trade_group`
4. `id`

JSON serialization uses:

- sorted object keys
- compact separators
- UTF-8
- canonicalized execution JSON
- canonicalized filter arrays

The source fingerprint MUST be recomputed from the live journal population immediately before saving a report.

A report whose supplied fingerprint no longer equals the current source fingerprint is rejected as stale.

## Deterministic Report Contract

The deterministic metrics payload is stored in `source_metrics_json`.

The report schema currently includes:

- `meta`
- `scoreboard`
- `matching`
- `trade_ledger`
- `hold_time`
- `position_size`
- `daily_pnl`
- `daily_stop_model`
- `ticker_ranking`
- `behavior`
- `time_analysis`
- `two_traders`
- `projections`

### Version metadata

`meta` must contain:

- `report_schema_version`
- `analytics_engine_version`
- `behavior_version`
- `trade_count`
- `open_position_count`
- `timestamp_coverage`

Current constants:

- report schema: `1`
- analytics engine: `2026.09.26.1`
- behavior version: `2026.09.26.1`

### Scoreboard

The deterministic executive scoreboard is the source of truth for headline metrics.

Expected fields include:

- `net_pnl`
- `gross_pnl`
- `fees`
- `win_rate`
- `profit_factor`
- `avg_winner`
- `avg_loser`
- `reward_risk`
- `max_drawdown`
- `active_days`
- `best_day`
- `worst_day`

AI narrative must not recalculate or replace these values.

### Compact Trade Ledger

The saved `trade_ledger` contains one row per completed trade and intentionally omits the raw execution JSON.

Expected fields:

- `trade_group`
- `date`
- `ticker`
- `instrument_type`
- `side`
- `entry_time`
- `exit_time`
- `hold_sec`
- `entry_size`
- `gross_pnl`
- `commissions`
- `net_pnl`
- `hold_bucket`
- `size_bucket`

The saved ledger must reconcile to deterministic trade count and net P&L.

## AI Output Contract

AI output is stored separately from deterministic source metrics.

### `diagnosis_json`

Recommended shape:

```json
{
  "headline": "Concise diagnostic headline",
  "data": [
    {
      "observation": "Deterministic fact",
      "evidence": "Exact source metric reference"
    }
  ],
  "diagnosis": [
    "Interpretation grounded in the deterministic payload"
  ],
  "limitations": [
    "Missing or thin evidence"
  ]
}
```

### `action_plan_json`

Recommended shape:

```json
[
  {
    "priority": 1,
    "rule": "Mechanical rule",
    "reason": "Why the rule follows from the evidence",
    "observed_impact": -1335.79,
    "validation": "How to test prospectively"
  }
]
```

Rules:

- AI must use the deterministic source payload as authoritative
- AI must not invent P&L, timestamps, stop results, sample sizes, or classifications
- overlapping behavioral counterfactuals must never be summed
- unsupported behaviors must be described as unsupported, not forced into a leak narrative
- thin samples must remain visibly qualified
- scenario projections are descriptive scenarios, not forecasts or guarantees

## Saved Report Table

Connected reports are stored in:

`journal.smoking_gun_reports`

Columns:

- `id`
- `account_id`
- `title`
- `date_from`
- `date_to`
- `generated_at`
- `report_version`
- `analytics_engine_version`
- `behavior_version`
- `analysis_provider`
- `analysis_model`
- `trade_count`
- `gross_pnl`
- `net_pnl`
- `primary_edge`
- `primary_leak`
- `data_fingerprint`
- `filters_json`
- `source_metrics_json`
- `diagnosis_json`
- `action_plan_json`
- `export_manifest_json`
- `status`

JSON fields are JSONB in Postgres and JSON text in SQLite local mode.

The logical API shape exposes these as:

- `filters`
- `source_metrics`
- `diagnosis`
- `action_plan`
- `export_manifest`

## Duplicate Prevention

The database uniqueness key is:

```text
(account_id, date_from, date_to, data_fingerprint, report_version)
```

If the same source population and report version already exist, connected generation must return the existing report instead of silently creating another row.

Older reports are immutable snapshots and are not overwritten in place.

## Stale-Report Detection

When the Report Library is read, the journal recomputes the canonical fingerprint for each distinct stored scope.

States:

- `is_stale = false`: source data still matches
- `is_stale = true, stale_reason = "source-data-changed"`: one or more source trade fields changed
- `is_stale = true, stale_reason = "source-data-missing"`: the original source population is no longer present

A stale report remains readable as a historical snapshot, but it is not presented as current.

## Save-Back API Contract

Application endpoint:

`POST /api/smoking-gun-reports`

Required payload:

```json
{
  "account_id": 1,
  "title": "September Smoking Gun",
  "date_from": "2026-09-01",
  "date_to": "2026-09-30",
  "report_version": "1",
  "analytics_engine_version": "2026.09.26.1",
  "behavior_version": "2026.09.26.1",
  "analysis_provider": "openai",
  "analysis_model": "gpt-5.6-sol",
  "data_fingerprint": "<sha256>",
  "filters": {},
  "source_metrics": {},
  "diagnosis": {},
  "action_plan": [],
  "primary_edge": null,
  "primary_leak": null
}
```

Before insert, the Trading Journal:

1. reloads the exact account/date/filter population,
2. rejects an empty population,
3. recomputes the source fingerprint,
4. rejects mismatches with HTTP 409,
5. recalculates stored summary trade count/gross/net values from journal source rows,
6. saves or returns the existing duplicate.

This prevents an old ChatGPT payload from being stored as current after the underlying journal data has changed.

## Direct Connected Supabase Write Contract

When ChatGPT has an authorized Supabase connection, direct save-back may write to the private report table without using the browser-facing API, provided the same invariants are enforced.

Direct connected writes must:

1. read the live source population from `journal.trades`,
2. apply the exact account/date/filter scope,
3. compute the canonical fingerprint using this contract,
4. verify the deterministic report reconciles to the source population,
5. save only a complete structured payload,
6. set `status = 'complete'` only after all required deterministic and AI sections exist,
7. respect the uniqueness key,
8. never overwrite an older report row in place,
9. never expose or request a service-role key in chat,
10. use the user's authorized connected Supabase session.

The `journal` schema is private and is not exposed merely to make this workflow work.

## Read / Export Endpoints

- `GET /api/smoking-gun-reports`
- `GET /api/smoking-gun-reports/{report_id}`
- `DELETE /api/smoking-gun-reports/{report_id}`
- `GET /api/smoking-gun-reports/{report_id}/report.html`
- `GET /api/smoking-gun-reports/{report_id}/trade-ledger.csv`

List responses are metadata-only. Heavy JSON payloads load only when one report is opened.

HTML and CSV are generated from the saved structured snapshot on demand.

## One-Line ChatGPT Workflow

Examples:

- `Generate Smoking Gun.`
- `Generate Smoking Gun for September.`
- `Generate YTD Smoking Gun.`
- `Generate Smoking Gun for SPY and QQQ only.`
- `Generate options-only Smoking Gun for September.`

For a filtered report, the report title and diagnosis must clearly disclose the narrower population.

The requested filter population is persisted in `filters_json` and participates in the data fingerprint, so filtered reports remain independently stale/duplicate-safe.

## Privacy and Security

- no report payloads are committed to GitHub
- no personal account identifiers are hardcoded into source files
- no browser code contains database/service credentials
- RLS remains enabled on the private report table
- `anon` and `authenticated` have no direct table privileges
- the app backend or authorized connected tooling performs report writes
- public/shareable reports require a separate explicit sharing design
