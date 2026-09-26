/* The V3 Today page. Presentation only: every value, handler and piece of
   state is passed in from Dashboard.js, so no behaviour lives here. */
import { useState, useEffect } from 'react';
import { X } from 'lucide-react';
import { calendarApi } from '../api';
import {
  Measures, Tabs, EquityCurve, DailyPnlBars, TimeOfDayBars, MonthGrid,
  money, money2, moneyK, tone, shortDate, MONTH_NAMES,
} from './parts';

/* ── the month, fetched per month like the old mini calendar ───────────── */
function MonthPanel({ accountId, onDayClick, latestDate }) {
  const now = new Date();
  // Open on the month of the most recent session, not on today. Today's month
  // is empty whenever the last trade was in an earlier month, which left the
  // dashboard showing a blank grid.
  const seed = latestDate ? String(latestDate).split('-') : null;
  const [year, setYear] = useState(seed ? Number(seed[0]) : now.getFullYear());
  const [month, setMonth] = useState(seed ? Number(seed[1]) : now.getMonth() + 1);
  const [byDay, setByDay] = useState({});
  const [share, setShare] = useState(false);

  useEffect(() => {
    const params = { year, month };
    if (accountId != null) params.account_id = accountId;
    let live = true;
    calendarApi.get(params).then((r) => {
      if (!live) return;
      const map = {};
      for (const d of r.data) map[d.date] = d;
      setByDay(map);
    }).catch(() => {});
    return () => { live = false; };
  }, [year, month, accountId]);

  const prev = () => (month === 1 ? (setYear((y) => y - 1), setMonth(12)) : setMonth((m) => m - 1));
  const next = () => (month === 12 ? (setYear((y) => y + 1), setMonth(1)) : setMonth((m) => m + 1));

  const all = Object.values(byDay);
  const total = all.reduce((s, d) => s + (d.net_pnl || 0), 0);
  const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;

  return (
    <>
      <div className="v3-cal-head">
        <h2 className="v3-cal-month">{MONTH_NAMES[month - 1]}<span>{year}</span></h2>
        <div className="v3-step">
          <button type="button" onClick={prev} aria-label="Previous month">&lsaquo;</button>
          <button type="button" onClick={next} aria-label="Next month">&rsaquo;</button>
        </div>
        <div className="v3-cal-tot">
          {all.length} session{all.length === 1 ? '' : 's'} traded
          <b className={tone(total)}>{all.length ? moneyK(total) : ''}</b>
        </div>
        <button
          type="button"
          className={`btn btn-secondary btn-sm v3-share-toggle${share ? ' active' : ''}`}
          onClick={() => setShare((v) => !v)}
          aria-pressed={share}
        >
          {share ? 'Exit share view' : 'Share view'}
        </button>
      </div>

      {share ? (
        <div className="v3-cal-share-card" aria-label="Screenshot-ready trading calendar">
          <div className="v3-cal-share-head">
            <div>
              <div className="v3-lab">Trading calendar</div>
              <h2>{MONTH_NAMES[month - 1]} <span>{year}</span></h2>
            </div>
            <div className="v3-cal-share-stat">
              <b className={tone(total)}>{all.length ? money(total) : '$0'}</b>
              <span>{all.length} session{all.length === 1 ? '' : 's'}</span>
            </div>
          </div>
          <MonthGrid
            year={year}
            month={month}
            byDay={byDay}
            today={todayKey}
            onPick={onDayClick}
            showWeek={false}
          />
          <div className="v3-cal-share-foot">Net P&amp;L · broker-recorded sessions</div>
        </div>
      ) : (
        <div className="v3-scroll">
          <MonthGrid year={year} month={month} byDay={byDay} today={todayKey} onPick={onDayClick} />
        </div>
      )}
    </>
  );
}

/* ── recorded context edges ────────────────────────────────────────────── */
function Patterns({ dimensions, onViewAll }) {
  const [tab, setTab] = useState('strategy');
  const tabs = [
    { id: 'strategy', label: 'Strategy' },
    { id: 'source', label: 'Source' },
    { id: 'setup', label: 'Setup' },
    { id: 'emotion', label: 'Emotion' },
  ];
  const titles = {
    strategy: 'Which recorded strategy is most repeatable',
    source: 'Which idea source is producing the strongest outcomes',
    setup: 'Which setup is working — kept separate from strategy',
    emotion: 'Recorded emotional state only — never inferred',
  };
  const columnNames = {
    strategy: 'Strategy',
    source: 'Source',
    setup: 'Setup',
    emotion: 'Emotion',
  };

  const meta = dimensions?.[tab] || {};
  const rows = meta.rows || [];
  const bestWin = meta.best_win_rate || null;
  const strongest = meta.strongest || null;
  const weakest = meta.weakest && (!strongest || meta.weakest.label !== strongest.label)
    ? meta.weakest : null;

  const fmtPct = (v) => v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
  const fmtPf = (v) => v == null ? '—' : Number(v).toFixed(2);

  return (
    <>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">What works</h2>
          <p className="v3-h-sub">{titles[tab]}</p>
        </div>
        <div className="v3-acts">
          <span className="v3-chip">
            Coverage {Number(meta.coverage_pct || 0).toFixed(0)}%
          </span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onViewAll}>Full report</button>
        </div>
      </div>

      <Tabs tabs={tabs} active={tab} onChange={setTab} label="Recorded context breakdown" />

      <div className="v3-edge-note">
        <span>{meta.coverage_count || 0} of {meta.total_trades || 0} trades recorded</span>
        <span>Ranking requires {meta.min_sample || 1}+ trades per label</span>
      </div>

      {(bestWin || strongest || weakest) && (
        <div className="v3-edge-insights">
          <article>
            <span className="v3-lab">Highest win rate</span>
            {bestWin ? (
              <>
                <b>{bestWin.label}</b>
                <strong>{Number(bestWin.win_rate || 0).toFixed(1)}%</strong>
                <small>{bestWin.count} trades · expectancy {money(bestWin.expectancy)}</small>
              </>
            ) : <small>Need more recorded trades.</small>}
          </article>

          <article>
            <span className="v3-lab">Strongest expectancy</span>
            {strongest ? (
              <>
                <b>{strongest.label}</b>
                <strong className={tone(strongest.expectancy)}>{money(strongest.expectancy)} / trade</strong>
                <small>WR {Number(strongest.win_rate || 0).toFixed(0)}% · PF {fmtPf(strongest.profit_factor)}</small>
              </>
            ) : <small>Need more recorded trades.</small>}
          </article>

          <article>
            <span className="v3-lab">Needs attention</span>
            {weakest ? (
              <>
                <b>{weakest.label}</b>
                <strong className={tone(weakest.expectancy)}>{money(weakest.expectancy)} / trade</strong>
                <small>WR {Number(weakest.win_rate || 0).toFixed(0)}% · PF {fmtPf(weakest.profit_factor)}</small>
              </>
            ) : <small>No separate qualified weak sample yet.</small>}
          </article>
        </div>
      )}

      {!rows.length ? (
        <div className="v3-empty">Nothing recorded for {columnNames[tab].toLowerCase()} in this range.</div>
      ) : (
        <div className="v3-scroll">
          <table className="v3-t v3-edge-table">
            <thead>
              <tr>
                <th>{columnNames[tab]}</th>
                <th className="r">Trades</th>
                <th className="r v3-hide-s">W-L</th>
                <th className="r">Win rate</th>
                <th className="r v3-hide-s">PF</th>
                <th className="r">Expectancy</th>
                <th className="r v3-hide-s">Avg P/L %</th>
                <th className="r">Net P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.label}-${i}`} className={r.sample_qualified ? '' : 'v3-edge-low-sample'}>
                  <td className="v3-tick">
                    <span>{r.label}</span>
                    {!r.sample_qualified && <small title={`Ranking requires ${meta.min_sample || 1}+ trades`}>small sample</small>}
                  </td>
                  <td className="r v3-mono">{r.count}</td>
                  <td className="r v3-mono v3-hide-s">{r.wins}-{r.losses}</td>
                  <td className="r v3-mono" style={{ fontWeight: 600 }}>{Number(r.win_rate || 0).toFixed(1)}%</td>
                  <td className="r v3-mono v3-hide-s">{fmtPf(r.profit_factor)}</td>
                  <td className={`r v3-mono ${tone(r.expectancy)}`}>{money(r.expectancy)}</td>
                  <td className={`r v3-mono v3-hide-s ${r.avg_pl_pct == null ? 'v3-flat' : tone(r.avg_pl_pct)}`}>
                    {fmtPct(r.avg_pl_pct)}
                  </td>
                  <td className={`r v3-mono ${tone(r.net_pnl)}`} style={{ fontWeight: 600 }}>{money(r.net_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/* ── open positions, with the exit form ────────────────────────────────── */
function OpenPositions(p) {
  const {
    openPositions, onOpenDetail, openCloseModal,
    closingPos, setClosingPos, closeDate, setCloseDate, closeTime, setCloseTime,
    closePrice, setClosePrice, closeCommission, setCloseCommission,
    closeError, closeSubmitting, handleClosePosition,
  } = p;

  return (
    <div className="v3-subband">
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">Open positions</h2>
          <p className="v3-h-sub">Recording an exit journals it, it does not place an order</p>
        </div>
        <span className={`v3-chip${openPositions?.length ? ' caution' : ''}`}>
          {openPositions?.length || 0} position{openPositions?.length === 1 ? '' : 's'}
        </span>
      </div>

      {!openPositions?.length ? (
        <div className="v3-empty">No open positions.</div>
      ) : (
        <div className="v3-scroll">
          <table className="v3-t">
            <thead>
              <tr>
                <th>Ticker</th>
                <th className="v3-hide-s">Side</th>
                <th className="r">Remaining</th>
                <th className="r">Avg entry</th>
                <th className="r"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {openPositions.map((pos, i) => {
                const execs = pos.executions || [];
                const side = (pos.side || 'LONG').toUpperCase();
                const entryAction = side === 'LONG' ? 'BOT' : 'SOLD';
                const exitAction = side === 'LONG' ? 'SOLD' : 'BOT';
                const entryFills = execs.filter((e) => e.action === entryAction);
                const totalQty = entryFills.reduce((a, e) => a + (e.qty || 0), 0);
                const exitQty = execs.filter((e) => e.action === exitAction).reduce((a, e) => a + (e.qty || 0), 0);
                const remainingQty = totalQty - exitQty;
                const avgEntry = totalQty > 0
                  ? entryFills.reduce((a, e) => a + (e.qty || 0) * (e.price || 0), 0) / totalQty : 0;
                const open = () => onOpenDetail && onOpenDetail(pos);
                return (
                  <tr
                    key={i}
                    className="clickable"
                    tabIndex={0}
                    onClick={open}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } }}
                    aria-label={`Open ${pos.ticker} position`}
                  >
                    <td className="v3-tick">{pos.ticker}</td>
                    <td className="v3-hide-s v3-side">{side === 'LONG' ? 'Long' : 'Short'}</td>
                    <td className="r v3-mono">
                      {remainingQty}
                      {exitQty > 0 && <span className="v3-read"> of {totalQty}</span>}
                    </td>
                    <td className="r v3-mono">
                      ${avgEntry.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="r">
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={(e) => { e.stopPropagation(); openCloseModal(pos); }}
                        onKeyDown={(e) => e.stopPropagation()}
                      >
                        Close
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {closingPos && (
        <div className="v3-notice" style={{ marginTop: 16, display: 'block' }} role="group" aria-label={`Close ${closingPos.ticker}`}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>
              Record exit: {closingPos.ticker} {closingPos.side} ({closingPos.openQty} remaining)
            </span>
            <button type="button" className="btn btn-ghost btn-icon" onClick={() => setClosingPos(null)} aria-label="Cancel closing position">
              <X size={14} />
            </button>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label className="field-label" htmlFor="close-date">Exit date</label>
              <input id="close-date" type="date" value={closeDate} onChange={(e) => setCloseDate(e.target.value)} style={{ width: 148 }} />
            </div>
            <div>
              <label className="field-label" htmlFor="close-time">Exit time</label>
              <input id="close-time" type="time" value={closeTime} onChange={(e) => setCloseTime(e.target.value)} style={{ width: 118 }} />
            </div>
            <div>
              <label className="field-label" htmlFor="close-price">Exit price</label>
              <input id="close-price" type="number" step="0.01" placeholder="0.00" value={closePrice}
                onChange={(e) => setClosePrice(e.target.value)} style={{ width: 118 }}
                onKeyDown={(e) => e.key === 'Enter' && handleClosePosition()} />
            </div>
            <div>
              <label className="field-label" htmlFor="close-comm">Fees</label>
              <input id="close-comm" type="number" step="0.01" min="0" value={closeCommission}
                onChange={(e) => setCloseCommission(e.target.value)} style={{ width: 96 }}
                onKeyDown={(e) => e.key === 'Enter' && handleClosePosition()} />
            </div>
            <button type="button" className="btn btn-primary" onClick={handleClosePosition}
              disabled={closeSubmitting || !closePrice || !closeDate}>
              {closeSubmitting ? 'Saving…' : 'Record exit'}
            </button>
          </div>
          {closeError && <div className="notice neg" role="alert" style={{ marginTop: 12 }}>{closeError}</div>}
        </div>
      )}
    </div>
  );
}

/* ── the page ──────────────────────────────────────────────────────────── */
export default function DashboardRender(p) {
  const {
    kpis, accountLabel, span, RangePicker,
    goalsNode, onToggleGoals, showGoals,
    accountId, onDayClick, onOpenDetail, onViewAllTrades,
    recentTrades, edgeReport, goals,
  } = p;

  const k = kpis || {};
  const days = k.daily_pnl || [];
  const net = k.total_net_pnl || 0;
  const cents = Math.abs(net % 1).toFixed(2).slice(1);

  const awin = Math.abs(k.avg_win || 0);
  const aloss = Math.abs(k.avg_loss || 0);
  const ratio = aloss > 0 ? awin / aloss : null;
  const avgR = k.avg_r == null ? null : Number(k.avg_r);

  const greenDays = days.filter((d) => Number(d.net_pnl || 0) > 0);
  const redDays = days.filter((d) => Number(d.net_pnl || 0) < 0);
  const bestDay = days.length
    ? days.reduce((a, b) => Number(a.net_pnl || 0) >= Number(b.net_pnl || 0) ? a : b)
    : null;
  const worstDay = days.length
    ? days.reduce((a, b) => Number(a.net_pnl || 0) <= Number(b.net_pnl || 0) ? a : b)
    : null;
  const avgRedDay = redDays.length
    ? Math.abs(redDays.reduce((s, d) => s + Number(d.net_pnl || 0), 0) / redDays.length)
    : 0;
  const lossOutlierRatio = worstDay && avgRedDay > 0
    ? Math.abs(Number(worstDay.net_pnl || 0)) / avgRedDay
    : null;

  const strategyTagged = k.edge_dimensions?.strategy?.coverage_count
    ?? (k.by_strategy || []).reduce((s, r) => s + Number(r.count || 0), 0);
  const strategyCoverage = k.total_trades ? strategyTagged / k.total_trades : 0;
  const rSamples = k.r_sample_count != null
    ? Number(k.r_sample_count)
    : (edgeReport?.r_multiple_dist || []).reduce((s, r) => s + Number(r.count || 0), 0);
  const rCoverage = k.total_trades ? rSamples / k.total_trades : 0;
  const processReady = k.total_trades >= 10 && strategyCoverage >= 0.6 && rCoverage >= 0.6;
  const lossContainGoal = goals?.loss_containment ?? 2.0;

  const trainingFocus = [];
  if (rCoverage < 0.6) {
    trainingFocus.push({
      title: 'Record planned risk',
      body: `${rSamples} of ${k.total_trades || 0} trades have R data. Initial stop/risk is required before a process score is trustworthy.`,
      evidence: 'INSUFFICIENT DATA',
    });
  }
  if (strategyCoverage < 0.6) {
    trainingFocus.push({
      title: 'Tag the setup before review',
      body: `${strategyTagged} of ${k.total_trades || 0} trades are strategy-tagged. Setup coverage is too low to identify your best repeatable edge.`,
      evidence: 'RECORDED',
    });
  }
  if (lossOutlierRatio != null && lossOutlierRatio > lossContainGoal) {
    trainingFocus.push({
      title: 'Reduce outlier losing days',
      body: `Your worst session was ${lossOutlierRatio.toFixed(1)}× the average red day. Consistency improves fastest by containing the tail loss.`,
      evidence: 'VERIFIED',
    });
  }
  if (ratio != null && ratio < 1) {
    trainingFocus.push({
      title: 'Improve payoff',
      body: 'Average winners are smaller than average losers. Protect downside or allow high-quality winners more room.',
      evidence: 'VERIFIED',
    });
  }
  if (!trainingFocus.length) {
    trainingFocus.push({
      title: 'Protect the repeatable process',
      body: 'No major verified gap stands out in this range. Keep size, setups and planned risk consistent.',
      evidence: 'VERIFIED',
    });
  }

  // These are the names the goals API actually returns. An earlier version
  // invented *_goal keys, so every goal silently fell back to a default and a
  // saved change never appeared on the card.
  const g = goals || {};
  const gWin = g.win_rate ?? 65;
  const gPf = g.profit_factor ?? 1.5;
  const gRatio = g.avg_win_loss_ratio ?? 1.5;
  const gExp = g.expectancy ?? 50;
  const gAvgR = g.avg_r ?? 0.5;
  const gLossContain = g.loss_containment ?? lossContainGoal;
  const cap = (x) => Math.max(0, Math.min(1, x));
  // Keep the goal marker inside a readable scale instead of pinning every
  // baseline to the far edge. Avg R is higher-is-better; loss containment is
  // lower-is-better, so its bar grows toward the warning side.
  const avgRScale = Math.max(gAvgR / 0.8, Math.abs(avgR || 0) * 1.05, 0.1);
  const lossScale = Math.max(gLossContain / 0.35, (lossOutlierRatio || 0) * 1.05, 0.1);

  const measures = [
    {
      label: 'Expectancy', value: money2(k.expectancy || 0),
      fill: cap((k.expectancy || 0) / gExp), goal: `$${gExp}`, goalPct: 100,
      met: (k.expectancy || 0) >= gExp,
      read: 'Average net value of each completed trade',
    },
    {
      label: 'Profit factor',
      value: k.profit_factor == null ? '—' : Number(k.profit_factor).toFixed(2),
      fill: cap((k.profit_factor || 0) / gPf), goal: Number(gPf).toFixed(2), goalPct: 100,
      met: (k.profit_factor || 0) >= gPf,
      read: `$${Number(k.profit_factor || 0).toFixed(2)} won for every $1.00 lost`,
    },
    {
      label: 'Trade win rate', value: `${(k.win_rate || 0).toFixed(1)}%`,
      fill: cap((k.win_rate || 0) / 100), goal: `${gWin}%`, goalPct: cap(gWin / 100) * 100,
      met: (k.win_rate || 0) >= gWin,
      read: `${(k.winning_trades || 0).toLocaleString()} won, ${(k.losing_trades || 0).toLocaleString()} lost`,
    },
    {
      label: 'Payoff ratio', value: ratio == null ? '—' : ratio.toFixed(2),
      fill: cap((ratio || 0) / gRatio), goal: Number(gRatio).toFixed(2), goalPct: 100,
      met: ratio != null && ratio >= gRatio,
      read: (
        <>Average win <b>$${Math.round(awin).toLocaleString()}</b> · average loss{' '}
          <b>$${Math.round(aloss).toLocaleString()}</b></>
      ),
    },
    {
      label: 'Avg R / trade',
      value: avgR == null ? 'N/A' : `${avgR > 0 ? '+' : ''}${avgR.toFixed(2)}R`,
      fill: cap(Math.max(avgR || 0, 0) / avgRScale),
      goal: `${Number(gAvgR).toFixed(2)}R`,
      goalPct: cap(gAvgR / avgRScale) * 100,
      tone: avgR != null && avgR < 0 ? 'neg' : undefined,
      met: avgR != null && avgR >= gAvgR,
      read: avgR == null
        ? `Record planned risk to unlock this metric · 0 of ${k.total_trades || 0} trades`
        : `Average realized R across ${rSamples} recorded trade${rSamples === 1 ? '' : 's'}`,
    },
    {
      label: 'Loss containment',
      value: lossOutlierRatio == null ? 'N/A' : `${lossOutlierRatio.toFixed(1)}×`,
      fill: cap((lossOutlierRatio || 0) / lossScale),
      goal: `≤${Number(gLossContain).toFixed(1)}×`,
      goalPct: cap(gLossContain / lossScale) * 100,
      tone: lossOutlierRatio != null && lossOutlierRatio > gLossContain ? 'neg' : undefined,
      met: lossOutlierRatio != null && lossOutlierRatio <= gLossContain,
      read: lossOutlierRatio == null
        ? 'Needs at least one losing session'
        : 'Worst red day vs average red day',
    },
  ];

  const readout = (() => {
    if (!days.length) return null;
    const last = days[days.length - 1];
    return { label: 'Last session', value: last.net_pnl, when: shortDate(last.date) };
  })();

  return (
    <div>
      {/* 1. the account band */}
      <div className="v3-hero">
        <div className="v3-eyeline">
          <div>
            <p className="v3-acct">{accountLabel}{span ? ` · ${span}` : ''}</p>
            <h1 className={`v3-money ${tone(net)}`}>
              {money(net)}<span className="cents">{cents}</span>
            </h1>
            <p className="v3-money-sub">
              <b>{(k.trading_days || 0).toLocaleString()} sessions</b>, {(k.total_trades || 0).toLocaleString()} trades.
              {' '}You kept <b>${Math.round(net).toLocaleString()}</b> of{' '}
              <b>${Math.round(k.total_gross_pnl || 0).toLocaleString()}</b> gross; commissions took{' '}
              <b>${Math.round(Math.abs(k.total_commissions || 0)).toLocaleString()}</b>.
            </p>
          </div>
          <div className="v3-heroside">
            <div className="v3-acts">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={onToggleGoals}
                aria-pressed={showGoals}
                aria-expanded={showGoals}
              >
                Edit goals
              </button>
              {RangePicker}
            </div>
            {readout && (
              <dl className="v3-readout">
                <dt className="v3-lab">{readout.label}</dt>
                <dd className={tone(readout.value)}>{money(readout.value)}</dd>
                <div className="when">{readout.when}</div>
              </dl>
            )}
          </div>
        </div>


      </div>

      {/* 2. the measures line */}
      <Measures items={measures} />

      {goalsNode}

      {/* 3. performance trend — the same verified daily P&L, two useful views */}
      <section className="v3-band">
        <div className="v3-sec-head">
          <div>
            <h2 className="v3-h">Performance trend</h2>
            <p className="v3-h-sub">Is the edge compounding, and which sessions are moving the account?</p>
          </div>
          <span className="v3-evidence verified">VERIFIED</span>
        </div>
        <div className="v3-chart-grid">
          <div className="v3-chart-panel">
            <div className="v3-chart-title">
              <div>
                <div className="v3-lab">Equity curve</div>
                <strong>Net account growth</strong>
              </div>
              <span>after commissions</span>
            </div>
            <EquityCurve days={days} height={210} onPick={onDayClick} />
          </div>
          <div className="v3-chart-panel">
            <div className="v3-chart-title">
              <div>
                <div className="v3-lab">Daily net P&amp;L</div>
                <strong>{k.trading_days || 0} sessions</strong>
              </div>
              <span>click a bar to review the day</span>
            </div>
            <DailyPnlBars days={days} height={210} onPick={onDayClick} />
          </div>
        </div>
        <div className="v3-tod-panel">
          <div className="v3-chart-title v3-tod-title">
            <div>
              <div className="v3-lab">Time-of-day edge</div>
              <strong>Entry window performance</strong>
            </div>
            <span>30-minute buckets · first broker-recorded entry · {k.entry_time_timezone || 'CT'}</span>
          </div>
          <TimeOfDayBars rows={k.by_entry_time || []} timezone={k.entry_time_timezone || 'CT'} />
        </div>
      </section>

      {/* 4. training system — verified facts first, process score only when evidence exists */}
      <section className="v3-band">
        <div className="v3-sec-head">
          <div>
            <h2 className="v3-h">Consistency &amp; training</h2>
            <p className="v3-h-sub">Use repeatability, risk control and process coverage to decide what to practice next.</p>
          </div>
        </div>
        <div className="v3-training-grid">
          <article className="v3-training-card">
            <div className="v3-training-head">
              <span className="v3-lab">Consistency</span>
              <span className="v3-evidence verified">VERIFIED</span>
            </div>
            <div className="v3-training-value">{Number(k.day_win_rate || 0).toFixed(1)}%</div>
            <p>{greenDays.length} green sessions · {redDays.length} red sessions</p>
            <dl className="v3-training-stats">
              <div><dt>Best day</dt><dd className={bestDay ? tone(bestDay.net_pnl) : 'v3-flat'}>{bestDay ? money(bestDay.net_pnl) : '—'}</dd></div>
              <div><dt>Worst day</dt><dd className={worstDay ? tone(worstDay.net_pnl) : 'v3-flat'}>{worstDay ? money(worstDay.net_pnl) : '—'}</dd></div>
            </dl>
          </article>

          <article className="v3-training-card">
            <div className="v3-training-head">
              <span className="v3-lab">Risk control</span>
              <span className="v3-evidence verified">VERIFIED</span>
            </div>
            <div className="v3-training-value v3-neg">{money(k.max_drawdown || 0)}</div>
            <p>Maximum realized drawdown in the selected range</p>
            <dl className="v3-training-stats">
              <div><dt>Avg red day</dt><dd>{redDays.length ? money(-avgRedDay) : '—'}</dd></div>
              <div><dt>Worst / avg red</dt><dd>{lossOutlierRatio == null ? '—' : `${lossOutlierRatio.toFixed(1)}×`}</dd></div>
            </dl>
          </article>

          <article className="v3-training-card">
            <div className="v3-training-head">
              <span className="v3-lab">Process score</span>
              <span className={`v3-evidence ${processReady ? 'recorded' : 'insufficient'}`}>
                {processReady ? 'RECORDED' : 'INSUFFICIENT DATA'}
              </span>
            </div>
            <div className="v3-training-value">{processReady ? 'Ready' : 'N/A'}</div>
            <p>{processReady ? 'Enough setup and R evidence exists to begin process grading.' : 'The journal will not invent a discipline score without enough planned-risk and setup evidence.'}</p>
            <dl className="v3-training-stats">
              <div><dt>Setup coverage</dt><dd>{Math.round(strategyCoverage * 100)}%</dd></div>
              <div><dt>R-plan coverage</dt><dd>{Math.round(rCoverage * 100)}%</dd></div>
            </dl>
          </article>

          <article className="v3-training-card v3-training-focus">
            <div className="v3-training-head">
              <span className="v3-lab">Training focus</span>
            </div>
            <ol>
              {trainingFocus.slice(0, 3).map((item, i) => (
                <li key={i}>
                  <span className={`v3-evidence ${item.evidence === 'VERIFIED' ? 'verified' : item.evidence === 'RECORDED' ? 'recorded' : 'insufficient'}`}>
                    {item.evidence}
                  </span>
                  <b>{item.title}</b>
                  <p>{item.body}</p>
                </li>
              ))}
            </ol>
          </article>
        </div>
      </section>

      {/* 5. calendar and live account activity */}
      <section className="v3-band">
        <div className="v3-split">
          <div>
            <MonthPanel
              accountId={accountId}
              onDayClick={onDayClick}
              latestDate={days.length ? days[days.length - 1].date : null}
            />
          </div>
          <div className="v3-rightcol">
            <div>
              <div className="v3-sec-head">
                <div>
                  <h2 className="v3-h">Recent trades</h2>
                  <p className="v3-h-sub">The last ten closed</p>
                </div>
                <button type="button" className="btn btn-secondary btn-sm" onClick={onViewAllTrades}>View all</button>
              </div>
              {!recentTrades?.length ? (
                <div className="v3-empty">No trades in this range.</div>
              ) : (
                <div className="v3-scroll">
                  <table className="v3-t">
                    <thead>
                      <tr>
                        <th>Ticker</th>
                        <th className="v3-hide-s">Side</th>
                        <th className="r">Date</th>
                        <th className="r">P&amp;L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recentTrades.slice(0, 10).map((t, i) => (
                        <tr
                          key={i}
                          className="clickable"
                          tabIndex={0}
                          onClick={() => onOpenDetail && onOpenDetail(t)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenDetail && onOpenDetail(t); }
                          }}
                          aria-label={`Open ${t.ticker} trade`}
                        >
                          <td className="v3-tick">{t.ticker}</td>
                          <td className="v3-hide-s v3-side">{(t.side || '').toLowerCase() === 'short' ? 'Short' : 'Long'}</td>
                          <td className="r v3-mono v3-read">{t.date}</td>
                          <td className={`r v3-mono ${tone(t.net_pnl)}`} style={{ fontWeight: 600 }}>{money2(t.net_pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <OpenPositions {...p} />
          </div>
        </div>
      </section>

      {/* 6. edge breakdowns */}
      <section className="v3-band">
        <Patterns dimensions={k.edge_dimensions} onViewAll={onViewAllTrades} />
      </section>
    </div>
  );
}
