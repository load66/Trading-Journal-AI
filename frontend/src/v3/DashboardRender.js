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
  const holdMax = Math.max(winnerHold || 0, loserHold || 0, 1);
  const winnerHoldPct = winnerHold == null ? 0 : Math.max(6, Math.min(100, (winnerHold / holdMax) * 100));
  const loserHoldPct = loserHold == null ? 0 : Math.max(6, Math.min(100, (loserHold / holdMax) * 100));
  const favorableMove = mfe == null ? null : Math.abs(mfe);
  const adverseMove = mae == null ? null : Math.abs(mae);
  const moveMax = Math.max(favorableMove || 0, adverseMove || 0, 1);
  const favorablePct = favorableMove == null ? 0 : Math.max(6, Math.min(100, (favorableMove / moveMax) * 100));
  const adversePct = adverseMove == null ? 0 : Math.max(6, Math.min(100, (adverseMove / moveMax) * 100));

  const captureState = capture == null
    ? { tone: 'neutral', label: 'NEED DATA' }
    : capture >= captureGoal
      ? { tone: 'good', label: 'ABOVE GOAL' }
      : { tone: 'caution', label: 'BELOW GOAL' };

  const holdState = holdRatio == null
    ? { tone: 'neutral', label: 'NEED DATA' }
    : holdRatio > 1.05
      ? { tone: 'bad', label: 'LOSERS HELD LONGER' }
      : { tone: 'good', label: 'CONTROLLED' };

  const riskState = favorableMove == null || adverseMove == null
    ? { tone: 'neutral', label: 'NEED DATA' }
    : adverseMove > favorableMove
      ? { tone: 'bad', label: 'ADVERSE > FAVORABLE' }
      : { tone: 'good', label: 'FAVORABLE > ADVERSE' };

  const primaryInsight = (() => {
    if (holdRatio != null && holdRatio > 1.05) {
      return {
        tone: 'bad',
        title: 'Cut losing trades faster',
        copy: 'The clearest management leak is excess time spent in losing trades. Treat invalidation as a decision point, not a negotiation.',
      };
    }
    if (capture != null && capture < captureGoal) {
      return {
        tone: 'caution',
        title: 'Improve winner monetization',
        copy: 'Your next management priority is preserving more of the favorable move without trying to capture the exact high.',
      };
    }
    if (mfe != null && mae != null && mae > mfe) {
      return {
        tone: 'bad',
        title: 'Reduce adverse excursion',
        copy: 'Trades are moving farther against you than for you on average. Tighten invalidation and position-risk discipline.',
      };
    }
    return {
      tone: 'good',
      title: 'Protect the process',
      copy: 'No single management leak dominates this window. Keep the same exit discipline and focus on repeatability.',
    };
  })();

  return (
    <>
      <div className="v3-sec-head v3-management-head">
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

      <div className="v3-management-grid v3-management-grid-clean">
        <article className="v3-management-card v3-management-card-feature v3-management-card-capture">
          <div className="v3-management-card-top">
            <div className="v3-management-label"><Trophy size={17} /><span>Profit capture</span></div>
            <span className={'v3-management-status ' + captureState.tone}>{captureState.label}</span>
          </div>
          <p className="v3-management-question">How much of the available favorable move are you actually keeping?</p>

          <div className="v3-capture-score">
            <strong className={capture != null && capture >= 0 ? 'v3-pos' : 'v3-neg'}>
              {capture == null ? 'N/A' : capture.toFixed(0) + '%'}
            </strong>
          </div>
          <div
            className={`v3-management-progress v3-capture-progress${capture == null ? ' is-empty' : ''}`}
            role={capture == null ? 'status' : 'meter'}
            aria-label={capture == null ? 'Profit capture unavailable' : 'Profit capture'}
            aria-valuemin={capture == null ? undefined : 0}
            aria-valuemax={capture == null ? undefined : 100}
            aria-valuenow={capture == null ? undefined : captureBar}
            aria-valuetext={capture == null ? undefined : `${capture.toFixed(0)}% exit efficiency; goal ${captureGoal.toFixed(0)}%`}
          >
            <i style={{ '--w': captureBar + '%' }} aria-hidden="true" />
            <span className="v3-capture-goal" style={{ '--goal': Math.max(0, Math.min(100, captureGoal)) + '%' }} aria-hidden="true" />
          </div>

          <div className="v3-management-meta">
            <span>Goal ≥ {captureGoal.toFixed(0)}%</span>
            <span>{excursionN ? excursionN + ' measured trades' : 'No excursion sample'}</span>
          </div>

          <div className={'v3-management-callout ' + (capture != null && capture >= captureGoal ? 'good' : capture != null && capture < 0 ? 'bad' : 'caution')}>
            <b>What it means:</b>{' '}
            {capture == null
              ? 'Excursion data is required before profit capture can be scored.'
              : capture >= captureGoal
                ? 'You are retaining enough of the favorable move to clear your current target.'
                : capture < 0
                  ? 'The favorable move was fully given back before exit on average. Exit discipline is the immediate priority.'
                  : 'Too much of the favorable move is being given back before exit. Improve exit structure, not prediction.'}
          </div>
        </article>

        <article className="v3-management-card">
          <div className="v3-management-card-top">
            <div className="v3-management-label"><Clock3 size={17} /><span>Holding behavior</span></div>
            <span className={'v3-management-status ' + holdState.tone}>{holdState.label}</span>
          </div>
          <p className="v3-management-question">Are you giving losing trades more time than winning trades?</p>

          <div className="v3-comparison-list" aria-label="Average hold time comparison">
            <div className="v3-comparison-row">
              <div className="v3-comparison-value">
                <span>Winning trades</span>
                <strong className="v3-pos">{winnerHold == null ? 'N/A' : winnerHold.toFixed(1) + ' min'}</strong>
              </div>
              <div className="v3-comparison-track" aria-hidden="true"><i className="pos" style={{ '--w': winnerHoldPct + '%' }} /></div>
            </div>
            <div className="v3-comparison-row">
              <div className="v3-comparison-value">
                <span>Losing trades</span>
                <strong className="v3-neg">{loserHold == null ? 'N/A' : loserHold.toFixed(1) + ' min'}</strong>
              </div>
              <div className="v3-comparison-track" aria-hidden="true"><i className="neg" style={{ '--w': loserHoldPct + '%' }} /></div>
            </div>
          </div>

          <div className={'v3-management-callout ' + (holdRatio != null && holdRatio > 1.05 ? 'bad' : 'good')}>
            <b>What it means:</b>{' '}
            {holdRatio == null
              ? 'Closed trades need usable entry and exit timestamps before holding behavior can be compared.'
              : holdRatio > 1.05
                ? 'Losing trades are being given more time. Faster invalidation is the clearest correction.'
                : 'Losing trades are not being held materially longer than winners.'}
          </div>
        </article>

        <article className="v3-management-card v3-management-card-giveback">
          <div className="v3-management-card-top">
            <div className="v3-management-label"><Target size={17} /><span>Left on table</span></div>
            <span className={'v3-management-status ' + (leftOnTable == null ? 'neutral' : leftOnTable <= 40 ? 'good' : 'caution')}>
              {leftOnTable == null ? 'NEED DATA' : leftOnTable <= 40 ? 'LOW GIVEBACK' : 'HIGH GIVEBACK'}
            </span>
          </div>
          <p className="v3-management-question">How much favorable movement are you giving back before exit?</p>

          <div className="v3-giveback-score">
            <strong className={leftOnTable != null && leftOnTable > 40 ? 'v3-neg' : 'v3-pos'}>
              {leftOnTable == null ? 'N/A' : leftOnTable.toFixed(0) + '%'}
            </strong>
            <span>unretained favorable movement</span>
          </div>

          <div className="v3-giveback-track" aria-hidden="true">
            <i style={{ '--w': leftOnTable == null ? '0%' : Math.max(0, Math.min(100, leftOnTable)) + '%' }} />
          </div>

          <div className={'v3-management-callout ' + (leftOnTable != null && leftOnTable <= 40 ? 'good' : 'caution')}>
            <b>What it means:</b>{' '}
            {leftOnTable == null
              ? 'There is not enough excursion data to measure giveback.'
              : leftOnTable <= 40
                ? 'Giveback is contained; most of the favorable move is being retained.'
                : capture < 0
                  ? 'Exit efficiency is negative in this window, meaning gains were fully surrendered and then some before exit.'
                  : 'A large share of the favorable move is being surrendered before exit.'}
          </div>
        </article>

        <article className="v3-management-card">
          <div className="v3-management-card-top">
            <div className="v3-management-label"><ShieldAlert size={17} /><span>Risk during trade</span></div>
            <span className={'v3-management-status ' + riskState.tone}>{riskState.label}</span>
          </div>
          <p className="v3-management-question">How far do trades move for you versus against you before exit?</p>

          <div className="v3-comparison-list" aria-label="Favorable and adverse move comparison">
            <div className="v3-comparison-row">
              <div className="v3-comparison-value">
                <span>Favorable move <small>MFE</small></span>
                <strong className="v3-pos">{favorableMove == null ? 'N/A' : '+' + favorableMove.toFixed(2) + '%'}</strong>
              </div>
              <div className="v3-comparison-track" aria-hidden="true"><i className="pos" style={{ '--w': favorablePct + '%' }} /></div>
            </div>
            <div className="v3-comparison-row">
              <div className="v3-comparison-value">
                <span>Adverse move <small>MAE</small></span>
                <strong className="v3-neg">{adverseMove == null ? 'N/A' : '-' + adverseMove.toFixed(2) + '%'}</strong>
              </div>
              <div className="v3-comparison-track" aria-hidden="true"><i className="neg" style={{ '--w': adversePct + '%' }} /></div>
            </div>
          </div>

          <div className={'v3-management-callout ' + (riskState.tone === 'bad' ? 'bad' : 'caution')}>
            <b>What it means:</b>{' '}
            {excursionN
              ? (adverseMove != null && favorableMove != null && adverseMove > favorableMove
                ? 'Trades are moving farther against you than for you on average. Tighten invalidation and position risk.'
                : 'Favorable excursion is at least as large as adverse excursion in this window.')
                + ' Based on ' + excursionN + ' measured trades.'
                + (data.excursion_option_n ? ' Options use the underlying directional path.' : '')
              : 'No measured excursion sample is available in this window.'}
          </div>
        </article>
      </div>

      <div className={'v3-bottom-line v3-bottom-line-clean ' + primaryInsight.tone}>
        <div className="v3-bottom-line-head">
          <Lightbulb size={20} />
          <div><strong>Bottom line</strong><span>Your single most important management priority</span></div>
        </div>
        <div className="v3-bottom-line-copy">
          <p>
            <CheckCircle2 size={15} />
            <span><b>{primaryInsight.title}.</b> {primaryInsight.copy}</span>
          </p>
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
            <p className="v3-h-sub">Diagnosis and mechanical actions only — dashboard statistics stay above.</p>
          </div>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onOpen}>Open reports</button>
        </div>
        <div className="v3-empty">No saved Smoking Gun report yet.</div>
      </>
    );
  }

  const diagnosis = report.diagnosis || {};
  const actionPlan = Array.isArray(report.action_plan)
    ? report.action_plan
    : Array.isArray(diagnosis.action_plan) ? diagnosis.action_plan : [];

  const firstText = (...values) => values
    .flat()
    .filter(Boolean)
    .map((value) => typeof value === 'string' ? value.trim() : '')
    .find(Boolean);

  const diagnosisRows = [
    {
      label: 'Main leak',
      tone: 'bad',
      value: firstText(
        report.primary_leak,
        diagnosis.primary_leak,
        diagnosis.main_leak,
        diagnosis.edge?.where_it_dies
      ) || 'No primary leak was saved with this report.',
    },
    {
      label: 'Key behavioral issue',
      tone: 'caution',
      value: firstText(
        diagnosis.key_behavioral_issue,
        diagnosis.behavioral_issue,
        diagnosis.behavioral_pattern,
        diagnosis.behavior?.issue
      ) || 'No behavioral issue was saved with this report.',
    },
    {
      label: 'Edge to protect',
      tone: 'good',
      value: firstText(
        report.primary_edge,
        diagnosis.primary_edge,
        diagnosis.edge?.where_it_lives
      ) || 'No protected edge was saved with this report.',
    },
  ];

  const planRows = actionPlan
    .map((item, index) => {
      if (typeof item === 'string') return { title: item, detail: '' };
      return {
        title: firstText(item?.mechanical_rule, item?.rule, item?.action, item?.title, item?.what) || 'Action ' + (index + 1),
        detail: firstText(item?.trigger, item?.implementation, item?.how, item?.rationale, item?.why) || '',
      };
    })
    .filter((item) => item.title)
    .slice(0, 5);

  return (
    <>
      <div className="v3-sec-head v3-smoking-summary-head">
        <div>
          <h2 className="v3-h v3-icon-title"><FileText size={18} /> Latest Smoking Gun report summary</h2>
          <p className="v3-h-sub">Diagnosis and mechanical action plan from your latest saved audit.</p>
        </div>
        <div className="v3-smoking-head-actions">
          <span className={'v3-evidence ' + (report.is_stale ? 'insufficient' : 'verified')}>
            {report.is_stale ? 'SOURCE CHANGED' : 'CURRENT'}
          </span>
          <button type="button" className="btn btn-primary btn-sm" onClick={onOpen}>View full report →</button>
        </div>
      </div>

      <div className="v3-smoking-clean">
        <article className="v3-smoking-diagnosis">
          <div className="v3-smoking-panel-title">
            <AlertTriangle size={17} />
            <div>
              <span className="v3-lab">Diagnosis</span>
              <strong>What is actually costing or protecting performance</strong>
            </div>
          </div>

          <div className="v3-diagnosis-rows">
            {diagnosisRows.map((row) => (
              <div className={'v3-diagnosis-row ' + row.tone} key={row.label}>
                <span>{row.label}</span>
                <p>{row.value}</p>
              </div>
            ))}
          </div>
        </article>

        <article className="v3-smoking-action-plan">
          <div className="v3-smoking-panel-title">
            <Target size={17} />
            <div>
              <span className="v3-lab">Mechanical action plan</span>
              <strong>Rules to execute on the next trading session</strong>
            </div>
          </div>

          {planRows.length ? (
            <ol className="v3-action-plan-list">
              {planRows.map((item, index) => (
                <li key={index}>
                  <b>{index + 1}</b>
                  <div>
                    <strong>{item.title}</strong>
                    {item.detail && <p>{item.detail}</p>}
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="v3-smoking-empty-copy">No mechanical action plan was saved with this report.</p>
          )}
        </article>
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
    accountId, onDayClick,
    goals,
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
      read: k.profit_factor == null ? 'Needs both wins and losses' : 'Above 1.00 means winning P&L outweighs losing P&L',
    },
    {
      label: 'Expectancy',
      value: money2(k.expectancy || 0),
      tone: Number(k.expectancy || 0) < 0 ? 'neg' : 'pos',
      read: 'Typical net result per completed trade',
    },
    {
      label: 'Avg R / trade',
      value: avgR == null ? 'N/A' : (avgR > 0 ? '+' : '') + avgR.toFixed(2) + 'R',
      tone: avgR != null && avgR < 0 ? 'neg' : undefined,
      read: avgR == null ? 'Record planned risk to unlock' : 'Risk-adjusted result from ' + (k.r_sample_count || 0) + ' trades with planned risk',
    },
    {
      label: 'Max drawdown',
      value: money2(k.max_drawdown || 0),
      tone: 'neg',
      read: 'Worst realized decline from a prior equity peak',
    },
  ];

  const readout = days.length ? days[days.length - 1] : null;

  return (
    <div className="v3-dashboard">
      <div className="v3-hero v3-hero-compact">
        <div className="v3-eyeline">
          <div>
            <p className="v3-acct">{accountLabel}{span ? ' · ' + span : ''}</p>
            <div className="v3-hero-label">Total net P&amp;L</div>
            <h1 className={'v3-money ' + tone(net)}>{money2(net)}</h1>
            <p className="v3-money-sub">
              {(k.total_trades || 0).toLocaleString()} completed trades
              {' '}· {(k.trading_days || 0).toLocaleString()} sessions
              {' '}· {k.trading_days ? money2(net / k.trading_days) + ' avg/day' : 'avg/day unavailable'}
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
                <p className="v3-h-sub">See whether performance is trending up and which sessions are driving the result.</p>
              </div>
              <span className="v3-evidence verified">VERIFIED</span>
            </div>
            <div className="v3-chart-grid v3-chart-grid-dashboard">
              <div className="v3-chart-panel">
                <div className="v3-chart-title">
                  <div>
                    <div className="v3-lab">Cumulative net P&amp;L</div>
                    <strong>Account growth</strong>
                  </div>
                  <span>after commissions</span>
                </div>
                <EquityCurve days={days} height={150} onPick={onDayClick} />
              </div>
              <div className="v3-chart-panel">
                <div className="v3-chart-title">
                  <div>
                    <div className="v3-lab">Daily net P&amp;L</div>
                    <strong>{k.trading_days || 0} sessions</strong>
                  </div>
                  <span>click a bar to review the day</span>
                </div>
                <DailyPnlBars days={days} height={150} onPick={onDayClick} />
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
        </aside>
      </div>

      <section className="v3-band v3-smoking-band">
        <LatestSmokingGunSummary report={latestSmokingGun} onOpen={onViewSmokingGun} />
      </section>
    </div>
  );
}
