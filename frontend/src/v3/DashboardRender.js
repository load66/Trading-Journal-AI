/* The V3 Today page. Presentation only: every value, handler and piece of
   state is passed in from Dashboard.js, so no behaviour lives here. */
import { useState, useEffect } from 'react';
import { Trophy, Clock3, Target, ShieldAlert, Lightbulb, FileText, AlertTriangle, BarChart3 } from 'lucide-react';
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
  const captureGoal = Number(goals?.exit_efficiency ?? 60);
  const totalTrades = Number(data.total_trades || 0);
  const totalWinners = Number(data.capture_winner_total ?? data.winning_trades ?? 0);
  const captureN = Number(data.capture_n || 0);
  const captureCoverage = Number(data.capture_coverage_pct || 0);
  const captureDays = Number(data.capture_days || 0);
  const captureConfidence = String(data.capture_confidence || 'LOW').toUpperCase();
  const excursionN = Number(data.excursion_n || 0);
  const excursionTotalN = Number(data.excursion_total_trades ?? totalTrades);
  const excursionCoverage = Number(data.management_coverage_pct || 0);
  const excursionDays = Number(data.excursion_days || 0);
  const excursionConfidence = String(data.excursion_confidence || 'LOW').toUpperCase();

  const captureUsable = capture != null && captureN > 0;
  const captureActionable = captureUsable && captureConfidence === 'RELIABLE';
  const captureBar = captureUsable ? Math.max(0, Math.min(100, capture)) : 0;
  const leftOnTable = captureUsable ? Math.max(0, 100 - captureBar) : null;
  const leftBar = leftOnTable == null ? 0 : leftOnTable;

  const winnerHold = hold.winners_avg_min == null ? null : Number(hold.winners_avg_min);
  const loserHold = hold.losers_avg_min == null ? null : Number(hold.losers_avg_min);
  const winnerMedian = hold.winners_median_min == null ? null : Number(hold.winners_median_min);
  const loserMedian = hold.losers_median_min == null ? null : Number(hold.losers_median_min);
  const winnerHoldN = Number(hold.winner_count || 0);
  const loserHoldN = Number(hold.loser_count || 0);
  const holdSampleN = Number(hold.sample_count || (winnerHoldN + loserHoldN));
  const holdEnough = winnerHold != null && loserHold != null && winnerHoldN >= 5 && loserHoldN >= 5;
  const avgHoldLeak = holdEnough && loserHold > winnerHold * 1.10;
  const medianHoldLeak = holdEnough && winnerMedian != null && loserMedian != null
    ? loserMedian > winnerMedian * 1.10
    : avgHoldLeak;
  const holdLeak = holdEnough && avgHoldLeak && medianHoldLeak;
  const holdMixed = holdEnough && avgHoldLeak !== medianHoldLeak;

  const mfe = data.avg_mfe == null ? null : Number(data.avg_mfe);
  const mae = data.avg_mae == null ? null : Number(data.avg_mae);
  const favorableMove = mfe == null ? null : Math.abs(mfe);
  const adverseMove = mae == null ? null : Math.abs(mae);
  const riskUsable = favorableMove != null && adverseMove != null && excursionN > 0;
  const riskActionable = riskUsable && excursionConfidence === 'RELIABLE';
  const riskLeak = riskActionable && adverseMove > favorableMove;
  const holdMax = Math.max(winnerHold || 0, loserHold || 0, 1);
  const winnerHoldPct = winnerHold == null ? 0 : Math.max(8, Math.min(100, (winnerHold / holdMax) * 100));
  const loserHoldPct = loserHold == null ? 0 : Math.max(8, Math.min(100, (loserHold / holdMax) * 100));
  const moveMax = Math.max(favorableMove || 0, adverseMove || 0, 1);
  const favorablePct = favorableMove == null ? 0 : Math.max(8, Math.min(100, (favorableMove / moveMax) * 100));
  const adversePct = adverseMove == null ? 0 : Math.max(8, Math.min(100, (adverseMove / moveMax) * 100));

  const captureState = !captureUsable
    ? { tone: 'neutral', label: 'NEED DATA' }
    : captureConfidence === 'LOW'
      ? { tone: 'neutral', label: 'LOW COVERAGE' }
      : captureConfidence === 'DEVELOPING'
        ? { tone: 'caution', label: 'DEVELOPING' }
        : capture >= captureGoal
          ? { tone: 'good', label: 'ABOVE GOAL' }
          : { tone: 'caution', label: 'BELOW GOAL' };

  const rangeCopy = range === '7D'
    ? 'last 7 days'
    : range === '90D'
      ? 'last 90 days'
      : range === 'ALL'
        ? 'all available trades'
        : 'last 30 days';
  const HelpDot = ({ label }) => (
    <span className="v3-ref-help" title={label} aria-label={label}>?</span>
  );

  const captureSummary = !captureUsable
    ? 'No contract-level market path is available yet for this window.'
    : captureConfidence === 'LOW'
      ? `Low coverage: ${captureN} of ${totalWinners} winning trades across ${captureDays} day${captureDays === 1 ? '' : 's'}. No capture diagnosis yet.`
      : captureConfidence === 'DEVELOPING'
        ? `Developing sample: ${captureN} of ${totalWinners} winning trades. Treat this as directional, not proven.`
        : capture >= captureGoal
          ? 'You are retaining a solid share of the favorable move on covered winners.'
          : 'Covered winners are giving back more of the favorable move than your goal allows.';

  const holdSummary = !holdEnough
    ? `Broker CSV has only ${winnerHoldN} winners and ${loserHoldN} losers with usable timestamps; more trades are needed.`
    : holdMixed
      ? 'Average and median hold times disagree, so there is no strong holding-time diagnosis yet.'
      : holdLeak
        ? 'Both average and median hold times show losers being held longer than winners.'
        : 'The broker CSV does not show a consistent loser-holding leak in this window.';

  const captureSplitSummary = !captureUsable
    ? 'Left-on-table analysis needs actual instrument price-path coverage.'
    : captureConfidence === 'LOW'
      ? 'This split is shown for context only because market-path coverage is too thin.'
      : captureConfidence === 'DEVELOPING'
        ? 'This split is developing and should not drive a rule change yet.'
        : leftOnTable <= 40
          ? 'Covered winners are retaining most of the available favorable move.'
          : 'Covered winners are leaving a large share of the favorable move on the table.';

  const bottomCopy = (() => {
    const brokerBase = `Broker CSV: ${totalTrades} closed trades in the ${rangeCopy}.`;

    if (holdLeak) {
      return `${brokerBase} The strongest verified management issue is hold time: losers average ${loserHold.toFixed(1)} minutes and winners ${winnerHold.toFixed(1)} minutes, with the medians pointing the same way. Cut invalidated losers faster.`;
    }

    if (captureActionable && capture < captureGoal) {
      return `${brokerBase} Hold-time behavior does not show a consistent loser-holding leak. Supplemental contract-level market data is reliable enough to flag profit capture at ${capture.toFixed(0)}%, below your ${captureGoal.toFixed(0)}% goal.`;
    }

    if (riskLeak) {
      return `${brokerBase} Hold-time behavior does not show a consistent loser-holding leak. Reliable market-path coverage shows adverse movement exceeding favorable movement on average; tighten entry quality and invalidation discipline.`;
    }

    if (holdMixed) {
      return `${brokerBase} Hold-time evidence is mixed: average and median behavior do not agree, so no strong hold-time leak is verified. Market-path coverage is ${excursionN}/${excursionTotalN} trades (${excursionCoverage.toFixed(0)}%) across ${excursionDays} day${excursionDays === 1 ? '' : 's'}.`;
    }

    if (holdEnough) {
      return `${brokerBase} The broker CSV does not show a consistent loser-holding leak. ${captureActionable || riskActionable ? 'Supplemental market-path coverage is reliable and does not identify a stronger management leak.' : `Supplemental market-path coverage is ${excursionN}/${excursionTotalN} trades (${excursionCoverage.toFixed(0)}%) across ${excursionDays} day${excursionDays === 1 ? '' : 's'}, so no additional diagnosis is promoted yet.`}`;
    }

    return `${brokerBase} There is not enough timestamped winner/loser history to make a strong management diagnosis. Supplemental market-path metrics remain secondary until coverage improves.`;
  })();

  return (
    <div className="v3-ref-management">
      <div className="v3-ref-management-head">
        <div className="v3-ref-management-title">
          <span className="v3-ref-title-icon"><BarChart3 size={26} /></span>
          <div>
            <h2>Trade management <HelpDot label="How well you manage trades after entry" /></h2>
            <p>How well do you manage trades after you enter?</p>
          </div>
        </div>
        <div className="v3-management-range v3-ref-range" role="group" aria-label="Trade management range">
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

      <div className="v3-ref-card-grid">
        <article className="v3-ref-card v3-ref-card-capture">
          <div className="v3-ref-card-heading">
            <span className="v3-ref-card-icon green"><Trophy size={20} /></span>
            <div>
              <h3>Profit capture <HelpDot label="Exit efficiency: how much favorable movement you retain" /></h3>
              <p>How much of the available move you actually keep on winning trades.</p>
            </div>
          </div>

          <div className="v3-ref-capture-line">
            <strong className={capture != null && capture >= 0 ? 'v3-pos' : 'v3-neg'}>
              {capture == null ? 'N/A' : capture.toFixed(0) + '%'}
            </strong>
            <span className={'v3-ref-goal-badge ' + captureState.tone}>{captureState.label}</span>
          </div>

          <div
            className={`v3-ref-meter${capture == null ? ' is-empty' : ''}`}
            role={capture == null ? 'status' : 'meter'}
            aria-label={capture == null ? 'Profit capture unavailable' : 'Profit capture'}
            aria-valuemin={capture == null ? undefined : 0}
            aria-valuemax={capture == null ? undefined : 100}
            aria-valuenow={capture == null ? undefined : captureBar}
            aria-valuetext={capture == null ? undefined : `${capture.toFixed(0)}% exit efficiency; goal ${captureGoal.toFixed(0)}%`}
          >
            <i className="green" style={{ '--w': captureBar + '%' }} aria-hidden="true" />
          </div>
          <div className="v3-ref-goal">Goal ≥ {captureGoal.toFixed(0)}%</div>

          <div className={'v3-ref-message ' + (capture != null && capture >= captureGoal ? 'good' : capture != null && capture < 0 ? 'bad' : 'caution')}>
            <span className="v3-ref-message-icon">{capture != null && capture >= captureGoal ? '✓' : '!'}</span>
            <p>{captureSummary}</p>
          </div>
        </article>

        <article className="v3-ref-card v3-ref-card-hold">
          <div className="v3-ref-card-heading">
            <span className="v3-ref-card-icon amber"><Clock3 size={20} /></span>
            <div>
              <h3>Holding behavior <HelpDot label="Average hold time for winning trades versus losing trades" /></h3>
              <p>How long you hold winners vs. losers.</p>
            </div>
          </div>

          <div className="v3-ref-dual">
            <div>
              <span>Winners</span>
              <strong className="v3-pos">{winnerHold == null ? 'N/A' : winnerHold.toFixed(1) + ' min'}</strong>
              <div className="v3-ref-mini-track"><i className="green" style={{ '--w': winnerHoldPct + '%' }} /></div>
            </div>
            <div>
              <span>Losers</span>
              <strong className="v3-neg">{loserHold == null ? 'N/A' : loserHold.toFixed(1) + ' min'}</strong>
              <div className="v3-ref-mini-track"><i className="red" style={{ '--w': loserHoldPct + '%' }} /></div>
            </div>
          </div>

          <div className={'v3-ref-message ' + (holdLeak ? 'bad' : 'good')}>
            <span className="v3-ref-message-icon">{holdLeak ? '!' : '✓'}</span>
            <p><b>{holdSummary}</b>{holdLeak ? ' Try to cut losing trades faster.' : ''}</p>
          </div>
        </article>

        <article className="v3-ref-card v3-ref-card-split">
          <div className="v3-ref-card-heading">
            <span className="v3-ref-card-icon purple"><Target size={20} /></span>
            <div>
              <h3>Profit vs. left on table <HelpDot label="Captured favorable movement compared with movement surrendered before exit" /></h3>
              <p>On winning trades, how much you captured vs. how much was left.</p>
            </div>
          </div>

          <div className="v3-ref-split-bar" aria-label="Captured movement versus left on table">
            <span className="captured" style={{ '--w': captureBar + '%' }}>
              {capture == null ? 'No data' : capture.toFixed(0) + '% Captured'}
            </span>
            <span className="left" style={{ '--w': leftBar + '%' }}>
              {leftOnTable == null ? '' : leftOnTable.toFixed(0) + '% Left'}
            </span>
          </div>

          <div className="v3-ref-split-values">
            <div><span className="v3-pos">Captured</span><strong className="v3-pos">{capture == null ? 'N/A' : capture.toFixed(0) + '%'}</strong></div>
            <div><span>Left on Table</span><strong>{leftOnTable == null ? 'N/A' : leftOnTable.toFixed(0) + '%'}</strong></div>
          </div>

          <div className={'v3-ref-message ' + (leftOnTable != null && leftOnTable <= 40 ? 'good' : 'caution')}>
            <span className="v3-ref-message-icon">{leftOnTable != null && leftOnTable <= 40 ? '✓' : '!'}</span>
            <p>{captureSplitSummary}</p>
          </div>
        </article>

        <article className="v3-ref-card v3-ref-card-risk">
          <div className="v3-ref-card-heading">
            <span className="v3-ref-card-icon red"><ShieldAlert size={20} /></span>
            <div>
              <h3>Risk during trade <HelpDot label="Average favorable and adverse excursion before exit" /></h3>
              <p>How much trades move for and against you before you exit (average).</p>
            </div>
          </div>

          <div className="v3-ref-dual v3-ref-risk-dual">
            <div>
              <strong className="v3-pos">{favorableMove == null ? 'N/A' : '+' + favorableMove.toFixed(2) + '%'}</strong>
              <div className="v3-ref-mini-track"><i className="green" style={{ '--w': favorablePct + '%' }} /></div>
              <span>Avg Favorable Move<br /><small>(MFE)</small></span>
            </div>
            <div>
              <strong className="v3-neg">{adverseMove == null ? 'N/A' : '-' + adverseMove.toFixed(2) + '%'}</strong>
              <div className="v3-ref-mini-track"><i className="red" style={{ '--w': adversePct + '%' }} /></div>
              <span>Avg Adverse Move<br /><small>(MAE)</small></span>
            </div>
          </div>

          <div className="v3-ref-risk-notes">
            <p className="good"><span>✓</span> Winners have good upside potential.</p>
            <p className={riskLeak ? 'bad' : 'good'}><span>{riskLeak ? '!' : '✓'}</span> {riskLeak ? 'Manage drawdown to still meaningful levels.' : 'Adverse movement is staying contained.'}</p>
          </div>
        </article>
      </div>

      <div className="v3-ref-bottom-line">
        <div className="v3-ref-bottom-label">
          <span className="v3-ref-bulb"><Lightbulb size={24} /></span>
          <div>
            <h3>Bottom line</h3>
            <p>Analysis based on your {rangeCopy}</p>
            <span>({excursionN || 0} qualifying trades)</span>
          </div>
        </div>
        <div className="v3-ref-bottom-copy">
          <span className="v3-ref-bottom-check">✓</span>
          <p>{bottomCopy}</p>
        </div>
      </div>
    </div>
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
