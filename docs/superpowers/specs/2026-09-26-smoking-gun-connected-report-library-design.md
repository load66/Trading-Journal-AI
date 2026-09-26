# Smoking Gun Connected Report Library — Design

**Date:** 2026-09-26  
**Status:** Design approved in chat; written spec pending user review  
**Repository:** `load66/Trading-Journal-AI`  
**Working branch:** `feature/smoking-gun-ai-report`

## 1. Goal

Turn the Trading Journal into the single source of truth for brokerage data while using ChatGPT as the deep-analysis engine.

The user uploads a brokerage export only once into the Trading Journal. After that, a ChatGPT request such as:

> Generate Smoking Gun for September.

should be able to:

1. read the already-imported trades directly from the connected journal database,
2. analyze them without another CSV upload,
3. generate the full Smoking Gun audit,
4. save the finished report back into the Trading Journal,
5. expose that report immediately under **Reports → Smoking Gun → Report Library**,
6. allow opening and downloading the report from phone or PC.

No personal trading data, account identifiers, report payloads, or generated artifacts are committed to the public GitHub repository.

---

## 2. Product Standard

The Smoking Gun area must look and behave like a professional trader performance terminal.

Design principles:

- **Data first.** Important numbers appear before AI prose.
- **Fast scanability.** A trader should understand the current state in under 10 seconds.
- **No dashboard clutter.** Every card must answer a trading-performance question.
- **Consistent hierarchy.** Edge, leaks, risk, behavior, timing, and action items are visually distinct.
- **Accurate labels.** Never label a behavior as a problem unless the data supports it.
- **Dollar impact matters.** Behavioral findings should emphasize observed P&L impact, not generic coaching.
- **Thin samples are clearly marked.**
- **Deterministic metrics and AI interpretation never mix.**
- **Mobile usable.** The report must remain readable on a phone without losing essential information.
- **Responsive and fast.** Large historical reports must not freeze the page.
- **Dark professional aesthetic.** Use the journal's existing dark system, restrained accents, compact typography, and financial-terminal density.
- **No gimmicks.** Avoid decorative AI gradients, oversized marketing cards, excessive animations, or consumer-app styling.

---

## 3. Current Verified Architecture

The project is already farther along than a greenfield implementation:

- Backend supports SQLite and Postgres.
- Supabase project `trading-journal-ai` is active and healthy.
- The private `journal` schema already exists.
- `journal.trades` is already populated.
- Existing shared dataset currently contains 191 reconstructed trades.
- The stored range currently spans 2026-08-18 through 2026-09-25.
- Existing stored net P&L reconciles to 4340.34.
- RLS is enabled on existing journal tables.
- Existing code already contains:
  - `backend/performance_report.py`
  - `/api/smoking-gun-report`
  - `/api/smoking-gun-diagnosis`
  - `frontend/src/components/SmokingGunReport.js`
  - a Smoking Gun tab inside Reports.

Therefore this work is an extension of the existing system, not a replacement.

---

## 4. Source-of-Truth Model

### 4.1 Trades

The canonical dataset is the journal database, not uploaded files inside ChatGPT.

ChatGPT analysis reads from the same trade records the Trading Journal uses.

The primary shared source is:

`journal.trades`

with `executions` retained as the detailed execution payload needed for hold-time, scaling, and behavioral reconstruction.

### 4.2 Deterministic vs AI responsibilities

The system must enforce a strict separation.

#### Deterministic engine owns

- matched completed trades
- unmatched/open positions
- gross P&L
- commissions and fees
- net P&L
- hold durations
- position sizes
- daily P&L
- running P&L
- drawdowns
- blow-up days
- daily stop simulations
- ticker statistics
- behavior cohorts
- time-of-day statistics
- day-of-week statistics
- projections
- report fingerprints
- sample-size classifications

#### ChatGPT owns

- diagnosis
- prioritization
- interpretation
- explanation of conflicting evidence
- mechanical improvement plan
- narrative summary
- report headline

ChatGPT must never be treated as the calculator for source metrics.

---

## 5. Smoking Gun Report Library

Add a permanent library inside:

**Reports → Smoking Gun**

The page has two top-level modes:

### A. Live Analytics

Current deterministic analytics generated from the selected account/date range.

This remains useful even when no ChatGPT report has been generated.

### B. Report Library

Historical deep-audit reports generated through ChatGPT.

Each report card shows:

- report title
- account
- analyzed date range
- generation timestamp
- trade count
- net P&L
- primary edge
- primary leak
- current drawdown if applicable
- report version
- data fingerprint status

Primary actions:

- **Open Report**
- **Download HTML**
- **Download Trade Ledger**
- **Compare**
- **Delete** only from a deliberate secondary action

Newest reports appear first.

---

## 6. Professional Trader Report Layout

Opening a saved report should use this hierarchy.

### 6.1 Command Header

Compact top strip containing:

- report title
- account
- date range
- generated date/time
- total trades
- data fingerprint
- report version
- status: Current / Underlying trades changed

This section should be dense and compact, similar to a professional analytics terminal.

### 6.2 Executive Scoreboard

First viewport should show only the highest-value metrics:

- Net P&L
- Win rate
- Profit factor
- Average winner
- Average loser
- Reward/risk
- Max drawdown
- Active trading days
- Best day
- Worst day

No AI commentary above this scoreboard.

### 6.3 Two Traders

Prominent split panel:

**Disciplined Trader**
- trade count
- P&L
- win rate
- $/trade

**Destructive Trader**
- trade count
- P&L
- win rate
- $/trade

This is one of the report's primary visual anchors.

### 6.4 Edge Map

Shows where performance improves or deteriorates across:

- hold duration
- position size
- hold × size interaction
- time of day
- day of week
- ticker

Positive edge uses restrained green treatment.
Negative edge uses restrained red treatment.
Neutral/thin evidence uses muted gray/amber.

### 6.5 Hold-Time Analysis

Use the exact required buckets:

- Under 30 sec
- 30s–1min
- 1–2min
- 2–5min
- 5–10min
- 10–15min
- 15–20min
- 20–30min
- 30–60min
- 60min+

Each row shows:

- trades
- total P&L
- win rate
- average P&L
- optional confidence/sample indicator

### 6.6 Position Size

Option buckets:

- 1–3
- 4–5
- 6–10
- 11–15
- 16–20
- 21–25
- 26–30
- 31–50
- 50+

Shares use notional/share-count logic appropriate to available data.

Also retain normalized sizing relative to the trader's own typical size for behavioral analysis.

### 6.7 Daily P&L and Drawdown

Show:

- options P&L
- shares P&L
- futures P&L when applicable
- total daily P&L
- running P&L
- trade count
- blow-up flag
- drawdown

Chart interactions should remain lightweight and responsive.

### 6.8 Daily Stop Lab

Model:

- 1× average losing trade
- 2× average losing trade
- 3× average losing trade
- $250
- $500
- $750
- $1,000
- additional sensible candidates when appropriate

For each stop:

- adjusted P&L
- dollar savings/loss
- breach days
- exact breach-day details
- recovery days harmed by the stop

The UI must explicitly distinguish:

- **saved loss**
- **cut off a recovery**

This prevents overfitting from being presented as certainty.

### 6.9 Ticker Ranking

Each ticker shows:

- trade count
- P&L
- win rate
- $/trade
- sample quality
- status

Status vocabulary:

- EDGE
- MARGINAL
- LEAK
- BLEEDING
- HEMORRHAGE

Classification logic remains deterministic and versioned.

### 6.10 Behavioral Forensics

Analyze:

- revenge re-entry depth
- overtrading
- incremental trades beyond normal daily count
- tilt sizing
- rapid re-entry / FOMO
- premature-exit proxy
- averaging down
- first 10 minutes
- first 30 minutes
- last 30 minutes
- winner/loser asymmetry

Each behavior card must show:

- definition
- flagged trade count
- observed P&L
- win rate when meaningful
- dollar impact
- P&L if removed
- evidence status

Evidence statuses:

- **Confirmed leak**
- **Possible leak**
- **Not supported as a leak**
- **Insufficient evidence**

Behavior counterfactual impacts may overlap and must never be summed.

### 6.11 Time Analysis

Use 30-minute blocks.

Also show:

- first 10 minutes vs rest
- first 30 minutes vs middle
- last 30 minutes
- weekday results

Charts and tables should support quick comparison without forcing horizontal scrolling on mobile.

### 6.12 Projection Scenarios

Projection scenarios are descriptive, not promises.

Display:

- 100%
- 75%
- 50%
- 33%
- 25%

of the historically observed disciplined daily edge.

Each scenario shows:

- daily edge
- remaining eligible sessions
- projected gross
- current drawdown
- projected amount after current drawdown
- estimated recovery trading days

Label this section **Scenario Model**, not Forecast.

### 6.13 Diagnosis

Only after all evidence sections.

Structure:

**DATA**
- strongest facts

**DIAGNOSIS**
- what is actually driving performance
- what is not supported
- apparent contradictions explained

**FIX**
- ranked mechanical rules
- measurable rule
- reason
- supporting dollar impact
- validation period

The tone should be concise and analytical, not motivational.

---

## 7. Saved Report Data Model

Add a dedicated report table inside the private `journal` schema.

Suggested table:

`journal.smoking_gun_reports`

Fields:

- `id`
- `account_id`
- `title`
- `date_from`
- `date_to`
- `generated_at`
- `report_version`
- `analysis_model`
- `trade_count`
- `gross_pnl`
- `net_pnl`
- `primary_edge`
- `primary_leak`
- `data_fingerprint`
- `source_metrics_json`
- `diagnosis_json`
- `action_plan_json`
- `export_manifest_json` for export metadata only

HTML and CSV are generated on demand from the saved structured report. Large generated artifacts are not stored inline in Postgres by default. If persistent file retention is added later, the files belong in authenticated object storage and the table stores only their references.
- `status`

Use JSONB for structured report payloads.

Do not duplicate the complete trade database inside each report.

---

## 8. Data Fingerprint

Each generated report must include a deterministic fingerprint of its underlying source dataset.

The fingerprint should include stable fields such as:

- account
- selected date range
- trade IDs/groups
- execution payload
- net/gross P&L
- commissions

Purpose:

- detect duplicate generation
- detect stale reports
- tell the user when underlying trades changed
- support comparison integrity

If the same fingerprint and report version already exist, the journal should offer the existing report rather than silently creating a duplicate.

---

## 9. ChatGPT Connected Workflow

Target workflow:

1. User imports Schwab/TOS/broker data into Trading Journal.
2. Journal parses and persists the trades in Supabase.
3. User opens ChatGPT and says:
   - “Generate Smoking Gun.”
   - “Generate September Smoking Gun.”
   - “Generate YTD.”
   - “Generate only SPY/QQQ.”
4. ChatGPT reads the appropriate connected journal data through the user's authorized Supabase connection.
5. ChatGPT computes or verifies the full analysis.
6. ChatGPT creates the deep diagnosis and action plan.
7. ChatGPT writes the completed structured report directly to the private report-library table through that authorized connection.
8. Trading Journal immediately shows it in Report Library.
9. User can open/download the report from phone or PC.

A brokerage file is not uploaded twice.

---

## 10. Accuracy Rules

The report is considered invalid if any of these fail:

- completed trade count does not reconcile
- report P&L does not reconcile to stored trade P&L
- open/unmatched positions are silently excluded
- hold-time totals do not equal timestamp-eligible trades
- position-size totals do not reconcile
- daily totals do not sum to overall P&L
- AI changes deterministic values
- counterfactual behavior impacts are added together
- thin samples are presented as strong evidence
- projections are presented as guarantees
- timezone assumptions are hidden

All calculations must be testable independently of the AI narrative.

---

## 11. Performance Requirements

The report must feel fast on both desktop and mobile.

Requirements:

- deterministic report endpoint should avoid repeated parsing of the same execution JSON within one request
- expensive derived data should be calculated once and reused
- report library list should return summary metadata only
- full JSON payload loads only when a report is opened
- large tables should use pagination, virtualization, or collapsed sections when needed
- charts should receive already-aggregated data
- avoid embedding multi-megabyte Plotly bundles inside the normal app route
- Recharts or the project's existing chart library remains preferred for live rendering
- HTML export is generated on demand rather than loaded into the main page
- loading states should preserve page layout
- mobile uses stacked cards and horizontally constrained tables only where necessary

Target experience:

- Report Library appears immediately from metadata.
- Opening a typical report should feel near-instant on a modern desktop.
- The app must remain usable with thousands of stored trades.

---

## 12. Security and Privacy

The `journal` schema remains private.

Requirements:

- no service-role key in frontend code
- no personal trading data in GitHub
- no generated report payloads in GitHub
- RLS remains enabled
- report access follows the same ownership/access model as journal trades
- backend or trusted connected tooling performs privileged report writes
- user-facing browser access never bypasses authorization
- the private `journal` schema is not made public merely to support this feature
- app reads/downloads continue through the authenticated Trading Journal backend
- connected ChatGPT access uses the user's authorized Supabase connection rather than a browser-exposed service credential
- any future public/shareable report requires a separate explicit sharing design

---

## 13. Report Versioning

The report must save:

- analytics engine version
- behavior-definition version
- report schema version
- AI model/provider identifier

This protects historical comparisons if formulas evolve later.

Old reports remain readable and are not silently recalculated.

---

## 14. Comparison Readiness

Do not build the full comparison engine in the first implementation unless it falls naturally out of the report schema.

However, the saved schema must make future comparison possible.

Future comparison should support:

- P&L change
- win-rate change
- hold-time improvement
- sizing improvement
- behavioral leak change
- daily stop impact change
- ticker edge migration
- disciplined vs destructive split change

This is why report data must be structured, not stored only as HTML.

---

## 15. Error Handling

If the Trading Journal has no trades for the requested range:

- no report is generated
- clear message explains there is no data

If timestamps are missing:

- timestamp-dependent sections show reduced coverage
- other valid sections still render

If ChatGPT generation fails:

- deterministic analytics remain available
- no partial report is marked complete
- retrying must not create duplicates

If report save-back fails:

- generated report remains recoverable in the active ChatGPT session
- journal status must not claim it was saved

---

## 16. Testing Standard

Tests must cover:

- exact hold bucket boundaries
- exact position-size bucket boundaries
- long and short reconstruction compatibility
- open/unmatched positions
- fee/net P&L reconciliation
- daily running P&L
- blow-up detection
- daily stop cutoff behavior
- averaging-down detection
- revenge depth logic
- rapid re-entry logic
- first-10-minute logic
- disciplined/destructive cohort split
- data fingerprint stability
- duplicate report prevention
- report library list/open behavior
- stale-report detection after trade mutation
- mobile rendering regression for key report sections

The deterministic engine must be testable without calling Groq, OpenAI, Anthropic, or any other AI provider.

---

## 17. First Implementation Scope

The first shippable version includes:

1. saved Smoking Gun report table,
2. report fingerprinting,
3. report create/read/list APIs,
4. native Report Library UI,
5. professional report detail page,
6. saving structured ChatGPT report payloads,
7. opening and downloading saved reports,
8. stale/duplicate detection,
9. preservation of current live deterministic Smoking Gun analytics.

The first version does **not** require:

- public sharing links,
- automated recurring generation,
- real-time brokerage syncing,
- a full report-to-report comparison engine,
- replacing the existing analytics engine with AI.

Those can be layered on after the report library is stable.

---

## 18. Success Criteria

The feature is successful when the user can:

1. import brokerage data once,
2. ask ChatGPT to generate a Smoking Gun report without re-uploading the file,
3. have ChatGPT read the same trade dataset the journal already uses,
4. save the finished report back into the Trading Journal,
5. see it immediately in an organized professional Report Library,
6. open it quickly on desktop or mobile,
7. download it,
8. trust that every number is reproducible from deterministic source data,
9. clearly distinguish proven edge from proven leaks,
10. build a historical library without duplicate or stale-report confusion.
