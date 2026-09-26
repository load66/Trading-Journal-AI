/* The V3 Today page. Presentation only: every value, handler and piece of
   state is passed in from Dashboard.js, so no behaviour lives here. */
import { useState, useEffect } from 'react';
import { X, Trophy, Clock3, Target, ShieldAlert, Lightbulb, FileText, CheckCircle2, AlertTriangle } from 'lucide-react';
import { calendarApi } from '../api';
import {
  Measures, EquityCurve, DailyPnlBars, MonthGrid,
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
          <MonthGrid year={year} month={month} byDay={byDay} today={todayKey} onPick={onDayClick} showWeek={false} />
        </div>
      )}
    </>
  );
}


/* ── management training cards ─────────────────────────────────────────── */
const absMoney = (value) => {
  const n = Math.abs(Number(value) || 0);
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const compactDateTime = (value) => {
  if (!value) return '';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });
};

function TradeManagement({ kpis, edge, range, onRangeChange, goals }) {
  const data = kpis || {};
  const hold = edge?.hold_time || {};
  const capture = data.exit_efficiency == null ? null : Number(data.exit_efficiency);
  const captureBar = capture == null ? 0 : Math.max(0, Math.min(100, capture));
  const captureGoal = Number(goals?.exit_efficiency ?? 60);
  const leftOnTable = capture == null ? null : Math.max(0, 100 - Math.min(100, capture));

  const winnerHold = hold.winners_avg_min == null ? null : Number(hold.winners_avg_min);
  const loserHold = hold.losers_avg_min == null ? null : Number(hold.losers_avg_min);
  const holdRatio = winnerHold > 0 && loserHold != null ? loserHold / winnerHold : null;

  const mfe = data.avg_mfe == null ? null : Number(data.avg_mfe);
  const mae = data.avg_mae == null ? null : Number(data.avg_mae);
  const excursionN = Number(data.excursion_n || 0);

  const bottom = [];
  if (capture != null) {
    bottom.push(
      capture >= captureGoal
        ? 'You captured ' + capture.toFixed(0) + '% of the available favorable move on winning trades with excursion data, above your ' + captureGoal.toFixed(0) + '% goal.'
        : 'You captured ' + capture.toFixed(0) + '% of the available favorable move on winning trades with excursion data, below your ' + captureGoal.toFixed(0) + '% goal by ' + (captureGoal - capture).toFixed(0) + ' points.'
    );
  }
  if (holdRatio != null) {
    if (holdRatio > 1.05) {
      bottom.push(
        'Losing trades averaged ' + loserHold.toFixed(1) + ' minutes versus ' + winnerHold.toFixed(1) +
        ' minutes for winners — about ' + Math.round((holdRatio - 1) * 100) +
        '% longer. The clearest management leak in this sample is time spent in losing trades.'
      );
    } else if (holdRatio < 0.95) {
      bottom.push(
        'Losers averaged ' + loserHold.toFixed(1) + ' minutes versus ' + winnerHold.toFixed(1) +
        ' minutes for winners. You are cutting losing trades faster than winning trades in this sample.'
      );
    } else {
      bottom.push(
        'Winner and loser hold times are similar (' + winnerHold.toFixed(1) + ' vs ' +
        loserHold.toFixed(1) + ' minutes), so duration is not a strong differentiator in this sample.'
      );
    }
  }
  if (mfe != null && mae != null) {
    bottom.push(
      'Across ' + excursionN + ' trades with market-path data, average favorable excursion was +' +
      mfe.toFixed(2) + '% and average adverse excursion was -' + mae.toFixed(2) + '% before exit.'
    );
  }
  if (!bottom.length) {
    bottom.push(
      'Trade-management evidence is incomplete for this window. Review trades with timestamps and excursion data before drawing a management conclusion.'
    );
  }

  return (
    <>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h v3-icon-title"><Target size={18} /> Trade management</h2>
          <p className="v3-h-sub">How well do you manage trades after you enter?</p>
        </div>
        <div className="v3-management-range" role="group" aria-label="Trade management range">
          {['7D', '30D', '90D', 'ALL'].map((option) => (
            <button
              key={option}
              type="button"
              className={range === option ? 'active' : ''}
              aria-pressed={range === option}
              onClick={() => onRangeChange?.(option)}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      <div className="v3-management-grid">
        <article className="v3-management-card">
          <div className="v3-management-label"><Trophy size={17} /><span>Profit capture</span></div>
          <p>How much of the available move you actually keep on winning trades.</p>
          <div className="v3-management-value v3-pos">{capture == null ? 'N/A' : capture.toFixed(0) + '%'}</div>
          <div className="v3-management-progress"><i style={{ '--w': captureBar + '%' }} /></div>
          <div className="v3-management-meta">
            <span>Goal ≥ {captureGoal.toFixed(0)}%</span>
            <span>{excursionN ? excursionN + ' measured trades' : 'No excursion sample'}</span>
          </div>
          <div className={'v3-management-callout ' + (capture != null && capture >= captureGoal ? 'good' : 'caution')}>
            {capture == null
              ? 'Excursion data is required to score profit capture.'
              : capture >= captureGoal
                ? 'You are retaining a solid share of winning-trade opportunity.'
                : 'There is room to improve winner monetization without assuming every peak was executable.'}
          </div>
        </article>

        <article className="v3-management-card">
          <div className="v3-management-label"><Clock3 size={17} /><span>Holding behavior</span></div>
          <p>How long you hold winners versus losers.</p>
          <div className="v3-hold-pair">
            <div><span>Winners</span><strong className="v3-pos">{winnerHold == null ? 'N/A' : winnerHold.toFixed(1) + ' min'}</strong></div>
            <div><span>Losers</span><strong className="v3-neg">{loserHold == null ? 'N/A' : loserHold.toFixed(1) + ' min'}</strong></div>
          </div>
          <div className={'v3-management-callout ' + (holdRatio != null && holdRatio > 1.05 ? 'bad' : 'good')}>
            {holdRatio == null
              ? 'Need closed trades with usable entry and exit timestamps.'
              : holdRatio > 1.05
                ? 'Losers stay open about ' + Math.round((holdRatio - 1) * 100) + '% longer than winners.'
                : 'Losers are not being held materially longer than winners.'}
          </div>
        </article>

        <article className="v3-management-card">
          <div className="v3-management-label"><Target size={17} /><span>Profit vs. left on table</span></div>
          <p>On winning trades, how much favorable movement you captured versus what remained.</p>
          <div className="v3-capture-split" aria-label="Captured versus left on table">
            <div className="captured" style={{ '--w': captureBar + '%' }}>
              {capture == null ? 'No data' : captureBar.toFixed(0) + '% Captured'}
            </div>
            <div className="left">{leftOnTable == null ? '' : leftOnTable.toFixed(0) + '% Left'}</div>
          </div>
          <div className="v3-capture-numbers">
            <div><span>Captured</span><strong className="v3-pos">{capture == null ? '—' : capture.toFixed(0) + '%'}</strong></div>
            <div><span>Left on table</span><strong>{leftOnTable == null ? '—' : leftOnTable.toFixed(0) + '%'}</strong></div>
          </div>
          <div className="v3-management-callout good">
            {capture == null
              ? 'This becomes available after excursion metrics are calculated.'
              : 'Use this as a coaching metric, not a demand to sell at the exact intraday high.'}
          </div>
        </article>

        <article className="v3-management-card">
          <div className="v3-management-label"><ShieldAlert size={17} /><span>Risk during trade</span></div>
          <p>How much the market moved for and against you while the trade was open.</p>
          <div className="v3-risk-pair">
            <div><span>Avg favorable move (MFE)</span><strong className="v3-pos">{mfe == null ? 'N/A' : '+' + mfe.toFixed(2) + '%'}</strong></div>
            <div><span>Avg adverse move (MAE)</span><strong className="v3-neg">{mae == null ? 'N/A' : '-' + mae.toFixed(2) + '%'}</strong></div>
          </div>
          <div className="v3-management-callout caution">
            {excursionN
              ? 'Based on ' + excursionN + ' measured trades.' + (data.excursion_option_n ? ' Options use the underlying directional path.' : '')
              : 'No measured excursion sample in this window.'}
          </div>
        </article>
      </div>

      <div className="v3-bottom-line">
        <div className="v3-bottom-line-head">
          <Lightbulb size={20} />
          <div><strong>Bottom line</strong><span>Rule-based analysis from the selected management window</span></div>
        </div>
        <div className="v3-bottom-line-copy">
          {bottom.map((line, index) => (
            <p key={index}><CheckCircle2 size={15} /><span>{line}</span></p>
          ))}
        </div>
      </div>
    </>
  );
}

function LatestSmokingGunSummary({ report, onOpen }) {
  if (!report) {
    return (
      <>
        <div className="v3-sec-head">
          <div>
            <h2 className="v3-h v3-icon-title"><FileText size={18} /> Latest Smoking Gun report summary</h2>
            <p className="v3-h-sub">Key takeaways from your most recent saved audit.</p>
          </div>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onOpen}>Open reports</button>
        </div>
        <div className="v3-empty">No saved Smoking Gun report yet.</div>
      </>
    );
  }

  const metrics = report.source_metrics || {};
  const board = metrics.scoreboard || {};
  const diagnosis = report.diagnosis || {};
  const actionPlan = Array.isArray(report.action_plan)
    ? report.action_plan
    : Array.isArray(diagnosis.action_plan) ? diagnosis.action_plan : [];

  const unique = (items) => [...new Set(items.filter(Boolean).map((x) => String(x).trim()).filter(Boolean))];
  const strengths = unique([...(diagnosis.edge?.where_it_lives || []), report.primary_edge]).slice(0, 3);
  const opportunities = unique([
    ...(diagnosis.edge?.where_it_dies || []),
    report.primary_leak,
    ...actionPlan.map((x) => x?.rule || x?.mechanical_rule),
  ]).slice(0, 3);

  const metricRows = [
    ['Net P&L', money2(board.net_pnl ?? report.net_pnl), tone(board.net_pnl ?? report.net_pnl)],
    ['Win rate', board.win_rate == null ? '—' : Number(board.win_rate).toFixed(1) + '%', ''],
    ['Profit factor', board.profit_factor == null ? '—' : Number(board.profit_factor).toFixed(2), ''],
    ['Trades', report.trade_count ?? '—', ''],
    ['Avg winner', board.avg_winner == null ? '—' : absMoney(board.avg_winner), 'v3-pos'],
    ['Max drawdown', board.max_drawdown == null ? '—' : money2(board.max_drawdown), 'v3-neg'],
  ];

  return (
    <>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h v3-icon-title"><FileText size={18} /> Latest Smoking Gun report summary</h2>
          <p className="v3-h-sub">Key takeaways from your most recent saved detailed analysis.</p>
        </div>
        <div className="v3-smoking-head-actions">
          <span className={'v3-evidence ' + (report.is_stale ? 'insufficient' : 'verified')}>
            {report.is_stale ? 'SOURCE CHANGED' : 'CURRENT'}
          </span>
          <button type="button" className="btn btn-primary btn-sm" onClick={onOpen}>View full report →</button>
        </div>
      </div>

      <div className="v3-smoking-meta">
        <div className="v3-smoking-cover">
          <span className="v3-kicker">TRADING SMOKING GUN REPORT</span>
          <strong>{report.title || 'Latest report'}</strong>
          <small>{report.date_from} → {report.date_to}</small>
          <small>Generated {compactDateTime(report.generated_at)}</small>
          {(report.analysis_provider || report.analysis_model) && (
            <small>Analysis: {[report.analysis_provider, report.analysis_model].filter(Boolean).join(' · ')}</small>
          )}
        </div>

        <div className="v3-smoking-scoreboard">
          <span className="v3-lab">Overall performance</span>
          <div>
            {metricRows.map(([label, value, cls]) => (
              <div key={label}>
                <span>{label}</span>
                <strong className={cls}>{value}</strong>
              </div>
            ))}
          </div>
        </div>

        <div className="v3-smoking-list strengths">
          <div className="v3-smoking-list-title"><Trophy size={15} /> Top strengths</div>
          {strengths.length ? (
            <ol>{strengths.map((item, i) => <li key={i}><b>{i + 1}</b><span>{item}</span></li>)}</ol>
          ) : <p>No AI strength summary was saved with this report.</p>}
        </div>

        <div className="v3-smoking-list opportunities">
          <div className="v3-smoking-list-title"><AlertTriangle size={15} /> Biggest opportunities</div>
          {opportunities.length ? (
            <ol>{opportunities.map((item, i) => <li key={i}><b>{i + 1}</b><span>{item}</span></li>)}</ol>
          ) : <p>No AI opportunity summary was saved with this report.</p>}
        </div>
      </div>
    </>
  );
}

function RecentTradesPanel({ recentTrades, onViewAllTrades, onOpenDetail }) {
  return (
    <div>
      <div className="v3-sec-head">
        <div>
          <h2 className="v3-h">Recent trades</h2>
          <p className="v3-h-sub">The actual ten most recent closed trades</p>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onViewAllTrades}>View all</button>
      </div>
      {!recentTrades?.length ? (
        <div className="v3-empty">No closed trades.</div>
      ) : (
        <div className="v3-scroll">
          <table className="v3-t v3-recent-compact">
            <thead>
              <tr><th>Ticker</th><th className="v3-hide-s">Side</th><th className="r">Date</th><th className="r">P&amp;L</th></tr>
            </thead>
            <tbody>
              {recentTrades.slice(0, 10).map((t, i) => (
                <tr
                  key={i}
                  className="clickable"
                  tabIndex={0}
                  onClick={() => onOpenDetail?.(t)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpenDetail?.(t); }
                  }}
                  aria-label={'Open ' + t.ticker + ' trade'}
                >
                  <td className="v3-tick">{t.ticker}</td>
                  <td className="v3-hide-s v3-side">{(t.side || '').toLowerCase() === 'short' ? 'Short' : 'Long'}</td>
                  <td className="r v3-mono v3-read">{t.date}</td>
                  <td className={'r v3-mono ' + tone(t.net_pnl)} style={{ fontWeight: 600 }}>{money2(t.net_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
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
    recentTrades, goals,
    managementRange, onManagementRangeChange, managementKpis, managementEdge,
    latestSmokingGun, onViewSmokingGun,
  } = p;

  const k = kpis || {};
  const days = k.daily_pnl || [];
  const net = Number(k.total_net_pnl || 0);
  const avgR = k.avg_r == null ? null : Number(k.avg_r);

  const measures = [
    {
      label: 'Average win',
      value: absMoney(k.avg_win),
      tone: 'pos',
      read: 'Average profit on winning trades · ' + (k.winning_trades || 0) + ' wins',
    },
    {
      label: 'Average loss',
      value: absMoney(k.avg_loss),
      tone: 'neg',
      read: 'Average loss on losing trades · ' + (k.losing_trades || 0) + ' losses',
    },
    {
      label: 'Win rate',
      value: Number(k.win_rate || 0).toFixed(1) + '%',
      read: (k.winning_trades || 0) + ' wins / ' + (k.total_trades || 0) + ' trades',
    },
    {
      label: 'Profit factor',
      value: k.profit_factor == null ? '—' : Number(k.profit_factor).toFixed(2),
      read: k.profit_factor == null ? 'Needs both wins and losses' : 'Net winning P&L ÷ absolute net losing P&L',
    },
    {
      label: 'Expectancy',
      value: money2(k.expectancy || 0),
      tone: Number(k.expectancy || 0) < 0 ? 'neg' : 'pos',
      read: 'Average net P&L per completed trade',
    },
    {
      label: 'Avg R / trade',
      value: avgR == null ? 'N/A' : (avgR > 0 ? '+' : '') + avgR.toFixed(2) + 'R',
      tone: avgR != null && avgR < 0 ? 'neg' : undefined,
      read: avgR == null ? 'Record planned risk to unlock' : (k.r_sample_count || 0) + ' trades with recorded R',
    },
    {
      label: 'Max drawdown',
      value: money2(k.max_drawdown || 0),
      tone: 'neg',
      read: 'Largest realized peak-to-trough drawdown',
    },
  ];

  const readout = days.length ? days[days.length - 1] : null;

  return (
    <div>
      <div className="v3-hero v3-hero-compact">
        <div className="v3-eyeline">
          <div>
            <p className="v3-acct">{accountLabel}{span ? ' · ' + span : ''}</p>
            <h1 className={'v3-money ' + tone(net)}>{money2(net)}</h1>
            <p className="v3-money-sub">
              Avg win <b className="v3-pos">{absMoney(k.avg_win)}</b> / Avg loss <b className="v3-neg">{absMoney(k.avg_loss)}</b>
              {' '}· {(k.total_trades || 0).toLocaleString()} completed trades
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
                <dt className="v3-lab">Last session</dt>
                <dd className={tone(readout.net_pnl)}>{money2(readout.net_pnl)}</dd>
                <div className="when">{shortDate(readout.date)}</div>
              </dl>
            )}
          </div>
        </div>
      </div>

      <Measures items={measures} className="v3-measures-dashboard" />
      {goalsNode}

      <div className="v3-dashboard-core">
        <main className="v3-dashboard-main">
          <section className="v3-band v3-dashboard-section">
            <div className="v3-sec-head">
              <div>
                <h2 className="v3-h">Performance trend</h2>
                <p className="v3-h-sub">Is the edge compounding, and which sessions are moving the account?</p>
              </div>
              <span className="v3-evidence verified">VERIFIED</span>
            </div>
            <div className="v3-chart-grid v3-chart-grid-dashboard">
              <div className="v3-chart-panel">
                <div className="v3-chart-title">
                  <div>
                    <div className="v3-lab">Cumulative P&amp;L</div>
                    <strong className={tone(net)}>{money2(net)}</strong>
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
          </section>

          <section className="v3-band v3-dashboard-section v3-management-section">
            <TradeManagement
              kpis={managementKpis || k}
              edge={managementEdge}
              range={managementRange || '30D'}
              onRangeChange={onManagementRangeChange}
              goals={goals}
            />
          </section>
        </main>

        <aside className="v3-dashboard-side">
          <section className="v3-side-section">
            <MonthPanel
              accountId={accountId}
              onDayClick={onDayClick}
              latestDate={days.length ? days[days.length - 1].date : null}
            />
          </section>
          <section className="v3-side-section">
            <RecentTradesPanel
              recentTrades={recentTrades}
              onViewAllTrades={onViewAllTrades}
              onOpenDetail={onOpenDetail}
            />
          </section>
          <section className="v3-side-section">
            <OpenPositions {...p} />
          </section>
        </aside>
      </div>

      <section className="v3-band v3-smoking-band">
        <LatestSmokingGunSummary report={latestSmokingGun} onOpen={onViewSmokingGun} />
      </section>
    </div>
  );
}
