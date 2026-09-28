import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  RefreshCw, ShieldCheck, TriangleAlert, CheckCircle2,
  Database, TrendingUp, Activity, Info,
} from 'lucide-react';
import { leApi } from '../api';
import { Measures } from '../v3/parts';
import { PanelHead } from './ui';

const fmtMoney = (value) => {
  const n = Number(value || 0);
  return `${n < 0 ? '-' : n > 0 ? '+' : ''}$${Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

const fmtPf = (value) => (value == null ? '—' : Number(value).toFixed(2));
const tone = (value) => (Number(value) > 0 ? 'pos' : Number(value) < 0 ? 'neg' : '');

function AuditStatus({ data }) {
  const complete = data?.missing_trades === 0 && data?.stale_snapshots === 0;
  return (
    <div className={`notice ${complete ? 'pos' : 'caution'}`} style={{ marginBottom: 16 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
        {complete ? <CheckCircle2 size={17} /> : <TriangleAlert size={17} />}
        <div>
          <strong>{complete ? 'LE audit is current' : 'LE audit needs refresh'}</strong>
          <div className="text-muted" style={{ marginTop: 3 }}>
            {data?.audited_trades || 0} of {data?.total_trades || 0} closed trades audited ·
            {' '}{data?.coverage_pct || 0}% coverage · ruleset {data?.compliance_version || '—'}.
            {data?.stale_snapshots > 0 ? ` ${data.stale_snapshots} snapshot(s) use an older ruleset.` : ''}
          </div>
        </div>
      </div>
    </div>
  );
}

function FindingCard({ item }) {
  const icon = item.kind === 'leak'
    ? <TriangleAlert size={17} />
    : item.kind === 'coverage'
      ? <Database size={17} />
      : item.kind === 'manual'
        ? <ShieldCheck size={17} />
        : item.kind === 'quality'
          ? <Activity size={17} />
          : <TrendingUp size={17} />;

  return (
    <div className="card" style={{ padding: 16, minHeight: 132 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        {icon}
        <strong>{item.title}</strong>
      </div>
      <div className="text-muted" style={{ lineHeight: 1.55 }}>{item.text}</div>
    </div>
  );
}

function CohortTable({ rows }) {
  if (!rows?.length) return <div className="empty">No deterministic LE cohorts are available yet.</div>;
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 820 }}>
        <thead>
          <tr>
            <th>LE cohort</th>
            <th className="num">Trades</th>
            <th className="num">Win %</th>
            <th className="num">Net P&amp;L</th>
            <th className="num">Avg</th>
            <th className="num">PF</th>
            <th>Sample</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id}>
              <td style={{ fontWeight: 650 }}>{row.label}</td>
              <td className="num">{row.trades}</td>
              <td className="num">{row.win_rate}%</td>
              <td className={`num ${tone(row.net_pnl)}`} style={{ fontWeight: 700 }}>
                {fmtMoney(row.net_pnl)}
              </td>
              <td className={`num ${tone(row.avg_pnl)}`}>{fmtMoney(row.avg_pnl)}</td>
              <td className="num">{fmtPf(row.profit_factor)}</td>
              <td>
                {row.stable_sample
                  ? <span className="status-pill pos">Established</span>
                  : <span className="v3-thin">thin</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RuleMatrix({ rows }) {
  if (!rows?.length) return <div className="empty">No rule data yet.</div>;
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 1040 }}>
        <thead>
          <tr>
            <th>LE rule</th>
            <th className="num">Coverage</th>
            <th className="num">Pass</th>
            <th className="num">Pass P&amp;L</th>
            <th className="num">Fail</th>
            <th className="num">Fail P&amp;L</th>
            <th className="num">Unknown</th>
            <th className="num">Fail PF</th>
            <th className="num">User-backed</th>
            <th className="num">Conflicts</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id}>
              <td style={{ fontWeight: 650 }}>{row.label}</td>
              <td className="num">{row.coverage_pct}%</td>
              <td className="num">{row.pass.trades}</td>
              <td className={`num ${tone(row.pass.net_pnl)}`}>{fmtMoney(row.pass.net_pnl)}</td>
              <td className="num">{row.fail.trades}</td>
              <td className={`num ${tone(row.fail.net_pnl)}`}>{fmtMoney(row.fail.net_pnl)}</td>
              <td className="num text-muted">{row.unknown.trades}</td>
              <td className="num">{fmtPf(row.fail.profit_factor)}</td>
              <td className="num">{row.user_backed_trades ?? 0}</td>
              <td className="num">{row.user_system_conflicts ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


function stageTone(stage) {
  if (stage === 'VALIDATED') return 'pos';
  if (stage === 'NOT_VALIDATED') return 'neg';
  if (stage === 'CANDIDATE') return 'caution';
  return '';
}

function LearningEdgesTable({ rows }) {
  if (!rows?.length) return (
    <div className="empty">
      No user-confirmed LE setup has enough labeled history to evaluate yet.
    </div>
  );

  return (
    <div className="scroll-x">
      <table style={{ minWidth: 1080 }}>
        <thead>
          <tr>
            <th>Setup</th>
            <th>Stage</th>
            <th className="num">Trades</th>
            <th className="num">Days</th>
            <th className="num">Net P&amp;L</th>
            <th className="num">Avg</th>
            <th className="num">PF</th>
            <th className="num">Early P&amp;L</th>
            <th className="num">Recent P&amp;L</th>
            <th>Validation</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id}>
              <td style={{ fontWeight: 700 }}>{row.label}</td>
              <td>
                <span className={`status-pill ${stageTone(row.stage)}`}>
                  {String(row.stage || '').replaceAll('_', ' ')}
                </span>
              </td>
              <td className="num">{row.overall?.trades || 0}</td>
              <td className="num">{row.overall?.trading_days || 0}</td>
              <td className={`num ${tone(row.overall?.net_pnl)}`} style={{ fontWeight: 700 }}>
                {fmtMoney(row.overall?.net_pnl)}
              </td>
              <td className={`num ${tone(row.overall?.avg_pnl)}`}>
                {fmtMoney(row.overall?.avg_pnl)}
              </td>
              <td className="num">{fmtPf(row.overall?.profit_factor)}</td>
              <td className={`num ${tone(row.early_sample?.net_pnl)}`}>
                {fmtMoney(row.early_sample?.net_pnl)}
              </td>
              <td className={`num ${tone(row.recent_sample?.net_pnl)}`}>
                {fmtMoney(row.recent_sample?.net_pnl)}
              </td>
              <td className="text-muted" style={{ minWidth: 270 }}>{row.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DetectorCalibrationTable({ rows }) {
  if (!rows?.length) return (
    <div className="empty">
      No manual rule labels are available yet for detector calibration.
    </div>
  );

  return (
    <div className="scroll-x">
      <table style={{ minWidth: 980 }}>
        <thead>
          <tr>
            <th>Detector</th>
            <th>Priority</th>
            <th className="num">Labels</th>
            <th className="num">Agreements</th>
            <th className="num">Conflicts</th>
            <th className="num">Unknown resolved</th>
            <th className="num">Agreement %</th>
            <th>Why it matters</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.id}>
              <td style={{ fontWeight: 700 }}>{row.label}</td>
              <td>
                <span className={`status-pill ${row.priority === 'HIGH' ? 'neg' : row.priority === 'LOW' ? 'pos' : 'caution'}`}>
                  {row.priority}
                </span>
              </td>
              <td className="num">{row.labeled}</td>
              <td className="num">{row.system_agreements}</td>
              <td className="num">{row.system_conflicts}</td>
              <td className="num">{row.system_unknown_resolved}</td>
              <td className="num">
                {row.agreement_rate_pct == null ? '—' : `${row.agreement_rate_pct}%`}
              </td>
              <td className="text-muted" style={{ minWidth: 280 }}>{row.priority_reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecentAudit({ rows }) {
  if (!rows?.length) return <div className="empty">No audited trades yet.</div>;
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 900 }}>
        <thead>
          <tr>
            <th>Date</th>
            <th>Symbol</th>
            <th>Side</th>
            <th>Classification</th>
            <th className="num">P&amp;L</th>
            <th className="num">Pass</th>
            <th className="num">Fail</th>
            <th className="num">Unknown</th>
            <th>Verified failures</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.trade_group}>
              <td>{row.date}</td>
              <td style={{ fontWeight: 700 }}>{row.ticker}</td>
              <td>{row.side}</td>
              <td>{row.classification_label || row.classification || '—'}</td>
              <td className={`num ${tone(row.net_pnl)}`} style={{ fontWeight: 700 }}>
                {fmtMoney(row.net_pnl)}
              </td>
              <td className="num">{row.score?.passed ?? 0}</td>
              <td className="num">{row.score?.failed ?? 0}</td>
              <td className="num">{row.score?.unknown ?? 0}</td>
              <td className="text-muted">
                {(row.failed_rule_ids || []).length
                  ? row.failed_rule_ids.map(x => x.replaceAll('_', ' ')).join(', ')
                  : 'None verified'}
                {(row.manual_le_evidence?.override_count || 0) > 0 && (
                  <div style={{ marginTop: 4 }}>
                    <span className="status-pill pos">
                      User-backed {row.manual_le_evidence.override_count}
                    </span>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function LEDiagnosisReport({ accountId, dateFrom, dateTo }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState('');
  const [lastRun, setLastRun] = useState(null);

  const params = useMemo(() => {
    const next = {};
    if (accountId != null) next.account_id = accountId;
    if (dateFrom) next.date_from = dateFrom;
    if (dateTo) next.date_to = dateTo;
    return next;
  }, [accountId, dateFrom, dateTo]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await leApi.getDiagnosis(params);
      setData(response.data);
    } catch (err) {
      setError(err?.response?.data?.detail || 'LE diagnosis could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [params]);

  useEffect(() => { load(); }, [load]);

  const generate = async () => {
    setGenerating(true);
    setError('');
    try {
      const response = await leApi.generateDiagnosis({
        account_id: accountId ?? null,
        date_from: dateFrom || null,
        date_to: dateTo || null,
        force: false,
      });
      setData(response.data.report);
      setLastRun({
        processed: response.data.processed || 0,
        attempted: response.data.attempted || 0,
        errors: response.data.errors || [],
        complete: Boolean(response.data.complete),
      });
    } catch (err) {
      setError(err?.response?.data?.detail || 'LE diagnosis generation failed.');
    } finally {
      setGenerating(false);
    }
  };

  if (loading) {
    return (
      <div style={{ display: 'grid', gap: 16 }}>
        {[1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 180 }} />)}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="card">
        <div className="empty">{error || 'No LE diagnosis data is available yet.'}</div>
      </div>
    );
  }

  const overall = data.overall || {};
  const edge = data.most_profitable_cohort;
  const leak = data.biggest_verified_leak;
  const manualEvidence = data.manual_evidence || {};
  const learning = data.learning_core || {};

  return (
    <div style={{ display: 'grid', gap: 18 }}>
      <section className="card" style={{ padding: 18 }}>
        <div style={{
          display: 'flex', justifyContent: 'space-between', gap: 16,
          alignItems: 'flex-start', flexWrap: 'wrap',
        }}>
          <div>
            <div className="text-muted" style={{
              display: 'flex', gap: 7, alignItems: 'center',
              textTransform: 'uppercase', letterSpacing: '.08em', fontSize: 11, fontWeight: 750,
            }}>
              <ShieldCheck size={15} /> LE compliance engine
            </div>
            <h2 style={{ margin: '5px 0 6px' }}>Journal-wide LE Smart Diagnosis</h2>
            <p className="text-muted" style={{ margin: 0, maxWidth: 760, lineHeight: 1.55 }}>
              Every closed trade is graded from deterministic LE evidence plus recognized manual LE tags.
              A user-confirmed LE tag is authoritative for the exact concept it asserts; the prior system result is preserved for audit.
              Missing evidence stays Unknown, and new trades are audited automatically.
            </p>
          </div>
          <button
            type="button"
            className="btn primary"
            onClick={generate}
            disabled={generating}
            style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}
          >
            <RefreshCw size={15} className={generating ? 'spin' : undefined} />
            {generating ? 'Generating…' : 'Generate / Refresh Diagnosis'}
          </button>
        </div>
      </section>

      {error && <div className="notice neg" role="alert">{error}</div>}
      {lastRun && (
        <div className={`notice ${lastRun.errors.length ? 'caution' : 'pos'}`} role="status">
          Diagnosis refresh processed {lastRun.processed} of {lastRun.attempted} stale/missing trade(s).
          {lastRun.errors.length ? ` ${lastRun.errors.length} trade(s) still need evidence refresh.` : ' Audit cache is current.'}
        </div>
      )}

      <AuditStatus data={data} />

      <Measures
        items={[
          {
            label: 'Audit coverage',
            value: `${data.audited_trades} / ${data.total_trades}`,
            met: data.coverage_pct === 100,
            read: `${data.coverage_pct}% on the current LE ruleset`,
          },
          {
            label: 'Journal P&L',
            value: fmtMoney(overall.net_pnl),
            met: Number(overall.net_pnl) >= 0,
            read: `${overall.trades || 0} closed trades · ${overall.win_rate || 0}% win rate`,
          },
          {
            label: 'User-backed LE evidence',
            value: `${manualEvidence.trades || 0} trades`,
            met: (manualEvidence.trades || 0) > 0,
            read: `${manualEvidence.override_count || 0} authoritative result(s) · ${manualEvidence.conflict_count || 0} conflict(s) preserved`,
          },
          {
            label: 'Validated LE edges',
            value: learning.validated_setup_count || 0,
            met: (learning.validated_setup_count || 0) > 0,
            read: `${learning.high_priority_detector_count || 0} high-priority detector gap(s)`,
          },
          {
            label: 'Most profitable cohort',
            value: edge ? fmtMoney(edge.net_pnl) : '—',
            met: Boolean(edge),
            read: edge ? `${edge.label} · ${edge.trades} trades` : 'No stable positive cohort yet',
          },
          {
            label: 'Biggest verified leak',
            value: leak ? fmtMoney(leak.net_pnl) : 'None',
            met: !leak,
            tone: leak ? 'neg' : undefined,
            read: leak ? `${leak.label} · ${leak.trades} failed trades` : 'No negative rule-failure cohort with 5+ trades',
          },
        ]}
      />

      <section className="card">
        <PanelHead
          title="Smart Diagnosis"
          sub="Deterministic findings from your current LE ruleset. These are evidence summaries, not AI guesses."
        />
        <div className="grid-2" style={{ padding: '0 18px 18px' }}>
          {(data.findings || []).map(item => <FindingCard key={item.kind + item.title} item={item} />)}
        </div>
      </section>

      <section className="card">
        <PanelHead
          title="LE Setup Cohorts"
          sub="Objective combinations reconstructed from broker executions and verified market/EMA evidence. Established = at least 8 trades."
        />
        <CohortTable rows={data.cohorts} />
      </section>


      <section className="card">
        <PanelHead
          title="User-Confirmed LE Setups"
          sub="Manual setup tags are treated as authoritative user evidence. Performance is grouped exactly by the setup label you applied."
        />
        <CohortTable rows={data.user_confirmed_setups} />
      </section>


      <section className="card">
        <PanelHead
          title="LE Learning Core"
          sub="Ground-truth manual tags are used to validate edge and calibrate detectors. No rule is auto-promoted from in-sample P&L alone."
        />
        <div style={{ padding: '0 18px 18px' }}>
          <div className="notice" style={{ marginBottom: 14 }}>
            <ShieldCheck size={16} />
            <span>{learning.headline || 'Learning core is collecting evidence.'}</span>
          </div>
          <LearningEdgesTable rows={learning.setup_edges} />
        </div>
      </section>

      <section className="card">
        <PanelHead
          title="LE Detector Calibration"
          sub="Where automated compliance disagrees with, or cannot resolve, your authoritative manual LE labels."
        />
        <DetectorCalibrationTable rows={learning.detector_calibration} />
      </section>

      <section className="card">
        <PanelHead
          title="Rule Performance Matrix"
          sub="Pass / Fail / Unknown for every LE rule. User-backed shows manual authoritative overrides; conflicts preserve where the automated system originally disagreed."
        />
        <RuleMatrix rows={data.rules} />
      </section>

      <section className="card">
        <PanelHead
          title="Evidence Gaps"
          sub="These are the fields to improve next as the LE engine gets smarter and your journaling becomes richer."
        />
        {(data.evidence_gaps || []).length ? (
          <div className="scroll-x">
            <table style={{ minWidth: 620 }}>
              <thead>
                <tr>
                  <th>Rule</th>
                  <th className="num">Unknown trades</th>
                  <th className="num">Unknown %</th>
                </tr>
              </thead>
              <tbody>
                {data.evidence_gaps.slice(0, 13).map(row => (
                  <tr key={row.id}>
                    <td>{row.label}</td>
                    <td className="num">{row.unknown_trades}</td>
                    <td className="num">{row.unknown_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="notice pos" style={{ margin: 18 }}>
            <CheckCircle2 size={16} /> No current rule has Unknown evidence.
          </div>
        )}
      </section>

      <section className="card">
        <PanelHead
          title="Recent Trade Audit"
          sub="Latest 50 audited trades with the exact compliance result that feeds Brain and this report."
        />
        <RecentAudit rows={data.recent_trades} />
      </section>

      <div className="notice" style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <Info size={16} />
        <span>{data.note} {learning.note || ''}</span>
      </div>
    </div>
  );
}
