import './SmokingGunTerminal.css';

const money = (value) => {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  return `${n > 0 ? '+' : n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

const percent = (value) => (
  value == null || Number.isNaN(Number(value)) ? '—' : `${Number(value).toFixed(1)}%`
);

const plain = (value, suffix = '') => (
  value == null || value === '' ? '—' : `${value}${suffix}`
);

const tone = (value) => Number(value) > 0 ? 'sg-positive' : Number(value) < 0 ? 'sg-negative' : '';

const evidenceLabel = (count) => {
  const n = Number(count || 0);
  if (n < 10) return 'Thin sample';
  if (n < 30) return 'Developing sample';
  return 'Established sample';
};

function Missing({ children = '—' }) {
  return <span className="sg-missing" title="Not available in this saved deterministic payload.">{children}</span>;
}

function Section({ title, subtitle, children, className = '' }) {
  return (
    <section className={`sg-section ${className}`.trim()}>
      <div className="sg-section-head">
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

function Stat({ label, value, className = '', detail }) {
  return (
    <div className="sg-stat">
      <span className="sg-stat-label">{label}</span>
      <strong className={className}>{value == null ? <Missing /> : value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function StatsTable({ rows, label = 'Bucket' }) {
  if (!rows?.length) return <div className="sg-empty">No deterministic rows recorded for this section.</div>;
  return (
    <div className="sg-table-wrap">
      <table className="sg-table">
        <thead>
          <tr><th>{label}</th><th>Trades</th><th>P&amp;L</th><th>Win rate</th><th>$/trade</th><th>Evidence</th></tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.bucket || row.depth || row.day || row.name || index}>
              <td>{row.bucket || row.depth || row.day || row.name || '—'}</td>
              <td>{plain(row.trade_count ?? row.days)}</td>
              <td className={tone(row.total_pnl)}>{money(row.total_pnl)}</td>
              <td>{percent(row.win_rate ?? row.green_day_rate)}</td>
              <td className={tone(row.avg_pnl)}>{money(row.avg_pnl)}</td>
              <td><span className={Number(row.trade_count ?? row.days) < 10 ? 'sg-chip sg-chip-warning' : 'sg-chip sg-chip-muted'}>{evidenceLabel(row.trade_count ?? row.days)}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Population({ filters }) {
  const parts = [];
  if (filters?.tickers?.length) parts.push(filters.tickers.join(', '));
  if (filters?.instrument_types?.length) parts.push(filters.instrument_types.join(', '));
  return parts.length ? parts.join(' · ') : 'All stored trades in range';
}

function BestWorst({ item }) {
  if (!item) return <Missing />;
  return <>{money(item.pnl)} <small>{item.date}</small></>;
}

function BehaviorEvidence({ behavior }) {
  const evidence = behavior?.evidence || [];
  const flaws = behavior?.ranked_flaws || [];
  const avg = behavior?.averaging_down || {};
  return (
    <div className="sg-stack">
      {!!evidence.length && (
        <div className="sg-evidence-grid">
          {evidence.map((item, index) => (
            <article className="sg-evidence-card" key={item.name || index}>
              <div className="sg-evidence-top">
                <strong>{item.name || 'Behavior cohort'}</strong>
                <span className={
                  item.evidence_status === 'Confirmed leak' ? 'sg-chip sg-chip-negative'
                  : item.evidence_status === 'Possible leak' ? 'sg-chip sg-chip-warning'
                  : item.evidence_status === 'Insufficient evidence' ? 'sg-chip sg-chip-muted'
                  : 'sg-chip sg-chip-positive'
                }>
                  {item.evidence_status || 'Observed'}
                </span>
              </div>
              <div className="sg-mini-metrics">
                <span>{plain(item.trade_count)} trades</span>
                <span className={tone(item.pnl)}>{money(item.pnl)}</span>
              </div>
            </article>
          ))}
        </div>
      )}

      {!!flaws.length && (
        <div className="sg-table-wrap">
          <table className="sg-table">
            <thead>
              <tr><th>Ranked behavior</th><th>Trades</th><th>Observed P&amp;L</th><th>Dollar impact</th><th>P&amp;L if removed</th></tr>
            </thead>
            <tbody>
              {flaws.map((row, index) => (
                <tr key={row.name || index}>
                  <td><b>#{index + 1}</b> {row.name}</td>
                  <td>{plain(row.trade_count)}</td>
                  <td className={tone(row.pnl)}>{money(row.pnl)}</td>
                  <td className="sg-negative">{money(-Math.abs(Number(row.dollar_impact || 0)))}</td>
                  <td className={tone(row.pnl_if_eliminated)}>{money(row.pnl_if_eliminated)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="sg-compare-grid">
        <Stat label="Averaged-down cohort" value={money(avg.averaged_down?.total_pnl)} className={tone(avg.averaged_down?.total_pnl)} detail={avg.averaged_down ? `${avg.averaged_down.trade_count} trades · ${percent(avg.averaged_down.win_rate)} win` : undefined} />
        <Stat label="Clean-entry cohort" value={money(avg.clean_entries?.total_pnl)} className={tone(avg.clean_entries?.total_pnl)} detail={avg.clean_entries ? `${avg.clean_entries.trade_count} trades · ${percent(avg.clean_entries.win_rate)} win` : undefined} />
      </div>

      <div className="sg-callout sg-callout-neutral">
        Behavioral counterfactuals are independent cohorts and may overlap. Their dollar impacts must not be added together.
      </div>
    </div>
  );
}

export default function SmokingGunSavedReport({
  report,
  onBack,
  onDownloadHtml,
  onDownloadLedger,
}) {
  const metrics = report?.source_metrics || {};
  const board = metrics.scoreboard || {};
  const two = metrics.two_traders || {};
  const diagnosis = report?.diagnosis || {};
  const actionPlan = report?.action_plan || diagnosis.action_plan || [];
  const tickerRows = metrics.ticker_ranking || [];
  const stopRows = metrics.daily_stop_model?.levels || [];
  const dailyRows = metrics.daily_pnl || [];
  const time = metrics.time_analysis || {};
  const projections = metrics.projections?.rates || [];

  return (
    <div className="sg-terminal" role="region" aria-label="Saved Smoking Gun report">
      <div className="sg-terminal-titlebar">
        <div>
          <span className="sg-kicker">SMOKING GUN · SAVED AUDIT</span>
          <h1>{report?.title || 'Smoking Gun Report'}</h1>
          <p>{report?.date_from} → {report?.date_to} · Population: {Population({ filters: report?.filters })}</p>
        </div>
        <div className="sg-terminal-actions">
          <button type="button" className="btn" onClick={onBack}>Back to Report Library</button>
          <button type="button" className="btn btn-primary" onClick={onDownloadHtml}>Download HTML</button>
          <button type="button" className="btn" onClick={onDownloadLedger}>Download Trade Ledger</button>
        </div>
      </div>

      <Section title="Command Header">
        <div className="sg-command-grid">
          <div>
            <span className={`sg-chip ${report?.is_stale ? 'sg-chip-warning' : 'sg-chip-positive'}`}>
              {report?.is_stale ? 'SOURCE CHANGED' : 'CURRENT'}
            </span>
          </div>
          <div><span>Generated</span><b>{plain(report?.generated_at)}</b></div>
          <div><span>Trades</span><b>{plain(report?.trade_count)}</b></div>
          <div><span>Report</span><b>v{plain(report?.report_version)}</b></div>
          <div><span>Analytics</span><b>{plain(report?.analytics_engine_version)}</b></div>
          <div><span>Behavior</span><b>{plain(report?.behavior_version)}</b></div>
          <div><span>Timestamp coverage</span><b>{percent(metrics.meta?.timestamp_coverage)}</b></div>
          <div className="sg-fingerprint"><span>Fingerprint</span><b title={report?.data_fingerprint}>{report?.data_fingerprint ? report.data_fingerprint.slice(0, 12) + '…' : '—'}</b></div>
        </div>
        {report?.is_stale && (
          <div className="sg-callout sg-callout-warning">
            The underlying journal trades changed after this snapshot was generated. The saved audit remains immutable; regenerate it before treating it as current.
          </div>
        )}
      </Section>

      <Section title="Executive Scoreboard" subtitle="Source-of-truth performance metrics saved with this audit.">
        <div className="sg-scoreboard">
          <Stat label="Net P&L" value={money(board.net_pnl)} className={tone(board.net_pnl)} />
          <Stat label="Gross P&L" value={money(board.gross_pnl)} className={tone(board.gross_pnl)} />
          <Stat label="Fees" value={money(board.fees)} />
          <Stat label="Win rate" value={percent(board.win_rate)} />
          <Stat label="Profit factor" value={plain(board.profit_factor)} />
          <Stat label="Avg winner" value={money(board.avg_winner)} className="sg-positive" />
          <Stat label="Avg loser" value={board.avg_loser == null ? null : money(-Math.abs(board.avg_loser))} className="sg-negative" />
          <Stat label="Reward / risk" value={board.reward_risk == null ? null : `${board.reward_risk}:1`} />
          <Stat label="Max drawdown" value={money(board.max_drawdown)} className="sg-negative" />
          <Stat label="Active days" value={plain(board.active_days)} />
          <Stat label="Best day" value={<BestWorst item={board.best_day} />} className="sg-positive" />
          <Stat label="Worst day" value={<BestWorst item={board.worst_day} />} className="sg-negative" />
        </div>
      </Section>

      <Section title="Behavioral Cohort Split" subtitle="Mechanical cohorts based on observed trade characteristics; these are not personality labels.">
        <div className="sg-trader-grid">
          {[
            ['Rule-aligned cohort', two.disciplined, 'sg-trader-positive'],
            ['Comparison cohort', two.destructive, 'sg-trader-negative'],
          ].map(([label, row, cls]) => (
            <article className={`sg-trader-card ${cls}`} key={label}>
              <span>{label}</span>
              <strong className={tone(row?.total_pnl)}>{money(row?.total_pnl)}</strong>
              <div><b>{plain(row?.trade_count)}</b> trades</div>
              <div><b>{percent(row?.win_rate)}</b> win rate</div>
              <div><b>{money(row?.avg_pnl)}</b> / trade</div>
              <div><span className="sg-chip sg-chip-muted">{evidenceLabel(row?.trade_count)}</span></div>
            </article>
          ))}
        </div>
      </Section>

      <Section title="Edge Map / Hold Time" subtitle="Exact entry-to-final-exit buckets. Longer or shorter is descriptive evidence, not a command to hold invalid trades.">
        <div className="sg-callout sg-callout-neutral" style={{ marginBottom: 12 }}>
          <b>Association, not causation.</b> These are outcomes observed in each hold-time cohort. A longer hold did not necessarily cause the better result, and a broken setup should still be exited.
        </div>
        <StatsTable rows={metrics.hold_time} label="Hold time" />
      </Section>

      <Section title="Position Size" subtitle="Exact option-contract buckets plus normalized size/hold cross-reference.">
        <div className="sg-two-column">
          <div><h3>Options contracts</h3><StatsTable rows={metrics.position_size?.options} label="Contracts" /></div>
          <div><h3>Shares / notional</h3><StatsTable rows={metrics.position_size?.shares_by_notional} label="Notional" /></div>
        </div>
        <div className="sg-compare-grid">
          <Stat label="Small size + long hold" value={money(metrics.position_size?.cross_reference?.small_size_long_hold?.total_pnl)} className={tone(metrics.position_size?.cross_reference?.small_size_long_hold?.total_pnl)} />
          <Stat label="Big size + short hold" value={money(metrics.position_size?.cross_reference?.big_size_short_hold?.total_pnl)} className={tone(metrics.position_size?.cross_reference?.big_size_short_hold?.total_pnl)} />
        </div>
      </Section>

      <Section title="Daily P&L" subtitle="Realized daily ledger and running equity from the saved snapshot.">
        <div className="sg-table-wrap">
          <table className="sg-table">
            <thead><tr><th>Date</th><th>Total</th><th>Running</th><th>Trades</th><th>Risk flag</th></tr></thead>
            <tbody>{dailyRows.map((row) => (
              <tr key={row.date}>
                <td>{row.date}</td>
                <td className={tone(row.total_pnl)}>{money(row.total_pnl)}</td>
                <td className={tone(row.running_total)}>{money(row.running_total)}</td>
                <td>{plain(row.trade_count)}</td>
                <td>{row.blow_up ? <span className="sg-chip sg-chip-negative">BLOW-UP</span> : '—'}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <Section title="Daily Stop Lab" subtitle="Historical what-if cutoffs. Positive savings and recovery days harmed by the cutoff remain visible.">
        <div className="sg-table-wrap">
          <table className="sg-table">
            <thead><tr><th>Stop</th><th>Adjusted P&amp;L</th><th>Observed difference</th><th>Breaches</th></tr></thead>
            <tbody>{stopRows.map((row) => (
              <tr key={row.stop}>
                <td>{money(-Math.abs(row.stop))}</td>
                <td className={tone(row.adjusted_pnl)}>{money(row.adjusted_pnl)}</td>
                <td className={tone(row.saved)}>{money(row.saved)}</td>
                <td>{plain(row.breach_count)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <Section title="Ticker Ranking" subtitle="Status and sample quality are saved deterministic classifications.">
        <div className="sg-table-wrap">
          <table className="sg-table">
            <thead><tr><th>Ticker</th><th>Status</th><th>Sample</th><th>Trades</th><th>P&amp;L</th><th>Win rate</th><th>$/trade</th></tr></thead>
            <tbody>{tickerRows.map((row) => (
              <tr key={row.ticker}>
                <td><b>{row.ticker}</b></td>
                <td><span className={`sg-chip ${row.total_pnl >= 0 ? 'sg-chip-positive' : 'sg-chip-negative'}`}>{row.label}</span></td>
                <td>{row.sample_quality === 'thin' ? <span className="sg-chip sg-chip-warning">thin</span> : 'established'}</td>
                <td>{plain(row.trade_count)}</td>
                <td className={tone(row.total_pnl)}>{money(row.total_pnl)}</td>
                <td>{percent(row.win_rate)}</td>
                <td className={tone(row.dollars_per_trade)}>{money(row.dollars_per_trade)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <Section title="Behavioral Forensics" subtitle="Only measured cohorts belong here; positive or unsupported behavior is not mislabeled as a leak.">
        <BehaviorEvidence behavior={metrics.behavior} />
      </Section>

      <Section title="Time Analysis" subtitle="Entry-time cohorts from the saved deterministic payload.">
        <div className="sg-compare-grid">
          <Stat label="First 10 minutes" value={money(time.first_10_minutes?.total_pnl)} className={tone(time.first_10_minutes?.total_pnl)} detail={time.first_10_minutes ? `${time.first_10_minutes.trade_count} trades · ${percent(time.first_10_minutes.win_rate)} win` : undefined} />
          <Stat label="Rest of day" value={money(time.rest_of_day?.total_pnl)} className={tone(time.rest_of_day?.total_pnl)} detail={time.rest_of_day ? `${time.rest_of_day.trade_count} trades · ${percent(time.rest_of_day.win_rate)} win` : undefined} />
        </div>
        <div className="sg-two-column">
          <div><h3>30-minute blocks</h3><StatsTable rows={time.half_hour_blocks} label="Entry block" /></div>
          <div><h3>Day of week</h3><StatsTable rows={time.day_of_week} label="Day" /></div>
        </div>
      </Section>

      <Section title="Scenario Model" subtitle="Descriptive scenarios based on the saved disciplined-cohort edge; not forecasts or guarantees.">
        <div className="sg-table-wrap">
          <table className="sg-table">
            <thead><tr><th>Edge retained</th><th>Daily edge</th><th>Sessions</th><th>Gross scenario</th><th>After drawdown</th><th>Recovery months</th></tr></thead>
            <tbody>{projections.map((row) => (
              <tr key={row.rate}>
                <td>{percent(Number(row.rate) * 100)}</td>
                <td className={tone(row.daily_edge)}>{money(row.daily_edge)}</td>
                <td>{plain(row.remaining_weekdays)}</td>
                <td className={tone(row.gross_earnings)}>{money(row.gross_earnings)}</td>
                <td className={tone(row.net_after_current_drawdown)}>{money(row.net_after_current_drawdown)}</td>
                <td>{plain(row.months_to_recover)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </Section>

      <Section title="Diagnosis" subtitle="AI interpretation of locked source metrics; causal claims require evidence beyond cohort P&L.">
        <div className="sg-diagnosis">
          <div className="sg-diagnosis-headline">{diagnosis.headline || <Missing>Diagnosis not saved.</Missing>}</div>
          <div className="sg-two-column">
            <div><h3>Positive observed cohorts</h3><ul>{(diagnosis.edge?.where_it_lives || []).map((item, i) => <li key={i}>{item}</li>)}</ul></div>
            <div><h3>Negative observed cohorts / risks</h3><ul>{(diagnosis.edge?.where_it_dies || []).map((item, i) => <li key={i}>{item}</li>)}</ul></div>
          </div>
          {!!diagnosis.limitations?.length && <div><h3>Limitations</h3><ul>{diagnosis.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></div>}
        </div>
      </Section>

      <Section title="Mechanical Action Plan" subtitle="Ranked prospective controls. Source metrics remain authoritative; negative cohort P&L does not prove causation.">
        {Array.isArray(actionPlan) && actionPlan.length ? (
          <div className="sg-action-list">
            {actionPlan.map((item, index) => (
              <article key={item.rule || index}>
                <span>#{item.priority || item.rank || index + 1}</span>
                <div><strong>{item.rule || item.mechanical_rule}</strong><p>{item.why || item.evidence}</p></div>
              </article>
            ))}
          </div>
        ) : <div className="sg-empty">No mechanical action plan was saved with this report.</div>}
      </Section>
    </div>
  );
}
