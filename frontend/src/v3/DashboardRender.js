/* The V3 Today page. Presentation only: every value, handler and piece of
   state is passed in from Dashboard.js, so no behaviour lives here. */
import { useState, useEffect } from 'react';
import { Trophy, Clock3, Target, ShieldAlert, Lightbulb, FileText, AlertTriangle, BarChart3 } from 'lucide-react';
import { calendarApi } from '../api';
import LERiskPlanner from './LERiskPlanner';
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
          <div className="v3-cal-share-foot">Net P&amp;L · recorded sessions</div>
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
  const totalTrades = Number(data.total_trades || edge?.total_trades || 0);
  const captureGoal = Number(goals?.exit_efficiency ?? 60);

  const rawCapture = data.exit_efficiency == null ? null : Number(data.exit_efficiency);
  const captureN = Number(data.capture_n || 0);
  const captureWinnerTotal = Number(data.capture_winner_total || 0);
  const captureCoverage = Number(data.capture_coverage_pct || 0);
  const captureConfidence = String(data.capture_confidence || 'LOW').toUpperCase();
  const captureUsable = rawCapture != null && captureConfidence !== 'LOW';
  const capture = captureUsable ? Math.max(0, Math.min(100, rawCapture)) : null;
  const captureBar = capture == null ? 0 : capture;
  const leftOnTable = capture == null ? null : Math.max(0, 100 - capture);
  const leftBar = leftOnTable == null ? 0 : leftOnTable;

  const winnerHold = hold.winners_avg_min == null ? null : Number(hold.winners_avg_min);
  const loserHold = hold.losers_avg_min == null ? null : Number(hold.losers_avg_min);
  const winnerMedian = hold.winners_median_min == null ? null : Number(hold.winners_median_min);
  const loserMedian = hold.losers_median_min == null ? null : Number(hold.losers_median_min);
  const winnerHoldN = Number(hold.winner_count || 0);
  const loserHoldN = Number(hold.loser_count || 0);
  const holdN = Number(hold.sample_count || (winnerHoldN + loserHoldN));
  const holdCoverage = Number(hold.coverage_pct ?? (totalTrades ? holdN / totalTrades * 100 : 0));
  const avgHoldLeak = winnerHold != null && loserHold != null && loserHold > winnerHold * 1.10;
  const medianHoldLeak = winnerMedian != null && loserMedian != null && loserMedian > winnerMedian * 1.10;
  const holdReliable = winnerHoldN >= 5 && loserHoldN >= 5 && winnerHold != null && loserHold != null;
  const holdMixed = holdReliable && winnerMedian != null && loserMedian != null && avgHoldLeak !== medianHoldLeak;

  const rawMfe = data.avg_mfe == null ? null : Number(data.avg_mfe);
  const rawMae = data.avg_mae == null ? null : Number(data.avg_mae);
  const medianMfe = data.median_mfe == null ? null : Number(data.median_mfe);
  const medianMae = data.median_mae == null ? null : Number(data.median_mae);
  const winnerMedianMfe = data.winner_median_mfe == null ? null : Number(data.winner_median_mfe);
  const loserMedianMfe = data.loser_median_mfe == null ? null : Number(data.loser_median_mfe);
  const winnerMedianMae = data.winner_median_mae == null ? null : Number(data.winner_median_mae);
  const loserMedianMae = data.loser_median_mae == null ? null : Number(data.loser_median_mae);
  const winnerMaeLe20Pct = data.winner_mae_le_20_pct == null ? null : Number(data.winner_mae_le_20_pct);
  const loserMfeLe5Pct = data.loser_mfe_le_5_pct == null ? null : Number(data.loser_mfe_le_5_pct);
  const loserMfeLe10Pct = data.loser_mfe_le_10_pct == null ? null : Number(data.loser_mfe_le_10_pct);
  const loserMaeGe25Pct = data.loser_mae_ge_25_pct == null ? null : Number(data.loser_mae_ge_25_pct);
  const excursionN = Number(data.excursion_n || 0);
  const managementCoverage = Number(data.management_coverage_pct || 0);
  const excursionConfidence = String(data.excursion_confidence || 'LOW').toUpperCase();
  const riskUsable = rawMfe != null && rawMae != null && excursionConfidence !== 'LOW';
  const favorableMove = riskUsable ? Math.abs(rawMfe) : null;
  const adverseMove = riskUsable ? Math.abs(rawMae) : null;

  const holdMax = Math.max(winnerHold || 0, loserHold || 0, 1);
  const winnerHoldPct = winnerHold == null ? 0 : Math.max(8, Math.min(100, (winnerHold / holdMax) * 100));
  const loserHoldPct = loserHold == null ? 0 : Math.max(8, Math.min(100, (loserHold / holdMax) * 100));
  const moveMax = Math.max(favorableMove || 0, adverseMove || 0, 1);
  const favorablePct = favorableMove == null ? 0 : Math.max(8, Math.min(100, (favorableMove / moveMax) * 100));
  const adversePct = adverseMove == null ? 0 : Math.max(8, Math.min(100, (adverseMove / moveMax) * 100));

  const holdLeak = holdReliable && avgHoldLeak && (winnerMedian == null || loserMedian == null || medianHoldLeak);
  const avgRiskPositive = riskUsable && favorableMove > adverseMove * 1.10;
  const avgRiskLeak = riskUsable && adverseMove > favorableMove * 1.10;
  const medianRiskPositive = riskUsable && medianMfe != null && medianMae != null && medianMfe > medianMae * 1.10;
  const medianRiskLeak = riskUsable && medianMfe != null && medianMae != null && medianMae > medianMfe * 1.10;
  const riskMixed = riskUsable && medianMfe != null && medianMae != null
    && ((avgRiskPositive && medianRiskLeak) || (avgRiskLeak && medianRiskPositive)
      || (!avgRiskPositive && !avgRiskLeak) !== (!medianRiskPositive && !medianRiskLeak));
  const riskLeak = riskUsable && avgRiskLeak && (medianMfe == null || medianMae == null || medianRiskLeak);
  const riskPositive = riskUsable && avgRiskPositive && (medianMfe == null || medianMae == null || medianRiskPositive);
  const earlyFailurePattern = riskUsable
    && loserMedianMae != null
    && winnerMedianMae != null
    && loserMfeLe5Pct != null
    && loserMedianMae >= winnerMedianMae * 1.5
    && loserMfeLe5Pct >= 50;
  const winnersBeyond20Pct = winnerMaeLe20Pct == null ? null : Math.max(0, 100 - winnerMaeLe20Pct);
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

  const oneDecimal = (value) => (
    Math.round((Number(value) + Number.EPSILON) * 10) / 10
  ).toFixed(1);

  const captureState = !captureUsable
    ? { tone: 'neutral', label: captureN ? 'LOW COVERAGE' : 'NEED DATA' }
    : captureConfidence === 'DEVELOPING'
      ? { tone: 'caution', label: 'DEVELOPING' }
      : capture >= captureGoal
        ? { tone: 'good', label: 'ABOVE GOAL' }
        : { tone: 'caution', label: 'BELOW GOAL' };

  const captureSummary = !captureUsable
    ? 'Not enough actual stock/option-premium path coverage to diagnose profit capture yet.'
    : captureConfidence === 'DEVELOPING'
      ? 'Early capture signal only. Treat this as developing evidence until more trades are covered.'
      : capture >= captureGoal
        ? 'You retain a solid portion of the favorable move on covered winning trades.'
        : 'Covered winning trades are giving back too much of the favorable move before exit.';

  const holdSummary = !holdReliable
    ? 'More timestamped winners and losers are needed before comparing holding behavior.'
    : holdMixed
      ? 'Average and median hold times point in different directions, so no strong holding-time leak is diagnosed.'
      : holdLeak
        ? 'Losing trades are held longer than winning trades in both average and median behavior.'
        : 'No consistent loser-holding leak is present in this window.';

  const captureSplitSummary = !captureUsable
    ? 'Capture vs. giveback is withheld until actual-instrument path coverage is sufficient.'
    : captureConfidence === 'DEVELOPING'
      ? 'This capture/giveback split is a developing signal, not a firm diagnosis.'
      : leftOnTable <= 40
        ? 'Covered winners retain most of the available favorable move.'
        : 'Covered winners leave a large share of the favorable move on the table.';

  const riskSummary = !riskUsable
    ? 'MFE/MAE diagnosis is withheld until actual-instrument path coverage is sufficient.'
    : excursionConfidence === 'DEVELOPING'
      ? 'MFE/MAE is a developing signal based only on currently covered trades.'
      : earlyFailurePattern
        ? 'Main improvement: tighten entry quality and invalidate failed trades sooner.'
        : riskMixed
          ? 'Mean and median excursion disagree, so no firm risk-direction diagnosis is issued.'
          : riskLeak
            ? 'Both average and median adverse excursion exceed favorable excursion on covered trades.'
            : riskPositive
              ? 'Both average and median favorable excursion exceed adverse excursion on covered trades.'
              : 'Favorable and adverse excursion are too close for a firm directional diagnosis.';

  const bottomCopy = (() => {
    const holdSentence = holdReliable
      ? 'Winners average ' + winnerHold.toFixed(1) + ' min and losers average ' + loserHold.toFixed(1) + ' min.'
      : 'There is not yet enough hold-time evidence for a firm comparison.';

    if (holdLeak) {
      return holdSentence + ' The clearest management issue is holding losing trades longer, and the median confirms the same pattern. Excursion-based capture/risk stays secondary.';
    }

    if (holdMixed) {
      return holdSentence + ' Average and median hold times disagree, so the data does not support a firm hold-time diagnosis. Supplemental market-path coverage is ' + managementCoverage.toFixed(0) + '% (' + excursionN + '/' + totalTrades + ' trades).';
    }

    if (!captureUsable && !riskUsable) {
      return holdSentence + ' No excursion-based diagnosis is issued because actual stock/option-premium path coverage is only ' + managementCoverage.toFixed(0) + '% (' + excursionN + '/' + totalTrades + ' trades).';
    }

    if (captureUsable && captureConfidence === 'RELIABLE' && capture < captureGoal) {
      return holdSentence + ' Profit capture is ' + capture.toFixed(0) + '% on ' + captureN + ' covered winners, below your ' + captureGoal.toFixed(0) + '% goal. That is the strongest excursion-based management leak in this window.';
    }

    if (riskUsable && excursionConfidence === 'RELIABLE' && earlyFailurePattern) {
      const loserHeat = oneDecimal(loserMedianMae);
      const winnerHeat = oneDecimal(winnerMedianMae);
      const weakLosers = loserMfeLe5Pct.toFixed(0);
      const winnerRoom = winnersBeyond20Pct == null ? null : winnersBeyond20Pct.toFixed(0);
      return holdSentence + ' The clearest improvement candidate is entry quality / early invalidation: losers reach a median -' + loserHeat + '% MAE versus -' + winnerHeat + '% for winners, and ' + weakLosers + '% of losers never achieve +5% MFE.'
        + (winnerRoom == null ? '' : ' Do not use a blanket -20% stop: ' + winnerRoom + '% of winners also exceeded -20% MAE, so the rule should be setup-specific.');
    }

    if (riskUsable && excursionConfidence === 'RELIABLE' && riskMixed) {
      return holdSentence + ' Excursion averages and medians disagree, so outliers are affecting the risk picture and no firm MFE/MAE diagnosis is promoted.';
    }

    if (riskUsable && excursionConfidence === 'RELIABLE' && riskLeak) {
      return holdSentence + ' Covered trades show more adverse than favorable excursion in both average and median behavior, so risk containment is the strongest excursion-based concern.';
    }

    if ((captureUsable && captureConfidence === 'DEVELOPING') || (riskUsable && excursionConfidence === 'DEVELOPING')) {
      return holdSentence + ' Excursion evidence is still developing, so the dashboard will not promote it to a firm diagnosis yet.';
    }

    return holdSentence + ' The currently covered excursion data does not identify a dominant management leak.';
  })();

  const captureEvidence = captureN + '/' + captureWinnerTotal + ' winning trades · ' + captureCoverage.toFixed(0) + '% coverage · ' + captureConfidence;
  const riskEvidence = excursionN + '/' + totalTrades + ' trades · ' + managementCoverage.toFixed(0) + '% coverage · ' + excursionConfidence
    + (winnerMedianMae != null && loserMedianMae != null
      ? ' · winner/loser median MAE -' + winnerMedianMae.toFixed(1) + '% / -' + loserMedianMae.toFixed(1) + '%'
      : medianMfe != null && medianMae != null
        ? ' · medians +' + medianMfe.toFixed(2) + '% / -' + medianMae.toFixed(2) + '%'
        : '');
  const holdEvidence = holdN + '/' + totalTrades + ' trades · ' + holdCoverage.toFixed(0) + '% timestamp coverage';
  const bottomTone = holdLeak
    ? 'bad'
    : earlyFailurePattern || riskMixed || (!captureUsable && !riskUsable)
        || captureConfidence === 'DEVELOPING' || excursionConfidence === 'DEVELOPING'
      ? 'caution'
      : 'good';

  return (
    <div className="v3-ref-management">
      <div className="v3-ref-management-head">
        <div className="v3-ref-management-title">
          <span className="v3-ref-title-icon"><BarChart3 size={26} /></span>
          <div>
            <h2>Trade management <HelpDot label="How well you manage trades after entry" /></h2>
            <p>Evaluate how efficiently you manage entries, risk, and exits.</p>
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
              <h3>Profit capture <HelpDot label="How much favorable premium/price movement was retained on covered winners" /></h3>
              <p>Measures how much of the available favorable move you retain.</p>
            </div>
          </div>

          <div className="v3-ref-capture-line">
            <strong className={capture != null ? 'v3-pos' : ''}>
              {capture == null ? 'N/A' : capture.toFixed(0) + '%'}
            </strong>
            <span className={'v3-ref-goal-badge ' + captureState.tone}>{captureState.label}</span>
          </div>

          <div
            className={'v3-ref-meter' + (capture == null ? ' is-empty' : '')}
            role={capture == null ? 'status' : 'meter'}
            aria-label={capture == null ? 'Profit capture unavailable' : 'Profit capture'}
            aria-valuemin={capture == null ? undefined : 0}
            aria-valuemax={capture == null ? undefined : 100}
            aria-valuenow={capture == null ? undefined : captureBar}
            aria-valuetext={capture == null ? undefined : capture.toFixed(0) + '% exit efficiency; goal ' + captureGoal.toFixed(0) + '%'}
          >
            <i className="green" style={{ '--w': captureBar + '%' }} aria-hidden="true" />
          </div>
          <div className="v3-ref-goal">Goal ≥ {captureGoal.toFixed(0)}%</div>
          <div className="v3-ref-evidence-line">{captureEvidence}</div>

          <div className={'v3-ref-message ' + (!captureUsable ? 'caution' : capture >= captureGoal ? 'good' : 'caution')}>
            <span className="v3-ref-message-icon">{captureUsable && capture >= captureGoal ? '✓' : '!'}</span>
            <p>{captureSummary}</p>
          </div>
        </article>

        <article className="v3-ref-card v3-ref-card-hold">
          <div className="v3-ref-card-heading">
            <span className="v3-ref-card-icon amber"><Clock3 size={20} /></span>
            <div>
              <h3>Holding behavior <HelpDot label="Average hold time for winning trades versus losing trades" /></h3>
              <p>Compares how long winning and losing trades are held.</p>
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
          <div className="v3-ref-evidence-line">
            {holdEvidence}
            {winnerMedian != null && loserMedian != null ? ' · medians ' + winnerMedian.toFixed(1) + ' / ' + loserMedian.toFixed(1) + ' min' : ''}
          </div>

          <div className={'v3-ref-message ' + (!holdReliable || holdMixed ? 'caution' : holdLeak ? 'bad' : 'good')}>
            <span className="v3-ref-message-icon">{holdReliable && !holdLeak && !holdMixed ? '✓' : '!'}</span>
            <p><b>{holdSummary}</b>{holdLeak ? ' Consider a faster invalidation rule for losing trades.' : ''}</p>
          </div>
        </article>

        <article className="v3-ref-card v3-ref-card-split">
          <div className="v3-ref-card-heading">
            <span className="v3-ref-card-icon purple"><Target size={20} /></span>
            <div>
              <h3>Profit vs. left on table <HelpDot label="Captured favorable movement compared with movement surrendered before exit" /></h3>
              <p>Shown only when actual-instrument path coverage is sufficient.</p>
            </div>
          </div>

          <div className="v3-ref-split-bar" aria-label="Captured movement versus left on table">
            <span className="captured" style={{ '--w': captureBar + '%' }}>
              {capture == null ? 'Awaiting coverage' : capture.toFixed(0) + '% Captured'}
            </span>
            <span className="left" style={{ '--w': leftBar + '%' }}>
              {leftOnTable == null ? '' : leftOnTable.toFixed(0) + '% Left'}
            </span>
          </div>

          <div className="v3-ref-split-values">
            <div><span className={capture == null ? '' : 'v3-pos'}>Captured</span><strong className={capture == null ? '' : 'v3-pos'}>{capture == null ? 'N/A' : capture.toFixed(0) + '%'}</strong></div>
            <div><span>Left on Table</span><strong>{leftOnTable == null ? 'N/A' : leftOnTable.toFixed(0) + '%'}</strong></div>
          </div>
          <div className="v3-ref-evidence-line">{captureEvidence}</div>

          <div className={'v3-ref-message ' + (!captureUsable ? 'caution' : leftOnTable <= 40 ? 'good' : 'caution')}>
            <span className="v3-ref-message-icon">{captureUsable && leftOnTable <= 40 ? '✓' : '!'}</span>
            <p>{captureSplitSummary}</p>
          </div>
        </article>

        <article className="v3-ref-card v3-ref-card-risk">
          <div className="v3-ref-card-heading">
            <span className="v3-ref-card-icon red"><ShieldAlert size={20} /></span>
            <div>
              <h3>Risk during trade <HelpDot label="Average favorable and adverse excursion using actual stock/option-premium paths" /></h3>
              <p>Shows favorable versus adverse movement while trades are open.</p>
            </div>
          </div>

          <div className="v3-ref-dual v3-ref-risk-dual">
            <div>
              <strong className={favorableMove == null ? '' : 'v3-pos'}>{favorableMove == null ? 'N/A' : '+' + favorableMove.toFixed(2) + '%'}</strong>
              <div className="v3-ref-mini-track"><i className="green" style={{ '--w': favorablePct + '%' }} /></div>
              <span>Avg Favorable Move<br /><small>(MFE)</small></span>
            </div>
            <div>
              <strong className={adverseMove == null ? '' : 'v3-neg'}>{adverseMove == null ? 'N/A' : '-' + adverseMove.toFixed(2) + '%'}</strong>
              <div className="v3-ref-mini-track"><i className="red" style={{ '--w': adversePct + '%' }} /></div>
              <span>Avg Adverse Move<br /><small>(MAE)</small></span>
            </div>
          </div>
          <div className="v3-ref-evidence-line">{riskEvidence}</div>

          <div className="v3-ref-risk-notes">
            <p className={!riskUsable ? 'neutral' : earlyFailurePattern ? 'bad' : riskMixed || (!riskLeak && !riskPositive) ? 'neutral' : riskLeak ? 'bad' : 'good'}>
              <span>{riskUsable && riskPositive && !riskMixed && !earlyFailurePattern ? '✓' : '!'}</span> {riskSummary}
            </p>
            {earlyFailurePattern ? (
              <>
                <p className="neutral"><span>→</span> Typical winner: +{oneDecimal(winnerMedianMfe)}% MFE / -{oneDecimal(winnerMedianMae)}% MAE. Typical loser: +{oneDecimal(loserMedianMfe)}% MFE / -{oneDecimal(loserMedianMae)}% MAE.</p>
                <p className="neutral"><span>→</span> {loserMfeLe5Pct.toFixed(0)}% of losers never reach +5% MFE, {loserMfeLe10Pct.toFixed(0)}% never reach +10%, and {loserMaeGe25Pct.toFixed(0)}% reach at least -25% MAE.</p>
                <p className="caution"><span>→</span> Test a setup-specific early-failure rule when a trade cannot make +5% favorable progress and adverse excursion starts expanding. {winnersBeyond20Pct != null ? winnersBeyond20Pct.toFixed(0) + '% of winners exceeded -20% MAE, so avoid a blanket -20% stop.' : 'Avoid using one universal stop across every setup.'}</p>
              </>
            ) : (
              <p className="neutral"><span>→</span> {riskMixed ? 'Use winner-vs-loser excursion separation to find the next actionable entry or stop improvement.' : 'Keep monitoring winner-vs-loser excursion separation before changing stop rules.'}</p>
            )}
          </div>
        </article>
      </div>

      <div className="v3-ref-bottom-line">
        <div className="v3-ref-bottom-label">
          <span className="v3-ref-bulb"><Lightbulb size={24} /></span>
          <div>
            <h3>Bottom line</h3>
            <p>Analysis window · {rangeCopy}</p>
            <span>{totalTrades} closed trades · market-path coverage {excursionN}/{totalTrades}</span>
          </div>
        </div>
        <div className="v3-ref-bottom-copy">
          <span className={'v3-ref-bottom-check ' + bottomTone}>{bottomTone === 'good' ? '✓' : '!'}</span>
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

  const goalValue = (key, fallback) => Number(goals?.[key] ?? fallback);

  const pctGoalItem = (label, value, goal, read, amberBelow = false) => {
    const numeric = Number(value || 0);
    const target = Number(goal || 0);
    return {
      label,
      value: numeric.toFixed(1) + '%',
      tone: numeric >= target ? 'pos' : undefined,
      amber: amberBelow && numeric < target,
      read,
      goal: target.toFixed(0) + '%',
      goalPct: Math.max(5, Math.min(95, target)),
      fill: Math.max(0, Math.min(1, numeric / 100)),
      met: numeric >= target,
    };
  };

  const scaledGoalItem = (label, value, goal, formatter, read) => {
    const numeric = Number(value || 0);
    const target = Number(goal || 0);
    const scaleMax = Math.max(target * 1.35, Math.abs(numeric) * 1.12, 1);
    return {
      label,
      value: formatter(numeric),
      tone: numeric >= target ? 'pos' : numeric < 0 ? 'neg' : undefined,
      read,
      goal: formatter(target),
      goalPct: Math.max(8, Math.min(92, target / scaleMax * 100)),
      fill: Math.max(0, Math.min(1, numeric / scaleMax)),
      met: numeric >= target,
    };
  };

  const tradeWinRate = Number(k.win_rate || 0);
  const dayWinRate = Number(k.day_win_rate || 0);
  const avgWin = Math.abs(Number(k.avg_win || 0));
  const avgLoss = Math.abs(Number(k.avg_loss || 0));
  const winLossRatio = avgLoss > 0 ? avgWin / avgLoss : null;
  const exitEfficiency = k.exit_efficiency == null ? null : Number(k.exit_efficiency);
  const exitReliable = exitEfficiency != null && String(k.capture_confidence || 'LOW').toUpperCase() !== 'LOW';

  const winLossDiagnosis = winLossRatio == null
    ? 'Needs both winning and losing trades before payoff size can be evaluated.'
    : tradeWinRate >= 50 && winLossRatio < 1
      ? <>Your average win is <b>{absMoney(avgWin)}</b>. Your average loss is <b>{absMoney(avgLoss)}</b>. You win more often, but losses are larger than wins.</>
      : tradeWinRate >= 50 && winLossRatio >= 1
        ? <>Your average win is <b>{absMoney(avgWin)}</b>. Your average loss is <b>{absMoney(avgLoss)}</b>. Wins are both more frequent and larger than losses.</>
        : tradeWinRate < 50 && winLossRatio >= 1
          ? <>Your average win is <b>{absMoney(avgWin)}</b>. Your average loss is <b>{absMoney(avgLoss)}</b>. You win less often, but winning trades are larger.</>
          : <>Your average win is <b>{absMoney(avgWin)}</b>. Your average loss is <b>{absMoney(avgLoss)}</b>. Both win frequency and payoff size need improvement.</>;

  const measures = [
    pctGoalItem(
      'Trade win rate',
      tradeWinRate,
      goalValue('win_rate', 65),
      (k.winning_trades || 0).toLocaleString() + ' won, ' + (k.losing_trades || 0).toLocaleString() + ' lost'
    ),
    pctGoalItem(
      'Day win rate',
      dayWinRate,
      goalValue('day_win_rate', 75),
      (k.positive_days || 0).toLocaleString() + ' green days, ' + (k.negative_days || 0).toLocaleString() + ' red'
    ),
    scaledGoalItem(
      'Profit factor',
      k.profit_factor == null ? 0 : Number(k.profit_factor),
      goalValue('profit_factor', 1.5),
      (value) => value.toFixed(2),
      k.profit_factor == null ? 'Needs both wins and losses' : '$' + Number(k.profit_factor).toFixed(2) + ' won for every $1.00 lost'
    ),
    scaledGoalItem(
      'Win / loss size',
      winLossRatio == null ? 0 : winLossRatio,
      goalValue('avg_win_loss_ratio', 1.5),
      (value) => value.toFixed(2),
      winLossDiagnosis
    ),
    exitReliable
      ? pctGoalItem(
          'Exit efficiency',
          exitEfficiency,
          goalValue('exit_efficiency', 60),
          'You capture ' + exitEfficiency.toFixed(0) + '% of the favorable move on covered winning trades.',
          true
        )
      : {
          label: 'Exit efficiency',
          value: 'N/A',
          read: 'More covered winning trades are needed before exit efficiency is reliable.',
        },
    scaledGoalItem(
      'Expectancy',
      Number(k.expectancy || 0),
      goalValue('expectancy', 50),
      (value) => money2(value),
      'What the next completed trade is worth, on average'
    ),
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
            <LERiskPlanner accountId={accountId} />
          </section>
        </aside>
      </div>

      <section className="v3-band v3-smoking-band">
        <LatestSmokingGunSummary report={latestSmokingGun} onOpen={onViewSmokingGun} />
      </section>
    </div>
  );
}
