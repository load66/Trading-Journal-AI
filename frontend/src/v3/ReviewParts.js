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
  const H = 230;
  const PLOT_TOP = 24;
  const PLOT_BOTTOM = H - 30;

  const geom = useMemo(() => {
    if (!marks.length) return null;

    const vals = marks.map((m) => m.cum).concat([0]);
    const rawLo = Math.min(...vals);
    const rawHi = Math.max(...vals);
    const rawSpan = Math.max(1, rawHi - rawLo);
    const margin = rawSpan * 0.12;
    const lo = rawLo < 0 ? rawLo - margin : 0;
    const hi = rawHi > 0 ? rawHi + margin : 0;
    const span = Math.max(1, hi - lo);

    const X = (hourFrac) => {
      const pct = (hourFrac - OPEN) / (CLOSE - OPEN);
      return Math.max(0, Math.min(W, pct * W));
    };
    const Y = (v) => PLOT_BOTTOM - ((v - lo) / span) * (PLOT_BOTTOM - PLOT_TOP);

    let d = `M0 ${Y(0).toFixed(1)}`;
    let prev = 0;
    marks.forEach((m) => {
      d += ` L${X(m.at).toFixed(1)} ${Y(prev).toFixed(1)} L${X(m.at).toFixed(1)} ${Y(m.cum).toFixed(1)}`;
      prev = m.cum;
    });
    d += ` L${W} ${Y(prev).toFixed(1)}`;
    const area = `${d} L${W} ${PLOT_BOTTOM} L0 ${PLOT_BOTTOM} Z`;

    const highWater = Math.max(0, ...marks.map((m) => m.cum));
    const lowWater = Math.min(0, ...marks.map((m) => m.cum));
    const giveback = Math.max(0, highWater - prev);

    const candidateTicks = lo < 0 && hi > 0
      ? [rawHi, 0, rawLo]
      : hi > 0
        ? [rawHi, rawHi / 2, 0]
        : [0, rawLo / 2, rawLo];

    const ticks = candidateTicks.filter((value, index, arr) => (
      arr.findIndex((other) => Math.abs(other - value) < 0.01) === index
    ));

    return {
      X,
      Y,
      d,
      area,
      zero: Y(0),
      final: prev,
      highWater,
      lowWater,
      giveback,
      ticks,
    };
  }, [marks]);

  if (!geom) {
    return <div className="v3-empty">No timed executions on this day, so the session cannot be drawn.</div>;
  }

  const zones = [
    { label: 'PRIME', start: 9 + 40 / 60, end: 11.5, tone: 'prime' },
    { label: 'CHOP', start: 11.5, end: 13.5, tone: 'chop' },
    { label: 'AFTERNOON', start: 13.5, end: 15, tone: 'afternoon' },
    { label: 'HARD CLOSE', start: 15, end: 15.75, tone: 'hard-close' },
  ];
  const xTicks = [10, 12, 14, 16];
  const peakTradePnl = Math.max(1, ...marks.map((m) => Math.abs(m.pnl)));
  const up = geom.final >= 0;
  const active = hover == null ? null : marks[hover];

  const clock = (hourFrac) => {
    let hour = Math.floor(hourFrac);
    let minute = Math.round((hourFrac - hour) * 60);
    if (minute === 60) {
      hour += 1;
      minute = 0;
    }
    const meridiem = hour >= 12 ? 'PM' : 'AM';
    const displayHour = hour % 12 || 12;
    return `${displayHour}:${String(minute).padStart(2, '0')} ${meridiem} ET`;
  };

  return (
    <div className="v3-session-wrap">
      <div className="v3-session-summary" aria-label="Session P and L landmarks">
        <span>
          <small>Peak</small>
          <b className={tone(geom.highWater)}>{money(geom.highWater)}</b>
        </span>
        <span>
          <small>Giveback</small>
          <b className={geom.giveback > 0 ? 'v3-neg' : 'v3-flat'}>
            {geom.giveback > 0 ? money(-geom.giveback) : '$0'}
          </b>
        </span>
        <span>
          <small>Close</small>
          <b className={tone(geom.final)}>{money2(geom.final)}</b>
        </span>
      </div>

      <div
        className="v3-session"
        aria-label="Realized P and L session chart"
        onPointerLeave={() => setHover(null)}
      >
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="v3day" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={up ? 'var(--result-pos)' : 'var(--result-neg)'} stopOpacity="0.22" />
              <stop offset="100%" stopColor={up ? 'var(--result-pos)' : 'var(--result-neg)'} stopOpacity="0" />
            </linearGradient>
          </defs>

          {zones.map((zone) => (
            <rect
              key={zone.label}
              className={`v3-session-zone ${zone.tone}`}
              x={geom.X(zone.start)}
              y="0"
              width={Math.max(0, geom.X(zone.end) - geom.X(zone.start))}
              height={H}
            />
          ))}

          {xTicks.map((h) => (
            <line
              key={h}
              className="v3-session-vgrid"
              x1={geom.X(h)}
              y1="0"
              x2={geom.X(h)}
              y2={H}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {geom.ticks.map((tick) => (
            <line
              key={tick}
              className={Math.abs(tick) < 0.01 ? 'v3-session-zero' : 'v3-session-hgrid'}
              x1="0"
              y1={geom.Y(tick)}
              x2={W}
              y2={geom.Y(tick)}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {geom.highWater > 0 && geom.giveback > 0 && (
            <line
              className="v3-session-peak-line"
              x1="0"
              y1={geom.Y(geom.highWater)}
              x2={W}
              y2={geom.Y(geom.highWater)}
              vectorEffect="non-scaling-stroke"
            />
          )}

          <path d={geom.area} fill="url(#v3day)" />
          <path
            d={geom.d}
            fill="none"
            stroke={up ? 'var(--result-pos)' : 'var(--result-neg)'}
            strokeWidth="2"
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
          />
        </svg>

        {zones.map((zone) => {
          const left = ((geom.X(zone.start) + geom.X(zone.end)) / 2 / W) * 100;
          return (
            <span
              key={zone.label}
              className={`v3-session-zone-label ${zone.tone}`}
              style={{ left: `${left}%` }}
            >
              {zone.label}
            </span>
          );
        })}

        {xTicks.map((h) => (
          <span
            key={h}
            className={`v3-session-x-label${h === 16 ? ' edge' : ''}`}
            style={{ left: `${(geom.X(h) / W) * 100}%` }}
          >
            {h === 16 ? '16:00' : `${h}:00`}
          </span>
        ))}

        {geom.ticks.map((tick) => (
          <span
            key={`y-${tick}`}
            className="v3-session-y-label"
            style={{ top: `${(geom.Y(tick) / H) * 100}%` }}
          >
            {money(tick)}
          </span>
        ))}

        {active && (
          <span
            className="v3-session-hover-guide"
            style={{ left: `${(geom.X(active.at) / W) * 100}%` }}
            aria-hidden="true"
          />
        )}

        {marks.map((m, i) => {
          const leftPct = (geom.X(m.at) / W) * 100;
          const topPct = (geom.Y(m.cum) / H) * 100;
          const size = 8 + (Math.abs(m.pnl) / peakTradePnl) * 8;
          const side = m.trade.side === 'LONG' ? 'Long' : m.trade.side === 'SHORT' ? 'Short' : m.trade.side;
          return (
            <button
              key={m.trade.id || m.trade.trade_group || i}
              type="button"
              className={`v3-daymark${hover === i ? ' is-active' : ''}`}
              style={{
                left: `${leftPct}%`,
                top: `${topPct}%`,
                width: size,
                height: size,
                background: m.pnl >= 0 ? 'var(--result-pos)' : 'var(--result-neg)',
              }}
              aria-label={`${m.trade.ticker} ${side || ''} closed ${clock(m.at)}, trade ${money2(m.pnl)}, running ${money2(m.cum)}`}
              onPointerEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
              onClick={() => onPick && onPick(m.trade)}
            />
          );
        })}

        {active && (
          <div
            className="v3-tipbox v3-session-tipbox"
            style={{
              left: (geom.X(active.at) / W) * 100 > 62
                ? undefined
                : `calc(${(geom.X(active.at) / W) * 100}% + 14px)`,
              right: (geom.X(active.at) / W) * 100 > 62
                ? `calc(${100 - (geom.X(active.at) / W) * 100}% + 14px)`
                : undefined,
              top: 30,
            }}
          >
            <div className="d">
              {active.trade.ticker}
              {active.trade.side ? ` · ${active.trade.side === 'LONG' ? 'Long' : active.trade.side === 'SHORT' ? 'Short' : active.trade.side}` : ''}
              {' · '}{clock(active.at)}
            </div>
            <div className={`v ${tone(active.pnl)}`}>{money2(active.pnl)}</div>
            <div className="r">Running {money2(active.cum)} · Held {tradeHoldLabel(active.trade)}</div>
            {active.trade.strategy && <div className="r">{active.trade.strategy}</div>}
          </div>
        )}
      </div>
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
          read: breaks ? 'Mechanical execution flags detected below' : 'No mechanical execution flags',
        },
      ]}
    />
  );
}

function escapeHighlightRegExp(value) {
  return value.replace(/[|\\{}()[\]^$+*?.-]/g, '\\$&');
}

function renderCoachHighlights(text, highlights) {
  if (!text) return text;

  const candidates = [
    ...((highlights?.good || []).map((phrase) => ({ phrase, tone: 'good' }))),
    ...((highlights?.bad || []).map((phrase) => ({ phrase, tone: 'bad' }))),
  ]
    .filter((item) => typeof item.phrase === 'string' && item.phrase.trim().length >= 4)
    .map((item) => ({ ...item, phrase: item.phrase.trim() }))
    .sort((a, b) => b.phrase.length - a.phrase.length);

  if (!candidates.length) return text;

  const byPhrase = new Map();
  candidates.forEach((item) => {
    const key = item.phrase.toLowerCase();
    if (!byPhrase.has(key)) byPhrase.set(key, item.tone);
  });

  const pattern = candidates.map((item) => escapeHighlightRegExp(item.phrase)).join('|');
  if (!pattern) return text;

  const pieces = text.split(new RegExp('(' + pattern + ')', 'gi'));
  return pieces.map((piece, index) => {
    const tone = byPhrase.get(piece.toLowerCase());
    return tone
      ? <mark className={'coach-highlight ' + tone} key={index + '-' + piece}>{piece}</mark>
      : piece;
  });
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
            <p className="v3-h-sub">Analyzing the complete trading session…</p>
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

  const wrap = (rows) => (rows || []).map((r) =>
    typeof r === 'string' ? { text: r } : r
  );
  const executionFlags = (summary.behavior_flags || []).map((f) => ({
    text: `${f.title}: ${f.detail}`,
    tone: 'bad',
  }));
  const recordedRows = wrap(summary.recorded_observations || []);
  const lists = {
    strengths: wrap(summary.strengths).map((r) => ({ ...r, tone: 'good' })),
    mistakes: wrap(summary.mistakes).map((r) => ({ ...r, tone: 'bad' })),
    focus: wrap((summary.coaching?.length ? summary.coaching : summary.tomorrow_focus) || []),
    patterns: wrap(summary.patterns),
    execution_flags: executionFlags,
    recorded: recordedRows,
  };
  const tabs = [
    { id: 'strengths', label: 'Strengths' },
    { id: 'mistakes', label: 'Mistakes' },
    { id: 'focus', label: 'Tomorrow’s focus' },
    { id: 'patterns', label: 'Patterns' },
    ...(executionFlags.length ? [{ id: 'execution_flags', label: 'Execution flags' }] : []),
    ...(recordedRows.length ? [{ id: 'recorded', label: 'Recorded' }] : []),
  ];
  const rows = lists[tab] || [];

  return (
    <>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">Coaching</h2>
          <p className="v3-h-sub">
            Written against your trades and your diary together, and graded on process
            {summary.ai_provider ? <>
              {' · '}{summary.ai_provider === 'groq' ? 'Groq' : 'Anthropic'}
              {summary.ai_model ? <span className="day-review-model-meta"> · {summary.ai_model}</span> : null}
            </> : ''}
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
        {summary.narrative && (
          <p className="v3-narr">{renderCoachHighlights(summary.narrative, summary.highlights)}</p>
        )}
        {summary.mental_game && (
          <p className="v3-narr v3-narr-quiet">
            {renderCoachHighlights(summary.mental_game, summary.highlights)}
          </p>
        )}
      </div>

      <div style={{ marginTop: 22 }}>
        <Tabs tabs={tabs} active={tab} onChange={setTab} label="Review detail" />
        {!rows.length ? (
          <div className="v3-empty">
            {tab === 'mistakes' ? 'No mistakes identified in this diagnosis.' : 'Nothing recorded here.'}
          </div>
        ) : tab === 'patterns' ? (
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
            {rows.map((r, i) => (
              <span className="v3-chip" key={i} style={{ whiteSpace: 'normal' }}>
                {r.text}
              </span>
            ))}
          </div>
        ) : (
          <ul className="v3-list v3-cols">
            {rows.map((r, i) => (
              <li
                key={i}
                className={
                  r.tone === 'bad' || tab === 'mistakes' || tab === 'execution_flags'
                    ? 'bad'
                    : r.tone === 'good' || tab === 'strengths'
                      ? 'good'
                      : tab === 'focus'
                        ? 'next'
                        : ''
                }
              >
                {r.text}
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

  const gradeTone = (grade) => {
    const value = String(grade || '').toUpperCase();
    if (value.startsWith('A')) return 'good';
    if (value === 'D' || value === 'F') return 'bad';
    return 'neutral';
  };

  return (
    <>
      <div className="v3-scroll v3-trade-table-wrap">
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

      <div className="v3-trade-cards" role="list" aria-label="Trades for this day">
        {trades.map((t) => {
          const pnl = Number(t.net_pnl || 0);
          const g = gradeMap?.[t.trade_group];
          const plPct = tradePLPercent(t);
          const open = () => onOpen && onOpen(t, trades);
          const side = t.side === 'LONG' ? 'Long' : t.side === 'SHORT' ? 'Short' : t.side;

          return (
            <div role="listitem" key={t.id} className="v3-trade-card-shell">
              <button
                type="button"
                className="v3-trade-card"
                onClick={open}
                aria-label={`Open ${t.ticker} trade, ${money2(pnl)}`}
              >
                <span className="v3-trade-card-top">
                <span>
                  <strong className="v3-trade-card-ticker">{t.ticker}</strong>
                  <span className="v3-trade-card-meta">{t.instrument_type} · {side}</span>
                </span>
                <strong className={`v3-trade-card-pnl ${tone(pnl)}`}>{money2(pnl)}</strong>
              </span>

              <span className="v3-trade-card-grid">
                <span>
                  <small>Grade</small>
                  <b className={`v3-trade-card-grade ${gradeTone(g?.grade)}`}>{g?.grade || '—'}</b>
                </span>
                <span>
                  <small>Hold</small>
                  <b>{tradeHoldLabel(t)}</b>
                </span>
                <span>
                  <small>P/L %</small>
                  <b className={plPct == null ? 'v3-flat' : tone(plPct)}>
                    {plPct == null ? '—' : `${plPct > 0 ? '+' : ''}${plPct.toFixed(1)}%`}
                  </b>
                </span>
                <span>
                  <small>R</small>
                  <b className={t.r_multiple != null ? tone(t.r_multiple) : 'v3-flat'}>
                    {t.r_multiple != null ? `${t.r_multiple > 0 ? '+' : ''}${Number(t.r_multiple).toFixed(2)}R` : '—'}
                  </b>
                </span>
              </span>

                {g?.one_line && <span className="v3-trade-card-reason">{g.one_line}</span>}
              </button>
            </div>
          );
        })}
      </div>
    </>
  );
}
