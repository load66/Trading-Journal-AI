import { useState, useEffect } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts';
import { edgeReportApi } from '../api';
import DateRangePicker from './DateRangePicker';

const fmt$ = (v) =>
  `$${Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

const fmtMinutes = (minutes, compact = false) => {
  if (minutes == null) return 'n/a';
  const value = Number(minutes);
  if (compact) {
    return `${value.toLocaleString('en-US', { maximumFractionDigits: 1 })} min`;
  }
  if (value < 60) {
    return `${value.toLocaleString('en-US', { maximumFractionDigits: 1 })} min`;
  }
  return `${Math.floor(value / 60)}h ${Math.round(value % 60)}m`;
};

const tradeLabel = (trade) => {
  if (!trade?.option_expiry || trade?.option_strike == null || !trade?.option_type) {
    return trade?.ticker || 'Unknown ticker';
  }
  const expiry = new Date(`${trade.option_expiry}T00:00:00Z`);
  const expiryLabel = Number.isNaN(expiry.getTime())
    ? trade.option_expiry
    : expiry.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
  const strike = Number(trade.option_strike).toLocaleString('en-US', { maximumFractionDigits: 2 });
  const optionType = String(trade.option_type).toLowerCase();
  return `${trade.ticker} ${expiryLabel} $${strike} ${optionType.charAt(0).toUpperCase()}${optionType.slice(1)}`;
};

function Section({ title, children }) {
  return (
    <div className="card" style={{ padding: '20px 24px 24px' }}>
      <div style={{
        fontSize: 13, fontWeight: 600, color: 'var(--text-muted)',
        textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 16,
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function NoData({ msg }) {
  return (
    <div className="empty" style={{ padding: '24px 0' }}>
      {msg || 'Not enough data yet.'}
    </div>
  );
}

const barTip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  const v = payload[0]?.value || 0;
  return (
    <div style={{
      background: 'var(--surface-control)', border: '1px solid var(--divider-strong)',
      borderRadius: 'var(--radius-md)', padding: '8px 12px', fontSize: 13, boxShadow: 'var(--shadow-dropdown)',
    }}>
      <div style={{ color: 'var(--text-secondary)', marginBottom: 2 }}>{label}</div>
      <div className="num" style={{ fontWeight: 600 }}>{v}</div>
    </div>
  );
};


export function RMultipleDist({ data }) {
  if (!data || data.every(d => d.count === 0)) return <NoData msg="No Realized R data yet. Save planned risk on trades so the journal can calculate P&L ÷ risk." />;
  const filtered = data.filter(d => d.count > 0);
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={filtered} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--divider-soft)" />
        <XAxis dataKey="bucket" tick={{ fill: 'var(--text-secondary)', fontSize: 12 }} axisLine={false} tickLine={false}
          tickFormatter={v => `${Number(v) >= 0 ? '+' : ''}${v}R`} />
        <YAxis tick={{ fill: 'var(--text-secondary)', fontSize: 12 }} axisLine={false} tickLine={false} />
        <ReferenceLine x="0" stroke="var(--divider-strong)" />
        <Tooltip content={barTip} cursor={{ fill: 'var(--surface-hover)' }} />
        <Bar dataKey="count" radius={[3, 3, 0, 0]}>
          {filtered.map((entry, i) => (
            <Cell key={i} fill={Number(entry.bucket) >= 0 ? 'var(--result-pos)' : 'var(--result-neg)'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function EmotionTable({ data }) {
  if (!data || data.length === 0) return <NoData msg="No emotion data yet. Add an Emotion tag or Emotional State during review." />;

  return (
    <div className="scroll-x">
    <table style={{ minWidth: 520 }}>
      <thead>
        <tr>
          {['State', 'Trades', 'Win Rate', 'Avg P&L', 'Avg R'].map(h => (
            <th key={h} className={h === 'State' ? undefined : 'num'}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {data.map((row, i) => {
          const pnlColor = row.avg_pnl >= 0 ? 'var(--result-pos)' : 'var(--result-neg)';
          const wr = row.win_rate;
          const wrColor = wr >= 55 ? 'var(--result-pos)' : wr >= 40 ? 'var(--caution)' : 'var(--result-neg)';
          return (
            <tr key={i}>
              <td style={{ textTransform: 'capitalize', fontWeight: 600 }}>
                {row.state}
              </td>
              <td className="num">{row.trade_count}</td>
              <td className="num" style={{ color: wrColor, fontWeight: 600 }}>
                {wr.toFixed(1)}%
              </td>
              <td className="num" style={{ color: pnlColor, fontWeight: 600 }}>
                {row.avg_pnl >= 0 ? '+' : '-'}{fmt$(Math.abs(row.avg_pnl))}
              </td>
              <td className="num" style={{ color: row.avg_r != null ? (row.avg_r >= 0 ? 'var(--result-pos)' : 'var(--result-neg)') : 'var(--text-secondary)' }}>
                {row.avg_r != null ? `${row.avg_r >= 0 ? '+' : ''}${row.avg_r.toFixed(2)}R` : 'n/a'}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
    </div>
  );
}

export function MistakeFreq({ data }) {
  if (!data || data.length === 0) return <NoData msg="No mistake data. Tag mistakes per trade." />;
  return (
    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 36)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 8, left: 8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--divider-soft)" horizontal={false} />
        <XAxis type="number" tick={{ fill: 'var(--text-secondary)', fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="mistake" tick={{ fill: 'var(--text-secondary)', fontSize: 12.5 }} axisLine={false} tickLine={false} width={170} />
        <Tooltip content={barTip} cursor={{ fill: 'var(--surface-hover)' }} />
        <Bar dataKey="count" fill="var(--caution)" radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function HoldTime({ data }) {
  const excluded = data?.overnight_excluded || [];
  if (!data || (data.winners_avg_min == null && data.losers_avg_min == null && excluded.length === 0)) {
    return <NoData msg="Not enough trade timing data yet." />;
  }
  const bars = [
    { label: 'Winners', value: data.winners_avg_min, fill: 'var(--result-pos)' },
    { label: 'Losers', value: data.losers_avg_min, fill: 'var(--result-neg)' },
  ].filter(b => b.value != null);

  return (
    <div>
      <div style={{ display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap', paddingTop: 8 }}>
        {bars.map(b => (
          <div key={b.label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, flex: '1 1 120px' }}>
            <div className="num" style={{ fontSize: 30, fontWeight: 600, fontFamily: 'var(--font-display)', color: b.fill, lineHeight: 1 }}>
              {fmtMinutes(b.value)}
            </div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Avg hold ({b.label.toLowerCase()})</div>
          </div>
        ))}
        {bars.length === 2 && bars[1].value != null && bars[0].value != null && (
          <div style={{ flex: '1 1 160px', fontSize: 14, color: 'var(--text-secondary)', lineHeight: 1.55 }}>
            {bars[1].value > bars[0].value
              ? `You hold losers ${(bars[1].value / bars[0].value).toFixed(1)}x longer than winners. Consider cutting losses faster.`
              : `You hold winners ${(bars[0].value / bars[1].value).toFixed(1)}x longer than losers. Good discipline, letting winners run.`}
          </div>
        )}
      </div>
      {excluded.length > 0 && (
        <div role="status" style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid var(--divider-soft)', fontSize: 12, color: 'var(--text-muted)' }}>
          <div style={{ fontWeight: 600, marginBottom: 5 }}>
            {excluded.length} overnight {excluded.length === 1 ? 'trade' : 'trades'} excluded
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px' }}>
            {excluded.map((trade, index) => (
              <span key={trade.trade_group || `${trade.ticker}-${index}`}>
                {tradeLabel(trade)} · {fmtMinutes(trade.hold_minutes, true)} ·{' '}
                <span style={{ color: Number(trade.net_pnl) >= 0 ? 'var(--result-pos)' : 'var(--result-neg)', fontWeight: 600 }}>
                  {Number(trade.net_pnl) >= 0 ? '+' : '-'}{fmt$(Math.abs(Number(trade.net_pnl)))}
                </span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Edge({ accountId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  useEffect(() => {
    setLoading(true);
    const params = {};
    if (accountId != null) params.account_id = accountId;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    edgeReportApi.get(params)
      .then(r => { setData(r.data); setLoading(false); })
      .catch(() => { setData(null); setLoading(false); });
  }, [accountId, dateFrom, dateTo]);

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h2 style={{ fontSize: 20, fontWeight: 700, marginBottom: 2 }}>Edge Analytics</h2>
          <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {data ? `${data.total_trades || 0} trades` : 'Loading...'}
            {data?.expectancy != null && (
              <span style={{ marginLeft: 12, color: data.expectancy >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 600 }}>
                Expectancy: {data.expectancy >= 0 ? '+' : ''}{fmt$(data.expectancy)}/trade
              </span>
            )}
          </div>
        </div>
        <DateRangePicker
          dateFrom={dateFrom}
          dateTo={dateTo}
          onChange={({ dateFrom: f, dateTo: t }) => { setDateFrom(f); setDateTo(t); }}
        />
      </div>

      {loading ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {[1, 2, 3, 4].map(i => <div key={i} className="skeleton" style={{ height: 220 }} />)}
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <Section title="R-Multiple Distribution">
              <RMultipleDist data={data?.r_multiple_dist} />
            </Section>
            <Section title="Holding Behavior">
              <HoldTime data={data?.hold_time} />
            </Section>
          </div>

          <Section title="Emotion vs. Outcome">
            <EmotionTable data={data?.emotion_outcomes} />
          </Section>

          <Section title="Mistake Frequency">
            <MistakeFreq data={data?.mistake_frequency} />
          </Section>
        </div>
      )}
    </div>
  );
}
