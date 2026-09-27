import { useState, useEffect, useRef, useCallback } from 'react';
import {
  ChevronLeft, ChevronRight, RotateCcw, Calendar,
  AlertTriangle
} from 'lucide-react';
import { tradesApi, kpisApi, diaryApi, dailySummaryApi, excursionApi } from '../api';
import { PageHeader, PanelHead } from './ui';
import { DayCurve, DayMeasures, Coaching, DayTrades } from '../v3/ReviewParts';
import { tradeMarketHour } from '../tradeMetrics';
import {
  BarChart, Bar, XAxis, YAxis, ReferenceLine,
  Tooltip, ResponsiveContainer, Cell
} from 'recharts';

// ── date helpers ──────────────────────────────────────────────────────────────
function prevTradingDay(iso) {
  const d = new Date(iso + 'T12:00:00');
  d.setDate(d.getDate() - 1);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
  return d.toISOString().split('T')[0];
}
function nextTradingDay(iso) {
  const d = new Date(iso + 'T12:00:00');
  d.setDate(d.getDate() + 1);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1);
  return d.toISOString().split('T')[0];
}
function formatDateLabel(iso) {
  const d = new Date(iso + 'T12:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
}

function summaryErrorMessage(error) {
  return error?.response?.data?.detail
    || error?.response?.data?.error
    || error?.message
    || 'The AI diagnosis could not be generated.';
}

// ── R-Multiple chart ──────────────────────────────────────────────────────────
function RMultipleChart({ trades }) {
  const data = trades.filter(t => t.r_multiple != null).map(t => ({
    name: t.ticker,
    r: Number(t.r_multiple),
  }));
  if (!data.length) return null;
  return (
    <section className="card">
      <PanelHead title="R-Multiple by Trade" />
      <ResponsiveContainer width="100%" height={140}>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <XAxis dataKey="name" tick={{ fontSize: 12, fill: 'var(--text-secondary)' }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fontSize: 11, fill: 'var(--text-secondary)' }} axisLine={false} tickLine={false} />
          <ReferenceLine y={0} stroke="var(--divider)" />
          <Tooltip
            cursor={{ fill: 'var(--accent-soft)' }}
            contentStyle={{ background: 'var(--surface-panel)', border: '1px solid var(--divider)', borderRadius: 6, fontSize: 13, color: 'var(--text-primary)' }}
            formatter={(v) => [`${v.toFixed(2)}R`, 'R-Multiple']}
          />
          <Bar dataKey="r" radius={[4, 4, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.r >= 0 ? 'var(--green)' : 'var(--red)'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  );
}

// ── Main component ────────────────────────────────────────────────────────────
export default function DailySummary({ accountId, date, onDateChange, onOpenDetail }) {
  const [trades, setTrades] = useState([]);
  const [kpis, setKpis] = useState(null);
  const [, setDiary] = useState(null);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState('');
  const [regenerating, setRegenerating] = useState(false);
  const [cbDismissed, setCbDismissed] = useState(false);
  const allTimeKpisRef = useRef(null);
  const dayRequestRef = useRef(0);
  const summaryInFlightRef = useRef(null);

  const today = new Date().toISOString().split('T')[0];

  const fetchAllTimeKpis = useCallback(async () => {
    if (allTimeKpisRef.current) return;
    try {
      const params = {};
      if (accountId != null) params.account_id = accountId;
      const res = await kpisApi.get(params);
      allTimeKpisRef.current = res.data;
    } catch (e) {
      console.error('Failed to load all-time kpis', e);
    }
  }, [accountId]);

  const fetchSummary = useCallback(async (d, { force = false } = {}) => {
    const params = { date: d };
    if (force) params.force = true;
    if (accountId != null) params.account_id = accountId;

    const key = `${accountId ?? 'all'}:${d}:auto`;
    if (!force && summaryInFlightRef.current?.key === key) {
      return summaryInFlightRef.current.promise;
    }

    const promise = dailySummaryApi.get(params);
    if (!force) summaryInFlightRef.current = { key, promise };

    try {
      return await promise;
    } finally {
      if (!force && summaryInFlightRef.current?.promise === promise) {
        summaryInFlightRef.current = null;
      }
    }
  }, [accountId]);

  const fetchDay = useCallback(async (d) => {
    const requestId = ++dayRequestRef.current;
    setLoading(true);
    setSummaryLoading(true);
    setSummaryError('');
    setSummary(null);
    setCbDismissed(false);

    try {
      const params = { date_from: d, date_to: d, closed_only: true };
      if (accountId != null) params.account_id = accountId;

      // Enrich missing MFE/MAE/exit-efficiency once, then read the day.
      // Failure here must never block the journal; the UI will show insufficient
      // market data instead of inventing excursion metrics.
      try {
        const excursionParams = { date: d };
        if (accountId != null) excursionParams.account_id = accountId;
        await excursionApi.calculate(excursionParams);
      } catch (e) {
        console.warn('Excursion enrichment unavailable', e);
      }

      const allParams = {};
      if (accountId != null) allParams.account_id = accountId;
      const [tradesRes, kpisRes, diaryRes, allKpisRes] = await Promise.all([
        tradesApi.list(params),
        kpisApi.get(params),
        diaryApi.list(accountId != null ? { account_id: accountId } : {}),
        kpisApi.get(allParams),
      ]);

      if (requestId !== dayRequestRef.current) return;

      const rawTrades = [...(tradesRes.data || [])].sort((a, b) => {
        const ax = tradeMarketHour(a, 'exit');
        const bx = tradeMarketHour(b, 'exit');
        if (ax == null && bx == null) return 0;
        if (ax == null) return 1;
        if (bx == null) return -1;
        return ax - bx;
      });
      setTrades(rawTrades);
      setKpis(kpisRes.data || null);
      allTimeKpisRef.current = allKpisRes.data || null;

      const diaryEntries = diaryRes.data || [];
      const dayDiary = diaryEntries.find(e => e.entry_date === d) || null;
      setDiary(dayDiary);
      setLoading(false);

      // Empty sessions never call the AI provider.
      if (!rawTrades.length) {
        setSummary({
          date: d,
          cached: false,
          no_trades: true,
          narrative: 'No completed trades to diagnose for this date.',
        });
        setSummaryLoading(false);
        return;
      }

      // Opening Day Review automatically resolves the current diagnosis.
      // The backend reuses a matching evidence fingerprint and regenerates only
      // when this day's trades/journal evidence changed.
      try {
        const sumRes = await fetchSummary(d);
        if (requestId !== dayRequestRef.current) return;
        setSummary(sumRes.data);
      } catch (e) {
        if (requestId !== dayRequestRef.current) return;
        console.error('Failed to load automatic Day Review diagnosis', e);
        setSummaryError(summaryErrorMessage(e));
      } finally {
        if (requestId === dayRequestRef.current) setSummaryLoading(false);
      }
    } catch (e) {
      if (requestId !== dayRequestRef.current) return;
      console.error('Failed to load day data', e);
      setLoading(false);
      setSummaryLoading(false);
      setSummaryError(summaryErrorMessage(e));
    }
  }, [accountId, fetchSummary]);

  useEffect(() => {
    fetchAllTimeKpis();
  }, [fetchAllTimeKpis]);

  useEffect(() => {
    fetchDay(date);
  }, [date, fetchDay]);

  const handleRegenerate = async () => {
    if (!trades.length) return;
    const requestId = dayRequestRef.current;
    const requestedDate = date;
    setRegenerating(true);
    setSummaryLoading(true);
    setSummaryError('');
    try {
      const res = await fetchSummary(requestedDate, { force: true });
      if (requestId !== dayRequestRef.current) return;
      setSummary(res.data);
    } catch (e) {
      if (requestId !== dayRequestRef.current) return;
      console.error('Re-run diagnosis failed', e);
      setSummaryError(summaryErrorMessage(e));
    } finally {
      if (requestId === dayRequestRef.current) {
        setRegenerating(false);
        setSummaryLoading(false);
      }
    }
  };

  const handleRetrySummary = async () => {
    if (!trades.length) return;
    const requestId = dayRequestRef.current;
    const requestedDate = date;
    setSummaryLoading(true);
    setSummaryError('');
    try {
      const res = await fetchSummary(requestedDate);
      if (requestId !== dayRequestRef.current) return;
      setSummary(res.data);
    } catch (e) {
      if (requestId !== dayRequestRef.current) return;
      console.error('Retry diagnosis failed', e);
      setSummaryError(summaryErrorMessage(e));
    } finally {
      if (requestId === dayRequestRef.current) setSummaryLoading(false);
    }
  };

  // Consecutive losing trades from end of today's list
  const consecutiveLosses = (() => {
    let count = 0;
    for (let i = trades.length - 1; i >= 0; i--) {
      if ((trades[i].net_pnl || 0) < 0) count++;
      else break;
    }
    return count;
  })();

  // Build grade map for trades table
  const tradeGradeMap = {};
  if (summary?.trade_grades) {
    summary.trade_grades.forEach(g => { tradeGradeMap[g.trade_group] = g; });
  }

  return (
    <div>
      {/* ── Header ── */}
      <PageHeader
        title={formatDateLabel(date)}
        subtitle="Day Review. Read the session while the decisions are fresh."
        actions={<>
          <button type="button" className="btn btn-secondary" onClick={() => onDateChange(prevTradingDay(date))} aria-label="Previous trading day">
            <ChevronLeft size={16} /> Previous
          </button>
          <button type="button" className="btn btn-secondary" onClick={() => onDateChange(nextTradingDay(date))} aria-label="Next trading day">
            Next <ChevronRight size={16} />
          </button>
          {date !== today && (
            <button type="button" className="btn btn-ghost" onClick={() => onDateChange(today)}>
              <Calendar size={15} aria-hidden="true" /> Today
            </button>
          )}
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleRegenerate}
            disabled={regenerating || loading || !trades.length}
          >
            <RotateCcw size={14} style={{ animation: regenerating ? 'spin 1s linear infinite' : 'none' }} aria-hidden="true" />
            {regenerating ? 'Re-running…' : 'Re-run Diagnosis'}
          </button>
        </>}
      />

      {/* ── KPI Strip ── */}
      {loading ? (
        <div className="v3-band"><div className="v3-empty">Loading…</div></div>
      ) : (
        <>
          <div className="v3-hero" style={{ paddingTop: 8 }}>
            <div className="v3-sec-head" style={{ marginBottom: 6 }}>
              <div>
                <h2 className="v3-h">The session</h2>
                <p className="v3-h-sub">
                  Realized P&amp;L through the session, booking each trade when its final execution closes it
                </p>
              </div>
            </div>
            <DayCurve trades={trades} onPick={(t) => onOpenDetail && onOpenDetail(t, trades)} />
          </div>
          <DayMeasures kpis={kpis} trades={trades} summary={summary} allTime={allTimeKpisRef.current} />
        </>
      )}

      {/* ── Circuit Breaker Banner ── */}
      {!loading && !cbDismissed && consecutiveLosses >= 3 && (
        <div className="notice caution" role="alert" style={{ alignItems: 'center', marginBottom: 20 }}>
          <AlertTriangle size={16} style={{ flexShrink: 0 }} aria-hidden="true" />
          <span style={{ fontSize: 14, fontWeight: 600, flex: 1 }}>
            {consecutiveLosses} losses in a row. Consider stepping back and reviewing before the next trade.
          </span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setCbDismissed(true)} aria-label="Dismiss loss-streak alert" style={{ color: 'inherit' }}>
            Dismiss
          </button>
        </div>
      )}

      {/* ── Main 2-column grid ── */}
      <div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {/* Coaching first: you read the review, then the trades it is about */}
          <section className="card">
            <Coaching
              summary={summary}
              loading={summaryLoading}
              error={summaryError}
              onRetry={handleRetrySummary}
              onRegenerate={handleRegenerate}
            />
          </section>

          {/* The trades */}
          <section className="card panel-flush">
            <div style={{ padding: '18px 0 12px' }}>
              <PanelHead
                title="Trade by trade"
                sub="MFE/MAE uses Alpaca 1-minute market paths. Options use the actual OCC contract premium; stocks use stock bars, with broker fills as execution anchors. Missing provider coverage stays blank. Hover a grade for its evidence."
              />
            </div>
            <DayTrades
              trades={trades}
              gradeMap={tradeGradeMap}
              loading={loading || summaryLoading}
              onOpen={onOpenDetail}
            />
          </section>

          {/* Trade Timeline */}

          {/* R-Multiple Chart */}
          {!loading && <RMultipleChart trades={trades} />}

        </div>

        {/* ── Right column ── */}
      </div>

    </div>
  );
}
