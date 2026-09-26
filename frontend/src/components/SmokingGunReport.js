import { useEffect, useMemo, useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts';
import { smokingGunApi } from '../api';
import { PanelHead } from './ui';

const fmt$ = (v) => {
  const n = Number(v || 0);
  return `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
};
const signed$ = (v) => `${Number(v) > 0 ? '+' : ''}${fmt$(v)}`;
const tone = (v) => Number(v) > 0 ? 'pos' : Number(v) < 0 ? 'neg' : '';
const tick = { fontSize: 10.5, fill: 'var(--text-secondary)' };

function Card({ title, sub, children }) {
  return (
    <section className="card">
      <PanelHead title={title} sub={sub} />
      {children}
    </section>
  );
}

function StatsRow({ data }) {
  const total = data?.daily_pnl?.length
    ? data.daily_pnl[data.daily_pnl.length - 1].running_total
    : 0;
  const d = data?.two_traders?.disciplined;
  const x = data?.two_traders?.destructive;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10 }}>
      {[
        ['Completed trades', data?.meta?.trade_count ?? 0, ''],
        ['Timestamp coverage', `${data?.meta?.timestamp_coverage ?? 0}%`, ''],
        ['Net P&L', signed$(total), tone(total)],
        ['Open positions', data?.meta?.open_position_count ?? 0, ''],
        ['Disciplined cohort', signed$(d?.total_pnl), tone(d?.total_pnl)],
        ['Destructive cohort', signed$(x?.total_pnl), tone(x?.total_pnl)],
        ['Disciplined trades', d?.trade_count ?? 0, ''],
        ['Destructive trades', x?.trade_count ?? 0, ''],
      ].map(([label, value, cls]) => (
        <div key={label} className="card" style={{ padding: 14, minHeight: 86 }}>
          <div className="text-muted" style={{ fontSize: 11.5 }}>{label}</div>
          <div className={`num ${cls}`} style={{ fontSize: 22, fontWeight: 650, marginTop: 6 }}>{value}</div>
        </div>
      ))}
    </div>
  );
}

function PnlBarChart({ rows, labelKey = 'bucket', valueKey = 'total_pnl', height = 260 }) {
  if (!rows?.length) return <div className="empty">Not enough data.</div>;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows} margin={{ top: 10, right: 8, left: 8, bottom: 30 }}>
        <CartesianGrid stroke="var(--divider-soft)" vertical={false} />
        <XAxis dataKey={labelKey} tick={tick} axisLine={false} tickLine={false} angle={-25} textAnchor="end" interval={0} />
        <YAxis tick={tick} axisLine={false} tickLine={false} tickFormatter={fmt$} width={62} />
        <ReferenceLine y={0} stroke="var(--divider)" />
        <Tooltip
          cursor={{ fill: 'var(--accent-soft)' }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const row = payload[0].payload;
            return (
              <div className="card" style={{ padding: '8px 11px', fontSize: 12.5 }}>
                <b>{row[labelKey]}</b>
                <div className={`num ${tone(row[valueKey])}`}>{signed$(row[valueKey])}</div>
                {row.trade_count != null && <div className="text-muted">{row.trade_count} trades · {row.win_rate}% win</div>}
              </div>
            );
          }}
        />
        <Bar dataKey={valueKey} radius={[3, 3, 0, 0]}>
          {rows.map((r, i) => <Cell key={i} fill={Number(r[valueKey]) >= 0 ? 'var(--result-pos)' : 'var(--result-neg)'} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

function HoldTable({ rows }) {
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 620 }}>
        <thead><tr><th>Hold time</th><th className="num">Trades</th><th className="num">P&L</th><th className="num">Win %</th><th className="num">$/trade</th></tr></thead>
        <tbody>
          {(rows || []).map(r => (
            <tr key={r.bucket}>
              <td style={{ fontWeight: 600 }}>{r.bucket}</td>
              <td className="num">{r.trade_count}</td>
              <td className={`num ${tone(r.total_pnl)}`}>{signed$(r.total_pnl)}</td>
              <td className="num">{r.win_rate}%</td>
              <td className={`num ${tone(r.avg_pnl)}`}>{signed$(r.avg_pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StopTable({ model }) {
  const rows = model?.levels || [];
  if (!rows.length) return <div className="empty">No losing trades to model daily stops.</div>;
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 700 }}>
        <thead><tr><th>Daily stop</th><th className="num">Adjusted P&L</th><th className="num">Saved</th><th className="num">Breaches</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.stop}>
              <td className="num" style={{ fontWeight: 650 }}>{fmt$(r.stop)}</td>
              <td className={`num ${tone(r.adjusted_pnl)}`}>{signed$(r.adjusted_pnl)}</td>
              <td className={`num ${tone(r.saved)}`}>{signed$(r.saved)}</td>
              <td className="num">{r.breach_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TickerTable({ rows }) {
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 760 }}>
        <thead><tr><th>Ticker</th><th>Status</th><th>Sample</th><th className="num">Trades</th><th className="num">P&L</th><th className="num">Win %</th><th className="num">$/trade</th></tr></thead>
        <tbody>
          {(rows || []).map(r => (
            <tr key={r.ticker}>
              <td style={{ fontWeight: 700 }}>{r.ticker}</td>
              <td><span className={`chip ${r.total_pnl >= 0 ? 'pos' : 'neg'}`}>{r.label}</span></td>
              <td><span className="v3-thin">{r.sample_quality === 'thin' ? 'thin' : 'established'}</span></td>
              <td className="num">{r.trade_count}</td>
              <td className={`num ${tone(r.total_pnl)}`}>{signed$(r.total_pnl)}</td>
              <td className="num">{r.win_rate}%</td>
              <td className={`num ${tone(r.dollars_per_trade)}`}>{signed$(r.dollars_per_trade)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FlawTable({ flaws }) {
  if (!flaws?.length) return <div className="empty">No negative-dollar behavior cohort was proven in this range.</div>;
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 700 }}>
        <thead><tr><th>Priority</th><th>Behavior</th><th className="num">Trades</th><th className="num">Observed P&L</th><th className="num">Dollar impact</th><th className="num">P&L if removed</th></tr></thead>
        <tbody>
          {flaws.map((r, i) => (
            <tr key={r.name}>
              <td className="num">#{i + 1}</td>
              <td style={{ fontWeight: 600 }}>{r.name}</td>
              <td className="num">{r.trade_count}</td>
              <td className="num neg">{fmt$(r.pnl)}</td>
              <td className="num neg" style={{ fontWeight: 700 }}>{fmt$(r.dollar_impact)}</td>
              <td className={`num ${tone(r.pnl_if_eliminated)}`} style={{ fontWeight: 700 }}>{signed$(r.pnl_if_eliminated)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DailyTable({ rows }) {
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 820 }}>
        <thead><tr><th>Date</th><th className="num">Options</th><th className="num">Shares</th><th className="num">Futures</th><th className="num">Total</th><th className="num">Running</th><th className="num">Trades</th><th>Flag</th></tr></thead>
        <tbody>{(rows || []).map(r => (
          <tr key={r.date}>
            <td>{r.date}</td>
            <td className={`num ${tone(r.options_pnl)}`}>{signed$(r.options_pnl)}</td>
            <td className={`num ${tone(r.shares_pnl)}`}>{signed$(r.shares_pnl)}</td>
            <td className={`num ${tone(r.futures_pnl)}`}>{signed$(r.futures_pnl)}</td>
            <td className={`num ${tone(r.total_pnl)}`} style={{ fontWeight: 650 }}>{signed$(r.total_pnl)}</td>
            <td className={`num ${tone(r.running_total)}`}>{signed$(r.running_total)}</td>
            <td className="num">{r.trade_count}</td>
            <td>{r.blow_up ? <span className="chip neg">BLOW-UP</span> : ''}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function GenericStatsTable({ rows, first = 'Bucket' }) {
  if (!rows?.length) return <div className="empty">Not enough data.</div>;
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 560 }}>
        <thead><tr><th>{first}</th><th className="num">Trades</th><th className="num">P&L</th><th className="num">Win %</th><th className="num">$/trade</th></tr></thead>
        <tbody>{rows.map(r => (
          <tr key={r.bucket || r.depth || r.day}>
            <td style={{ fontWeight: 600 }}>{r.bucket || r.depth || r.day}</td>
            <td className="num">{r.trade_count ?? 0}</td>
            <td className={`num ${tone(r.total_pnl)}`}>{signed$(r.total_pnl)}</td>
            <td className="num">{r.win_rate ?? 0}%</td>
            <td className={`num ${tone(r.avg_pnl)}`}>{signed$(r.avg_pnl)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function StopBreaches({ model }) {
  const best = [...(model?.levels || [])].sort((a, b) => b.saved - a.saved)[0];
  if (!best?.breaches?.length) return <div className="empty">No modeled stop breach days.</div>;
  return (
    <div>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>Highest modeled savings scenario: {fmt$(best.stop)}</div>
      <div className="scroll-x">
        <table style={{ minWidth: 620 }}>
          <thead><tr><th>Date</th><th className="num">Actual</th><th className="num">Modeled</th><th className="num">Excess loss avoided</th></tr></thead>
          <tbody>{best.breaches.map(r => (
            <tr key={r.date}><td>{r.date}</td><td className="num neg">{fmt$(r.actual_pnl)}</td><td className={`num ${tone(r.modeled_pnl)}`}>{signed$(r.modeled_pnl)}</td><td className="num pos">{signed$(r.saved)}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

function BehaviorEvidence({ behavior, time }) {
  const tilt = behavior?.tilt_escalation || {};
  const chase = behavior?.chasing_fomo || {};
  const asym = behavior?.winner_loser_asymmetry || {};
  const avgd = behavior?.averaging_down || {};
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      <div className="grid-2">
        <div><b>Re-entry depth after a same-ticker loss</b><GenericStatsTable rows={behavior?.revenge_trading} first="Depth" /></div>
        <div><b>Overtrading by daily trade count</b>
          <div className="scroll-x"><table style={{ minWidth: 480 }}><thead><tr><th>Trades/day</th><th className="num">Days</th><th className="num">P&L</th><th className="num">Green-day %</th></tr></thead>
          <tbody>{(behavior?.overtrading || []).map(r => <tr key={r.bucket}><td>{r.bucket}</td><td className="num">{r.days}</td><td className={`num ${tone(r.total_pnl)}`}>{signed$(r.total_pnl)}</td><td className="num">{r.green_day_rate}%</td></tr>)}</tbody></table></div>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: 10 }}>
        {[
          ['Rapid re-entry ≤30 sec', `${chase.trade_count || 0} trades · ${signed$(chase.total_pnl)}`],
          ['First-3 avg size', tilt.first3_avg_size == null ? 'Insufficient evidence' : `${tilt.first3_avg_size}× typical`],
          ['Post-loss avg size', tilt.post_threshold_avg_size == null ? 'Insufficient evidence' : `${tilt.post_threshold_avg_size}× typical`],
          ['Post-loss-threshold P&L', signed$(tilt.post_threshold_pnl)],
          ['Averaging-down P&L', signed$(avgd.averaged_down?.total_pnl)],
          ['Clean-entry P&L', signed$(avgd.clean_entries?.total_pnl)],
          ['Average winner', signed$(asym.avg_win)],
          ['Average loser', fmt$(-Math.abs(asym.avg_loss || 0))],
          ['Actual reward/risk', asym.reward_risk == null ? '—' : `${asym.reward_risk}:1`],
          ['First 10 min', signed$(time?.first_10_minutes?.total_pnl)],
          ['Rest of day', signed$(time?.rest_of_day?.total_pnl)],
          ['Last 30 min', signed$(time?.last_30_minutes?.total_pnl)],
        ].map(([k, v]) => <div key={k} style={{ padding: 12, background: 'var(--surface-inset)', borderRadius: 6 }}><div className="text-muted" style={{ fontSize: 11.5 }}>{k}</div><div className="num" style={{ fontWeight: 650, marginTop: 4 }}>{v}</div></div>)}
      </div>
      <div className="notice" style={{ fontSize: 12.5 }}>
        Premature-exit opportunity cost: {behavior?.premature_exits?.left_on_table == null ? 'Insufficient evidence — requires post-exit market data.' : fmt$(behavior.premature_exits.left_on_table)}
      </div>
    </div>
  );
}

function ProjectionTable({ data }) {
  const rows = data?.rates || [];
  return (
    <div className="scroll-x">
      <table style={{ minWidth: 760 }}>
        <thead><tr><th>Edge retained</th><th className="num">Daily edge</th><th className="num">Rest-of-year gross</th><th className="num">After current DD</th><th className="num">Months to recover</th></tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.rate}>
              <td>{Math.round(r.rate * 100)}%</td>
              <td className={`num ${tone(r.daily_edge)}`}>{signed$(r.daily_edge)}</td>
              <td className={`num ${tone(r.gross_earnings)}`}>{signed$(r.gross_earnings)}</td>
              <td className={`num ${tone(r.net_after_current_drawdown)}`}>{signed$(r.net_after_current_drawdown)}</td>
              <td className="num">{r.months_to_recover}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="text-muted" style={{ fontSize: 11.5, marginTop: 8 }}>
        Projection uses remaining weekdays as an approximation; exchange holidays are not removed.
      </div>
    </div>
  );
}

function Diagnosis({ diagnosis }) {
  if (!diagnosis) return null;
  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div className="v3-notice">
        <div>
          <b>AI diagnosis</b>
          <p style={{ marginTop: 4 }}>{diagnosis.headline}</p>
        </div>
      </div>
      <div className="grid-2">
        <div className="card" style={{ padding: 16 }}>
          <div style={{ fontWeight: 650, marginBottom: 8 }}>Where the edge lives</div>
          <ul className="v3-list">
            {(diagnosis.edge?.where_it_lives || []).map((x, i) => <li key={i} className="good">{x}</li>)}
          </ul>
        </div>
        <div className="card" style={{ padding: 16 }}>
          <div style={{ fontWeight: 650, marginBottom: 8 }}>Where it dies</div>
          <ul className="v3-list">
            {(diagnosis.edge?.where_it_dies || []).map((x, i) => <li key={i} className="bad">{x}</li>)}
          </ul>
        </div>
      </div>
      {diagnosis.daily_stop && (
        <Card title="AI stop candidate" sub="Interpretation only — modeled results remain the source of truth.">
          <div><b>{diagnosis.daily_stop.recommended_candidate == null ? 'No candidate' : fmt$(diagnosis.daily_stop.recommended_candidate)}</b></div>
          <div className="text-muted" style={{ marginTop: 5 }}>{diagnosis.daily_stop.reason}</div>
        </Card>
      )}
      <Card title="Fix — ranked mechanical rules" sub="AI can interpret the evidence, but the dollar impacts come from the deterministic engine.">
        <ol style={{ paddingLeft: 20, display: 'grid', gap: 12 }}>
          {(diagnosis.action_plan || []).map((x, i) => (
            <li key={i}>
              <b>{x.rule}</b>
              <div className="text-muted" style={{ marginTop: 3 }}>{x.why}</div>
            </li>
          ))}
        </ol>
      </Card>
      {!!diagnosis.limitations?.length && (
        <Card title="Limitations" sub="What this dataset cannot prove.">
          <ul className="v3-list">{diagnosis.limitations.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </Card>
      )}
    </div>
  );
}

export default function SmokingGunReport({ accountId, dateFrom, dateTo }) {
  const [data, setData] = useState(null);
  const [diagnosis, setDiagnosis] = useState(null);
  const [aiMeta, setAiMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [aiLoading, setAiLoading] = useState(false);
  const [error, setError] = useState('');

  const params = useMemo(() => {
    const p = {};
    if (accountId != null) p.account_id = accountId;
    if (dateFrom) p.date_from = dateFrom;
    if (dateTo) p.date_to = dateTo;
    return p;
  }, [accountId, dateFrom, dateTo]);

  useEffect(() => {
    setLoading(true);
    setError('');
    setDiagnosis(null);
    setAiMeta(null);
    smokingGunApi.get(params)
      .then(r => setData(r.data?.has_data ? r.data : null))
      .catch(e => setError(e?.response?.data?.detail || e.message || 'Could not load report.'))
      .finally(() => setLoading(false));
  }, [params]);

  const generateDiagnosis = () => {
    setAiLoading(true);
    setError('');
    smokingGunApi.diagnose(params)
      .then(r => {
        if (r.data?.unavailable) {
          setAiMeta(null);
          setError(r.data.message || 'AI diagnosis is unavailable.');
        } else {
          setDiagnosis(r.data?.diagnosis || null);
          setAiMeta(r.data?.provider ? { provider: r.data.provider, model: r.data.model } : null);
        }
      })
      .catch(e => setError(e?.response?.data?.detail || e.message || 'Could not generate diagnosis.'))
      .finally(() => setAiLoading(false));
  };

  if (loading) return <div className="skeleton" style={{ height: 420 }} />;
  if (!data) return <div className="card"><div className="empty">No completed trades in this range.</div></div>;

  return (
    <div style={{ display: 'grid', gap: 20 }}>
      <div className="v3-notice">
        <div>
          <b>Smoking Gun Report</b>
          <p style={{ marginTop: 4 }}>
            Data first. Diagnosis second. Fix last. Source metrics come from stored executions; AI is not allowed to recalculate them.
          </p>
        </div>
      </div>

      {error && <div className="notice caution">{error}</div>}

      <StatsRow data={data} />

      <Card title="1. Hold time — where the edge lives" sub="Exact buckets from entry fill to final exit fill.">
        <PnlBarChart rows={data.hold_time} />
        <HoldTable rows={data.hold_time} />
      </Card>

      <Card title="2. Daily P&L ledger" sub="Every trading day, separated by instrument, with running total and blow-up flag.">
        <DailyTable rows={data.daily_pnl} />
      </Card>

      <div className="grid-2">
        <Card title="3. Daily stop modeling" sub={`Average losing trade: ${fmt$(data.daily_stop_model?.avg_loss)}`}>
          <StopTable model={data.daily_stop_model} />
          <div style={{ marginTop: 16 }}><StopBreaches model={data.daily_stop_model} /></div>
        </Card>
        <Card title="4. Two traders" sub="5+ minute normal-size cohort versus everything outside that discipline definition.">
          <div style={{ display: 'grid', gap: 12 }}>
            {[
              ['Disciplined', data.two_traders?.disciplined],
              ['Destructive', data.two_traders?.destructive],
            ].map(([label, r]) => (
              <div key={label} style={{ padding: 14, background: 'var(--surface-inset)', borderRadius: 6 }}>
                <div className="text-muted" style={{ fontSize: 11.5 }}>{label}</div>
                <div className={`num ${tone(r?.total_pnl)}`} style={{ fontSize: 24, fontWeight: 700 }}>{signed$(r?.total_pnl)}</div>
                <div className="text-muted">{r?.trade_count || 0} trades · {r?.win_rate || 0}% win · {signed$(r?.avg_pnl)} / trade</div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card title="5. Ticker ranking" sub="EDGE requires positive P&L, at least 55% wins, and 5+ completed trades. Thin positive samples stay MARGINAL.">
        <TickerTable rows={data.ticker_ranking} />
      </Card>

      <Card title="6. Behavioral flaws ranked by dollar impact" sub="Each P&L-if-removed scenario is independent. Behavior cohorts can overlap, so dollar impacts must not be added together.">
        <FlawTable flaws={data.behavior?.ranked_flaws} />
      </Card>

      <div className="grid-2">
        <Card title="7. Time of day" sub="30-minute entry blocks.">
          <PnlBarChart rows={data.time_analysis?.half_hour_blocks} />
        </Card>
        <Card title="8. Position-size cross-check" sub="Small/big is relative to the median inside each instrument family, so option contracts are never compared directly with stock dollars.">
          <div style={{ display: 'grid', gap: 12 }}>
            {[
              ['Small size + long hold', data.position_size?.cross_reference?.small_size_long_hold],
              ['Big size + short hold', data.position_size?.cross_reference?.big_size_short_hold],
            ].map(([label, r]) => (
              <div key={label} style={{ padding: 14, background: 'var(--surface-inset)', borderRadius: 6 }}>
                <b>{label}</b>
                <div className={`num ${tone(r?.total_pnl)}`} style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{signed$(r?.total_pnl)}</div>
                <div className="text-muted">{r?.trade_count || 0} trades · {r?.win_rate || 0}% win</div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid-2">
        <Card title="9. Option position-size buckets" sub="Contracts per completed options trade.">
          <GenericStatsTable rows={data.position_size?.options} />
        </Card>
        <Card title="10. Share notional buckets" sub="Entry notional per completed stock trade.">
          <GenericStatsTable rows={data.position_size?.shares_by_notional} />
        </Card>
      </div>

      <Card title="11. Behavioral evidence" sub="Revenge depth, overtrading, tilt sizing, FOMO re-entry, averaging down, asymmetry, and session timing.">
        <BehaviorEvidence behavior={data.behavior} time={data.time_analysis} />
        <div className="grid-2" style={{ marginTop: 18 }}>
          <div><b>Day of week</b><GenericStatsTable rows={data.time_analysis?.day_of_week} first="Day" /></div>
          <div><b>30-minute entry blocks</b><PnlBarChart rows={data.time_analysis?.half_hour_blocks} height={220} /></div>
        </div>
      </Card>

      <Card title="12. Forward projections" sub={`Proven disciplined-cohort daily edge: ${signed$(data.projections?.proven_daily_edge)}`}>
        <ProjectionTable data={data.projections} />
      </Card>

      <Card title="13. AI diagnosis" sub="The AI receives the deterministic report as locked evidence and returns interpretation plus mechanical rules.">
        <button type="button" className="btn btn-primary" disabled={aiLoading} onClick={generateDiagnosis}>
          {aiLoading ? 'Analyzing verified data…' : diagnosis ? 'Refresh AI diagnosis' : 'Generate AI diagnosis'}
        </button>
        {aiMeta && (
          <div className="text-muted" style={{ marginTop: 8, fontSize: 11.5 }}>
            Generated by {aiMeta.provider === 'groq' ? 'Groq' : 'Anthropic'} · {aiMeta.model}
          </div>
        )}
      </Card>

      <Diagnosis diagnosis={diagnosis} />
    </div>
  );
}
