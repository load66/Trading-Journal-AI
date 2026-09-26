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
    <div className="grid-4" style={{ gap: 10 }}>
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
        <thead><tr><th>Ticker</th><th>Status</th><th className="num">Trades</th><th className="num">P&L</th><th className="num">Win %</th><th className="num">$/trade</th></tr></thead>
        <tbody>
          {(rows || []).map(r => (
            <tr key={r.ticker}>
              <td style={{ fontWeight: 700 }}>{r.ticker}</td>
              <td><span className={`chip ${r.total_pnl >= 0 ? 'pos' : 'neg'}`}>{r.label}</span></td>
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
        <thead><tr><th>Priority</th><th>Behavior</th><th className="num">Trades</th><th className="num">Observed P&L</th><th className="num">Dollar impact</th></tr></thead>
        <tbody>
          {flaws.map((r, i) => (
            <tr key={r.name}>
              <td className="num">#{i + 1}</td>
              <td style={{ fontWeight: 600 }}>{r.name}</td>
              <td className="num">{r.trade_count}</td>
              <td className="num neg">{fmt$(r.pnl)}</td>
              <td className="num neg" style={{ fontWeight: 700 }}>{fmt$(r.dollar_impact)}</td>
            </tr>
          ))}
        </tbody>
      </table>
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
    </div>
  );
}

export default function SmokingGunReport({ accountId, dateFrom, dateTo }) {
  const [data, setData] = useState(null);
  const [diagnosis, setDiagnosis] = useState(null);
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
        if (r.data?.unavailable) setError(r.data.message || 'AI diagnosis is unavailable.');
        else setDiagnosis(r.data?.diagnosis || null);
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

      <div className="grid-2">
        <Card title="2. Daily stop modeling" sub={`Average losing trade: ${fmt$(data.daily_stop_model?.avg_loss)}`}>
          <StopTable model={data.daily_stop_model} />
        </Card>
        <Card title="3. Two traders" sub="5+ minute normal-size cohort versus everything outside that discipline definition.">
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

      <Card title="4. Ticker ranking" sub="EDGE / MARGINAL / LEAK / BLEEDING / HEMORRHAGE is generated from actual P&L contribution and win rate.">
        <TickerTable rows={data.ticker_ranking} />
      </Card>

      <Card title="5. Behavioral flaws ranked by dollar impact" sub="Counterfactual impact is the negative P&L attributable to the detected behavior cohort; overlapping behaviors can overlap in dollars.">
        <FlawTable flaws={data.behavior?.ranked_flaws} />
      </Card>

      <div className="grid-2">
        <Card title="6. Time of day" sub="30-minute entry blocks.">
          <PnlBarChart rows={data.time_analysis?.half_hour_blocks} />
        </Card>
        <Card title="7. Position-size cross-check" sub="Median size splits small vs big; long hold is 5+ minutes.">
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

      <Card title="8. Forward projections" sub={`Proven disciplined-cohort daily edge: ${signed$(data.projections?.proven_daily_edge)}`}>
        <ProjectionTable data={data.projections} />
      </Card>

      <Card title="9. AI diagnosis" sub="The AI receives the deterministic report as locked evidence and returns interpretation plus mechanical rules.">
        <button type="button" className="btn btn-primary" disabled={aiLoading} onClick={generateDiagnosis}>
          {aiLoading ? 'Analyzing verified data…' : diagnosis ? 'Refresh AI diagnosis' : 'Generate AI diagnosis'}
        </button>
      </Card>

      <Diagnosis diagnosis={diagnosis} />
    </div>
  );
}
