import { useState, useEffect, useCallback, useRef } from 'react';
import { kpisApi, tradesApi, edgeReportApi, goalsApi, smokingGunLibraryApi, excursionApi, tradeManagementAnalysisApi } from '../api';
import DateRangePicker from './DateRangePicker';
import DashboardRender from '../v3/DashboardRender';
import {
  PageHeader, } from './ui';

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmtLong = (d) => {
  if (!d) return '';
  const [y, m, day] = d.split('-');
  return `${MONTHS_SHORT[Number(m) - 1]} ${Number(day)}, ${y}`;
};

// ── Goals Panel ───────────────────────────────────────────────────────────────

const GOAL_FIELDS = [
  { key: 'win_rate', label: 'Trade Win Rate', suffix: '%', step: 1, min: 0, max: 100 },
  { key: 'day_win_rate', label: 'Day Win Rate', suffix: '%', step: 1, min: 0, max: 100 },
  { key: 'profit_factor', label: 'Profit Factor', suffix: '', step: 0.1, min: 0 },
  { key: 'avg_win_loss_ratio', label: 'Win / Loss Size', suffix: '', step: 0.1, min: 0 },
  { key: 'exit_efficiency', label: 'Exit Efficiency', suffix: '%', step: 1, min: 0, max: 100 },
  { key: 'expectancy', label: 'Expectancy ($)', prefix: '$', suffix: '', step: 5, min: 0 },
];

function GoalsPanel({ draft, onChange, onSave, onCancel, accountLabel, saving, error }) {
  return (
    <section className="card" style={{ marginBottom: 20, boxShadow: 'inset 0 0 0 1px var(--accent-line-soft), var(--shadow-card)' }} aria-labelledby="goals-title">
      <div className="panel-head" style={{ marginBottom: 14 }}>
        <div>
          <h2 id="goals-title" className="section-title">Goals</h2>
          <div className="section-sub">Applies to {accountLabel}</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={saving}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={onSave} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
      </div>
      {error && (
        <div className="notice neg" role="alert" style={{ marginBottom: 14 }}>
          Could not save goals: {error}. Your edits are still here, try again.
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
        {GOAL_FIELDS.map(f => (
          <label key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <span className="field-label" style={{ marginBottom: 0 }}>{f.label}</span>
            <input
              type="number"
              step={f.step}
              min={f.min}
              max={f.max}
              value={draft?.[f.key] ?? ''}
              onChange={e => onChange({ ...draft, [f.key]: Number(e.target.value) })}
              style={{ width: '100%' }}
            />
          </label>
        ))}
      </div>
    </section>
  );
}

// ── Dashboard ──────────────────────────────────────────────────────────────────

export default function Dashboard({ accountId, accounts = [], selectedAccountId, onDayClick, onViewSmokingGun }) {
  const [kpis, setKpis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [recentTrades, setRecentTrades] = useState([]);
  const [edgeReport, setEdgeReport] = useState(null);
  const [goals, setGoals] = useState(null);
  const [showGoals, setShowGoals] = useState(false);
  const [goalsDraft, setGoalsDraft] = useState(null);
  const [goalsSaving, setGoalsSaving] = useState(false);
  const [goalsError, setGoalsError] = useState(null);
  const [managementRange, setManagementRange] = useState('30D');
  const [managementKpis, setManagementKpis] = useState(null);
  const [managementEdge, setManagementEdge] = useState(null);
  const [managementError, setManagementError] = useState('');
  const [managementAi, setManagementAi] = useState(null);
  const [managementAiLoading, setManagementAiLoading] = useState(false);
  const [managementAiError, setManagementAiError] = useState('');
  const [latestSmokingGun, setLatestSmokingGun] = useState(null);
  // Bumped after a write so every panel refetches; also drives Retry.
  const [reloadKey, setReloadKey] = useState(0);
  const reload = useCallback(() => setReloadKey(k => k + 1), []);
  // Each effect run takes a ticket; a response that is not the newest is dropped,
  // so a slow reply cannot overwrite a newer account or date selection.
  const kpiRun = useRef(0);
  const recentRun = useRef(0);
  const managementActiveKey = useRef(null);
  const managementBackfillKey = useRef(null);
  const managementAiRun = useRef(0);
  const smokingGunRun = useRef(0);

  useEffect(() => {
    const run = ++kpiRun.current;
    const current = () => run === kpiRun.current;
    setLoading(true);
    setError(null);
    const params = {};
    if (accountId != null) params.account_id = accountId;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;

    kpisApi.get(params)
      .then(r => { if (current()) { setKpis(r.data); setLoading(false); } })
      .catch(e => { if (current()) { setError(e.message); setLoading(false); } });

    edgeReportApi.get(params)
      .then(r => { if (current()) setEdgeReport(r.data); })
      .catch(() => { if (current()) setEdgeReport(null); });
  }, [accountId, dateFrom, dateTo, reloadKey]);

  useEffect(() => {
    const params = {};
    if (accountId != null) params.account_id = accountId;
    goalsApi.get(params).then(r => setGoals(r.data)).catch(() => {});
  }, [accountId]);

  const handleSaveGoals = () => {
    const payload = { ...goalsDraft };
    if (accountId != null) payload.account_id = accountId;
    setGoalsSaving(true);
    setGoalsError(null);
    goalsApi.put(payload)
      .then(r => { setGoals(r.data); setShowGoals(false); })
      .catch(e => setGoalsError(e.response?.data?.detail || e.message))
      .finally(() => setGoalsSaving(false));
  };

  useEffect(() => {
    const run = ++recentRun.current;
    const current = () => run === recentRun.current;
    const recentParams = { limit: 10, closed_only: true, sort_by: 'closed_at_desc' };
    if (accountId != null) recentParams.account_id = accountId;
    tradesApi.list(recentParams)
      .then(r => { if (current()) setRecentTrades(r.data); })
      .catch(() => { if (current()) setRecentTrades([]); });
  }, [accountId, reloadKey]);


  const buildManagementParams = useCallback(() => {
    const params = {};
    if (accountId != null) params.account_id = accountId;

    const latestTradeDate = recentTrades?.[0]?.date
      || kpis?.daily_pnl?.[kpis.daily_pnl.length - 1]?.date
      || null;

    if (managementRange !== 'ALL' && latestTradeDate) {
      const daysBack = managementRange === '7D' ? 6 : managementRange === '90D' ? 89 : 29;
      const [y, m, d] = latestTradeDate.split('-').map(Number);
      const start = new Date(Date.UTC(y, m - 1, d));
      start.setUTCDate(start.getUTCDate() - daysBack);
      params.date_from = start.toISOString().slice(0, 10);
      params.date_to = latestTradeDate;
    }
    return params;
  }, [accountId, managementRange, recentTrades, kpis]);

  useEffect(() => {
    const params = buildManagementParams();
    const requestKey = [
      accountId == null ? 'all' : accountId,
      managementRange,
      params.date_from || '',
      params.date_to || '',
      reloadKey,
    ].join('|');
    managementActiveKey.current = requestKey;

    setManagementError('');

    const loadManagement = () => Promise.all([
      kpisApi.get(params)
        .then(r => ({ ok: true, data: r.data }))
        .catch(() => ({ ok: false, data: null })),
      edgeReportApi.get(params)
        .then(r => ({ ok: true, data: r.data }))
        .catch(() => ({ ok: false, data: null })),
    ]);

    const applyIfActive = ([kpiResult, edgeResult]) => {
      if (managementActiveKey.current !== requestKey) return;
      setManagementKpis(kpiResult.data);
      setManagementEdge(edgeResult.data);
      if (!kpiResult.ok && !edgeResult.ok) {
        setManagementError('Selected-window performance and timing metrics are unavailable.');
      } else if (!kpiResult.ok) {
        setManagementError('Selected-window performance metrics are unavailable.');
      } else if (!edgeResult.ok) {
        setManagementError('Selected-window timing metrics are unavailable.');
      } else {
        setManagementError('');
      }
    };

    // Load broker-derived and timing metrics independently. One failed endpoint
    // must never make a selected 7D/30D/90D window silently fall back to all-time.
    loadManagement().then(applyIfActive);

    if (params.date_from && params.date_to) {
      const backfillKey = requestKey;
      if (managementBackfillKey.current !== backfillKey) {
        managementBackfillKey.current = backfillKey;
        excursionApi.calculateRange(params)
          .then(() => loadManagement())
          .then(applyIfActive)
          .catch(() => {
            if (managementBackfillKey.current === backfillKey) {
              managementBackfillKey.current = null;
            }
          });
      }
    }
  }, [accountId, managementRange, buildManagementParams, reloadKey]);

  useEffect(() => {
    const run = ++managementAiRun.current;
    setManagementAi(null);
    setManagementAiError('');
    setManagementAiLoading(false);

    // Restore a previously generated diagnosis after refresh/account/range
    // changes without triggering a fresh provider request. The backend only
    // returns cached content when its evidence fingerprint still matches.
    const params = {
      ...buildManagementParams(),
      range: managementRange,
      cached_only: true,
    };
    tradeManagementAnalysisApi.get(params)
      .then((response) => {
        if (run !== managementAiRun.current) return;
        if (response.data?.cached && response.data?.diagnosis) {
          setManagementAi(response.data);
        }
      })
      .catch(() => {
        // Cache restore is opportunistic. Deterministic cards remain usable
        // and Generate can still be clicked if the cache probe is unavailable.
      });
  }, [accountId, managementRange, buildManagementParams, reloadKey]);

  const handleManagementAi = useCallback(async (force = false) => {
    const run = ++managementAiRun.current;
    const params = {
      ...buildManagementParams(),
      range: managementRange,
    };
    if (force) params.force = true;

    setManagementAiLoading(true);
    setManagementAiError('');
    try {
      const response = await tradeManagementAnalysisApi.get(params);
      if (run !== managementAiRun.current) return;
      setManagementAi(response.data);
      if (response.data?.unavailable) {
        setManagementAiError(response.data?.diagnosis || 'AI management review is unavailable.');
      }
    } catch (e) {
      if (run !== managementAiRun.current) return;
      setManagementAiError(
        e?.response?.data?.detail
          || e?.response?.data?.error
          || e?.message
          || 'Could not generate the AI management review.'
      );
    } finally {
      if (run === managementAiRun.current) setManagementAiLoading(false);
    }
  }, [buildManagementParams, managementRange]);

  useEffect(() => {
    const run = ++smokingGunRun.current;
    const current = () => run === smokingGunRun.current;
    const params = accountId == null ? {} : { account_id: accountId };

    smokingGunLibraryApi.list(params)
      .then(async (response) => {
        const latest = (response.data || [])[0];
        if (!latest) return null;
        const detail = await smokingGunLibraryApi.get(latest.id);
        return detail.data;
      })
      .then((report) => {
        if (current()) setLatestSmokingGun(report || null);
      })
      .catch(() => {
        if (current()) setLatestSmokingGun(null);
      });
  }, [accountId, reloadKey]);

  const accountLabel = (() => {
    const a = accounts.find(x => x.id === selectedAccountId);
    return a ? a.name : 'All Accounts';
  })();

  const { daily_pnl = [] } = kpis || {};
  const span = daily_pnl.length
    ? `${fmtLong(daily_pnl[0].date)} to ${fmtLong(daily_pnl[daily_pnl.length - 1].date)}`
    : null;

  if (error) return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle={accountLabel}
        actions={<DateRangePicker
          dateFrom={dateFrom}
          dateTo={dateTo}
          onChange={({ dateFrom: f, dateTo: t }) => { setDateFrom(f); setDateTo(t); }}
        />}
      />
      <div className="notice neg" role="alert" style={{ alignItems: 'center' }}>
        <span style={{ flex: 1 }}>Could not load the dashboard: {error}. No trades were changed.</span>
        <button type="button" className="btn btn-secondary btn-sm" onClick={reload}>Retry</button>
      </div>
    </div>
  );

  return (
    <div>
      {loading ? (
        <div className="v3-band"><div className="v3-empty">Loading…</div></div>
      ) : (
        <DashboardRender
          kpis={kpis}
          goals={goals}
          accountLabel={accountLabel}
          span={span}
          dateFrom={dateFrom}
          dateTo={dateTo}
          accountId={accountId}
          onDayClick={onDayClick}
          edgeReport={edgeReport}
          managementRange={managementRange}
          onManagementRangeChange={setManagementRange}
          managementKpis={managementKpis}
          managementEdge={managementEdge}
          managementError={managementError}
          managementAi={managementAi}
          managementAiLoading={managementAiLoading}
          managementAiError={managementAiError}
          onManagementAiGenerate={handleManagementAi}
          latestSmokingGun={latestSmokingGun}
          onViewSmokingGun={onViewSmokingGun}
          showGoals={showGoals}
          onToggleGoals={() => { setGoalsDraft({ ...goals }); setShowGoals(v => !v); }}
          goalsNode={showGoals ? (
            <GoalsPanel
              saving={goalsSaving}
              error={goalsError}
              draft={goalsDraft}
              onChange={setGoalsDraft}
              onSave={handleSaveGoals}
              onCancel={() => setShowGoals(false)}
              accountLabel={accountLabel}
            />
          ) : null}
          RangePicker={(
            <DateRangePicker
              dateFrom={dateFrom}
              dateTo={dateTo}
              onChange={({ dateFrom: f, dateTo: t }) => { setDateFrom(f); setDateTo(t); }}
            />
          )}
        />
      )}
    </div>
  );
}
