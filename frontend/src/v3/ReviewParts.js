/* Review page pieces in the V3 language. Presentation only. */
import { useState, useMemo } from 'react';
import { Measures, Tabs, Grade, money, money2, tone } from './parts';
import { tradeHoldSeconds, tradeMarketHour, tradeStats } from '../tradeMetrics';

/* the clock every trading session runs on */
const OPEN = 9.5;
const CLOSE = 16;

function tradeHoldLabel(t) {
  const secRaw = tradeHoldSeconds(t);
  if (secRaw == null) return '—';
  const sec = Math.max(0, Math.round(secRaw));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}h ${m}m`;
}

function tradePLPercent(t) {
  return tradeStats(t).plPercent;
}

export function tradeTime(t, which = 'entry') {
  return tradeMarketHour(t, which);
}

/* ── the day, as one picture ────────────────────────────────────────────────
   Realized session P&L. A trade's final P&L is booked at its last execution,
   not at entry, so the curve never pretends a future exit result was already
   known when the position opened.                                          */
export function DayCurve({ trades, onPick }) {
  const [hover, setHover] = useState(null);

  const marks = useMemo(() => {
    const withTime = (trades || [])
      .map((t) => ({ t, at: tradeTime(t, 'exit') }))
      .filter((x) => x.at != null)
      .sort((a, b) => a.at - b.at);
    let cum = 0;
    return withTime.map(({ t, at }) => {
      cum += Number(t.net_pnl) || 0;
      return { trade: t, at, pnl: Number(t.net_pnl) || 0, cum };
    });
  }, [trades]);

  const W = 1000;
  const H = 210;
  const PAD = 26;

  const geom = useMemo(() => {
    if (!marks.length) return null;
    const vals = marks.map((m) => m.cum).concat([0]);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = (hi - lo) || 1;
    const X = (hourFrac) => ((hourFrac - OPEN) / (CLOSE - OPEN)) * W;
    const Y = (v) => H - PAD - ((v - lo) / span) * (H - PAD * 2);
    // a step line: the balance holds until the next trade closes it
    let d = `M0 ${Y(0).toFixed(1)}`;
    let prev = 0;
    marks.forEach((m) => {
      d += ` L${X(m.at).toFixed(1)} ${Y(prev).toFixed(1)} L${X(m.at).toFixed(1)} ${Y(m.cum).toFixed(1)}`;
      prev = m.cum;
    });
    d += ` L${W} ${Y(prev).toFixed(1)}`;
    const area = `${d} L${W} ${H} L0 ${H} Z`;
    return { X, Y, d, area, zero: Y(0), final: prev };
  }, [marks]);

  if (!geom) return <div className="v3-empty">No timed executions on this day, so the session cannot be drawn.</div>;

  const peak = Math.max(1, ...marks.map((m) => Math.abs(m.pnl)));
  const up = geom.final >= 0;

  return (
    <div className="v3-session" onPointerLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <defs>
          <linearGradient id="v3day" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={up ? 'var(--result-pos)' : 'var(--result-neg)'} stopOpacity="0.2" />
            <stop offset="100%" stopColor={up ? 'var(--result-pos)' : 'var(--result-neg)'} stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* the hours */}
        {[10, 11, 12, 13, 14, 15].map((h) => (
          <g key={h}>
            <line x1={geom.X(h)} y1="0" x2={geom.X(h)} y2={H} stroke="var(--divider-soft)" strokeWidth="1"
              vectorEffect="non-scaling-stroke" />
            <text className="v3-tl-lab" x={geom.X(h) + 5} y="12">{h}:00</text>
          </g>
        ))}
        <line x1="0" y1={geom.zero} x2={W} y2={geom.zero} stroke="var(--divider)" strokeWidth="1"
          vectorEffect="non-scaling-stroke" />
        <path d={geom.area} fill="url(#v3day)" />
        <path d={geom.d} fill="none" stroke={up ? 'var(--result-pos)' : 'var(--result-neg)'}
          strokeWidth="1.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>

      {/* one mark per completed trade, booked at its final exit time */}
      {marks.map((m, i) => {
        const leftPct = (geom.X(m.at) / W) * 100;
        const topPct = (geom.Y(m.cum) / H) * 100;
        const size = 5 + (Math.abs(m.pnl) / peak) * 7;
        return (
          <button
            key={i}
            type="button"
            className="v3-daymark"
            style={{
              left: `${leftPct}%`, top: `${topPct}%`, width: size, height: size,
              background: m.pnl >= 0 ? 'var(--result-pos)' : 'var(--result-neg)',
            }}
            aria-label={`${m.trade.ticker} at ${m.at ? `${Math.floor(m.at)}:${String(Math.round((m.at % 1) * 60)).padStart(2, '0')}` : ''}, ${money2(m.pnl)}`}
            onPointerEnter={() => setHover(i)}
            onFocus={() => setHover(i)}
            onClick={() => onPick && onPick(m.trade)}
          />
        );
      })}

      {hover != null && (
        <div
          className="v3-tipbox"
          style={{
            left: (geom.X(marks[hover].at) / W) * 100 > 62 ? undefined : `calc(${(geom.X(marks[hover].at) / W) * 100}% + 14px)`,
            right: (geom.X(marks[hover].at) / W) * 100 > 62 ? `calc(${100 - (geom.X(marks[hover].at) / W) * 100}% + 14px)` : undefined,
            top: 8,
          }}
        >
          <div className="d">{marks[hover].trade.ticker}</div>
          <div className={`v ${tone(marks[hover].pnl)}`}>{money2(marks[hover].pnl)}</div>
          <div className="r">running {money(marks[hover].cum)}</div>
        </div>
      )}
    </div>
  );
}

/* ── the day's measures ───────────────────────────────────────────────────
   Each cell reads the day's own KPIs and sets them against all time in the
   same cell, instead of a separate comparison row. Values that cannot be
   measured on a day (a profit factor with no losers) say so in words, and are
   never compared as if they were zero. */
function Delta({ day, all, digits = 1, unit = '', prefix = '' }) {
  if (day == null || all == null || !Number.isFinite(Number(day)) || !Number.isFinite(Number(all))) return null;
  const d = Number(day) - Number(all);
  if (Math.abs(d) < 10 ** -digits / 2) return <span className="v3-flat"> level</span>;
  const cls = d > 0 ? 'v3-pos' : 'v3-neg';
  const abs = Math.abs(d).toFixed(digits);
  return <span className={cls}> {d > 0 ? '+' : '−'}{prefix}{abs}{unit}</span>;
}

export function DayMeasures({ kpis, trades, summary, allTime }) {
  const k = kpis || {};
  const a = allTime || null;
  const net = Number(k.total_net_pnl || 0);
  const n = k.total_trades ?? (trades || []).length;

  // how far the day came off its own high water mark
  const { peak, given } = useMemo(() => {
    const withTime = (trades || [])
      .map((t) => ({ at: tradeTime(t, 'exit'), p: Number(t.net_pnl) || 0 }))
      .filter((x) => x.at != null)
      .sort((x, y) => x.at - y.at);
    let cum = 0; let hi = 0;
    withTime.forEach((x) => { cum += x.p; hi = Math.max(hi, cum); });
    return { peak: hi, given: Math.max(0, hi - cum) };
  }, [trades]);

  const breaks = (summary?.behavior_flags || []).length;
  const wins = (trades || []).filter((t) => (t.net_pnl || 0) > 0).length;
  const losses = (trades || []).filter((t) => (t.net_pnl || 0) < 0).length;

  const avgDay = a && a.trading_days ? Number(a.total_net_pnl || 0) / a.trading_days : null;
  const pf = k.profit_factor == null ? null : Number(k.profit_factor);
  const noLosers = pf == null && wins > 0;
  const avgWin = Math.abs(Number(k.avg_win || 0));
  const avgLoss = Math.abs(Number(k.avg_loss || 0));
  const payoff = avgLoss > 0 ? avgWin / avgLoss : null;
  const allAvgWin = a ? Math.abs(Number(a.avg_win || 0)) : 0;
  const allAvgLoss = a ? Math.abs(Number(a.avg_loss || 0)) : 0;
  const allPayoff = allAvgLoss > 0 ? allAvgWin / allAvgLoss : null;
  const perTrade = n ? net / n : null;
  const allExp = a && a.expectancy != null ? Number(a.expectancy) : null;

  const allLine = (label, value) => (a && value != null ? <>All-time {label}{value}</> : null);

  return (
    <Measures
      className="v3-measures-4"
      items={[
        {
          label: 'Net for the day',
          value: money2(net),
          met: net >= 0,
          tone: net < 0 ? 'neg' : undefined,
          read: avgDay == null
            ? `${wins} green, ${losses} red`
            : <>Your average day {money(avgDay)}<Delta day={net} all={avgDay} digits={0} prefix="$" /></>,
        },
        {
          label: 'Win rate',
          value: n ? `${Number(k.win_rate || 0).toFixed(0)}%` : '—',
          read: <>
            {wins} of {n} won.{' '}
            {allLine('', a ? `${Number(a.win_rate || 0).toFixed(1)}%` : null)}
            {a && n ? <Delta day={k.win_rate} all={a.win_rate} unit="pp" /> : null}
          </>,
        },
        {
          label: 'Profit factor',
          value: noLosers ? 'No losers' : pf == null ? '—' : pf.toFixed(2),
          met: noLosers || (pf != null && pf >= 1),
          tone: pf != null && pf < 1 ? 'neg' : undefined,
          read: <>
            {noLosers ? 'Nothing to divide by today. ' : ''}
            {allLine('', a && a.profit_factor != null ? Number(a.profit_factor).toFixed(2) : null)}
            {pf != null && a && a.profit_factor != null ? <Delta day={pf} all={a.profit_factor} digits={2} unit="x" /> : null}
          </>,
        },
        {
          label: 'Avg win',
          value: k.avg_win ? money(k.avg_win) : '—',
          read: <>
            {allLine('', a && a.avg_win ? money(a.avg_win) : null)}
            {k.avg_win && a && a.avg_win ? <Delta day={k.avg_win} all={a.avg_win} digits={0} prefix="$" /> : null}
          </>,
        },
        {
          label: 'Avg per trade',
          value: perTrade == null ? '—' : money2(perTrade),
          tone: perTrade != null && perTrade < 0 ? 'neg' : undefined,
          read: <>
            {allExp != null ? <>All-time expectancy {money2(allExp)}</> : 'Net divided by trades'}
            {perTrade != null && allExp != null ? <Delta day={perTrade} all={allExp} digits={0} prefix="$" /> : null}
          </>,
        },
        {
          label: 'Payoff ratio',
          value: payoff == null ? '—' : `${payoff.toFixed(2)}x`,
          met: payoff != null && payoff >= 1,
          tone: payoff != null && payoff < 1 ? 'neg' : undefined,
          read: payoff == null
            ? 'Needs at least one winning and one losing trade'
            : <>
              Avg win {money(avgWin)} / avg loss {money(avgLoss)}
              {allPayoff != null ? <> · All-time {allPayoff.toFixed(2)}x</> : null}
              {allPayoff != null ? <Delta day={payoff} all={allPayoff} digits={2} unit="x" /> : null}
            </>,
        },
        {
          label: 'Realized giveback',
          value: given > 0 ? money(-given) : '$0',
          amber: given > 0,
          read: peak > 0
            ? `From a realized P&L peak of ${money(peak)}`
            : 'Realized P&L never went green',
        },
        {
          label: 'Review flags',
          value: String(breaks),
          amber: breaks > 0,
          read: breaks ? 'Evidence-backed coaching flags below' : 'No verified coaching flags',
        },
      ]}
    />
  );
}

function EvidenceBadge({ level }) {
  if (!level) return null;
  const normalized = String(level).toUpperCase();
  const cls = normalized === 'VERIFIED'
    ? 'verified'
    : normalized === 'RECORDED'
      ? 'recorded'
      : normalized === 'ANALYZED'
        ? 'analyzed'
        : 'insufficient';
  return <span className={`v3-evidence ${cls}`}>{normalized}</span>;
}

/* ── coaching: the report, with the lists behind tabs ───────────────────── */
export function Coaching({ summary, loading, error, onRetry, onRegenerate }) {
  const [tab, setTab] = useState('strengths');

  if (loading) {
    return (
      <div role="status" aria-live="polite">
        <div className="v3-sec-head">
          <div>
            <h2 className="v3-h">Coaching</h2>
            <p className="v3-h-sub">Analyzing the session and checking the current evidence…</p>
          </div>
        </div>
        <div className="skeleton" style={{ height: 15, width: '92%', marginBottom: 10 }} />
        <div className="skeleton" style={{ height: 15, width: '84%', marginBottom: 10 }} />
        <div className="skeleton" style={{ height: 15, width: '67%', marginBottom: 22 }} />
        <div style={{ display: 'flex', gap: 8 }}>
          <div className="skeleton" style={{ height: 30, width: 92 }} />
          <div className="skeleton" style={{ height: 30, width: 74 }} />
          <div className="skeleton" style={{ height: 30, width: 116 }} />
        </div>
        <span className="sr-only">Generating AI diagnosis.</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="v3-notice" role="alert">
        <div style={{ flex: 1 }}>
          <b>AI diagnosis could not be generated.</b>
          <p>{error}</p>
        </div>
        {onRetry && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    );
  }

  if (summary?.no_trades) {
    return (
      <>
        <div className="v3-sec-head">
          <div>
            <h2 className="v3-h">Coaching</h2>
            <p className="v3-h-sub">AI diagnosis starts automatically when this day has completed trades.</p>
          </div>
        </div>
        <div className="v3-empty">No completed trades to diagnose for this date.</div>
      </>
    );
  }

  if (!summary) return <div className="v3-empty">No review for this day yet.</div>;

  const obs = summary.observations || {};
  const wrap = (rows, fallbackEvidence = null) => (rows || []).map((r) =>
    typeof r === 'string' ? { text: r, evidence: fallbackEvidence } : r
  );
  const lists = {
    strengths: obs.strengths?.length ? obs.strengths : wrap(summary.strengths, summary.evidence_locked ? 'VERIFIED' : null),
    mistakes: obs.mistakes?.length ? obs.mistakes : wrap(summary.mistakes),
    focus: obs.focus?.length ? obs.focus : wrap(summary.coaching),
    patterns: obs.patterns?.length ? obs.patterns : wrap(summary.patterns),
    recorded: obs.recorded || [],
  };
  const tabs = [
    { id: 'strengths', label: 'Strengths' },
    { id: 'mistakes', label: 'Flags' },
    { id: 'focus', label: 'Tomorrow’s focus' },
    { id: 'patterns', label: 'Patterns' },
    ...(lists.recorded.length ? [{ id: 'recorded', label: 'Recorded' }] : []),
  ];
  const rows = lists[tab] || [];

  return (
    <>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">Coaching</h2>
          <p className="v3-h-sub">
            Written against your trades and your diary together, and graded on process
            {summary.ai_provider ? ` · ${summary.ai_provider === 'groq' ? 'Groq' : 'Anthropic'} · ${summary.ai_model || ''}` : ''}
            {summary.evidence_locked ? ' · Evidence-locked' : ''}
            {summary.cached && summary.cache_reason === 'evidence_unchanged' ? ' · Saved diagnosis · evidence unchanged' : ''}
            {!summary.cached && summary.regeneration_reason === 'manual_override' ? ' · Manually refreshed' : ''}
          </p>
        </div>
        <div className="v3-acts">
          {onRegenerate && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={onRegenerate}>Re-run</button>
          )}
        </div>
      </div>

      <div className="v3-cols">
        {summary.narrative && <p className="v3-narr">{summary.narrative}</p>}
        {summary.mental_game && (
          <p className="v3-narr v3-narr-quiet">
            <EvidenceBadge level={obs.mental_game?.evidence || (summary.evidence_locked ? 'INSUFFICIENT DATA' : null)} /> {summary.mental_game}
          </p>
        )}
      </div>

      <div style={{ marginTop: 22 }}>
        <Tabs tabs={tabs} active={tab} onChange={setTab} label="Review detail" />
        {!rows.length ? (
          <div className="v3-empty">
            {tab === 'mistakes' ? 'No deterministic behavior flags on this day.' : 'Nothing recorded here.'}
          </div>
        ) : tab === 'patterns' ? (
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
            {rows.map((r, i) => (
              <span className="v3-chip" key={i} style={{ whiteSpace: 'normal' }}>
                <EvidenceBadge level={r.evidence} /> {r.text}
              </span>
            ))}
          </div>
        ) : (
          <ul className="v3-list v3-cols">
            {rows.map((r, i) => (
              <li key={i} className={tab === 'mistakes' ? 'bad' : tab === 'focus' ? 'next' : 'good'}>
                <EvidenceBadge level={r.evidence} /> {r.text}
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

/* ── the day's trades, with the grade carrying its reason ───────────────── */
export function DayTrades({ trades, gradeMap, loading, onOpen }) {
  if (loading) return <div className="v3-empty">Loading…</div>;
  if (!trades?.length) return <div className="v3-empty">No trades on this day.</div>;
  return (
    <div className="v3-scroll">
      <table className="v3-t">
        <thead>
          <tr>
            <th>Ticker</th>
            <th className="v3-hide-s">Side</th>
            <th className="v3-hide-s">Strategy</th>
            <th>Grade</th>
            <th className="r v3-hide-s">Hold</th>
            <th className="r v3-hide-s">P/L %</th>
            <th className="r">R</th>
            <th className="r">P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => {
            const pnl = Number(t.net_pnl || 0);
            const g = gradeMap?.[t.trade_group];
            const open = () => onOpen && onOpen(t, trades);
            return (
              <tr
                key={t.id}
                className="clickable"
                tabIndex={0}
                onClick={open}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
                aria-label={`Open ${t.ticker} trade`}
              >
                <td>
                  <div className="v3-tick">{t.ticker}</div>
                  <div className="v3-read">{t.instrument_type}</div>
                </td>
                <td className="v3-hide-s v3-side">
                  {t.side === 'LONG' ? 'Long' : t.side === 'SHORT' ? 'Short' : t.side}
                </td>
                <td className="v3-hide-s v3-read">{t.strategy || 'not set'}</td>
                <td onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                  {g ? <Grade grade={g.grade} reason={g.one_line} /> : <span className="v3-flat">&mdash;</span>}
                </td>
                <td className="r v3-mono v3-hide-s">{tradeHoldLabel(t)}</td>
                <td className={`r v3-mono v3-hide-s ${tradePLPercent(t) == null ? 'v3-flat' : tone(tradePLPercent(t))}`}>
                  {tradePLPercent(t) == null ? '—' : `${tradePLPercent(t) > 0 ? '+' : ''}${tradePLPercent(t).toFixed(1)}%`}
                </td>
                <td className={`r v3-mono ${t.r_multiple != null ? tone(t.r_multiple) : 'v3-flat'}`}>
                  {t.r_multiple != null ? `${t.r_multiple > 0 ? '+' : ''}${Number(t.r_multiple).toFixed(2)}R` : '—'}
                </td>
                <td className={`r v3-mono ${tone(pnl)}`} style={{ fontWeight: 600 }}>{money2(pnl)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
