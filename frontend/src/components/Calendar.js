import { useState, useEffect } from 'react';
import YearBehind from '../v3/YearBehind';
import { ChevronLeft, ChevronRight, Download, Share2, X } from 'lucide-react';
import { calendarApi, kpisApi, yearlyKpisApi } from '../api';
import CalendarGrid from './CalendarGrid';
import { PageHeader, KpiStrip, KpiCell } from './ui';

const fmtPnl = (v) => {
  const n = Number(v || 0);
  const sign = n < 0 ? '-' : '';
  const abs = Math.abs(n);
  if (abs >= 1000) {
    const k = abs / 1000;
    return `${sign}$${k % 1 === 0 ? k.toFixed(0) : k.toFixed(2).replace(/\.?0+$/, '')}K`;
  }
  return `${sign}$${abs % 1 === 0 ? abs.toFixed(0) : abs.toFixed(1)}`;
};
const signedPnl = (v) => (Number(v) > 0 ? '+' : '') + fmtPnl(v);

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function getDaysInMonth(year, month) { return new Date(year, month, 0).getDate(); }
function getFirstDayOfMonth(year, month) {
  const d = new Date(year, month - 1, 1).getDay();
  return (d + 6) % 7;
}
const pad = (n) => String(n).padStart(2, '0');

const SHARE_PALETTE = {
  bg: '#06131d',
  panel: '#0a1b28',
  panel2: '#0d2332',
  border: '#18384d',
  text: '#f1f5f9',
  muted: '#91a4b7',
  dim: '#607487',
  green: '#4fd196',
  red: '#ff6572',
  blue: '#38bdf8',
  amber: '#f8b342',
};

const escapeXml = (value) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&apos;');

const metricHelp = {
  pnl: 'Money made or lost after trading results',
  win: 'Percent of closed trades that finished profitable',
  pf: 'Gross profit divided by gross loss',
  ratio: 'Average winner compared with average loser',
  days: 'Number of days with at least one trade',
};

function shareMetricCard(x, y, width, label, value, help, tone = 'neutral') {
  const valueColor = tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.text;
  return `
    <g transform="translate(${x} ${y})">
      <rect width="${width}" height="112" rx="14" fill="${SHARE_PALETTE.panel}" stroke="${SHARE_PALETTE.border}" />
      <text x="18" y="26" fill="${SHARE_PALETTE.muted}" font-size="13" font-weight="700" letter-spacing=".8">${escapeXml(label)}</text>
      <text x="18" y="62" fill="${valueColor}" font-size="26" font-weight="750">${escapeXml(value)}</text>
      <text x="18" y="88" fill="${SHARE_PALETTE.dim}" font-size="10.5">${escapeXml(help)}</text>
    </g>`;
}

export function buildMonthShareSvg({ year, month, weeks, dayData, monthPnl, winRate, profitFactor, avgWinLoss, tradingDays }) {
  const width = 1200;
  const margin = 54;
  const inner = width - margin * 2;
  const metricGap = 10;
  const metricW = (inner - metricGap * 4) / 5;
  const gridY = 366;
  const headerH = 44;
  const cellGap = 8;
  const cellW = (inner - cellGap * 4) / 5;
  const cellH = 112;
  const rows = Math.max(1, weeks.length);
  const height = gridY + headerH + rows * cellH + Math.max(0, rows - 1) * cellGap + 108;
  const pfValue = profitFactor == null ? '∞' : Number(profitFactor).toFixed(2);
  const winValue = winRate === '--' ? '--' : `${winRate}%`;
  const monthName = MONTHS[month - 1];

  const metrics = [
    ['NET P&L', signedPnl(monthPnl), metricHelp.pnl, monthPnl > 0 ? 'pos' : monthPnl < 0 ? 'neg' : 'neutral'],
    ['WIN RATE', winValue, metricHelp.win, 'neutral'],
    ['PROFIT FACTOR', pfValue, metricHelp.pf, 'neutral'],
    ['AVG WIN / LOSS', avgWinLoss, metricHelp.ratio, 'neutral'],
    ['TRADING DAYS', tradingDays, metricHelp.days, 'neutral'],
  ].map((m, i) => shareMetricCard(margin + i * (metricW + metricGap), 204, metricW, ...m)).join('');

  const weekdayHeaders = ['MON', 'TUE', 'WED', 'THU', 'FRI'].map((d, i) =>
    `<text x="${margin + i * (cellW + cellGap) + cellW / 2}" y="${gridY + 28}" text-anchor="middle" fill="${SHARE_PALETTE.muted}" font-size="13" font-weight="700" letter-spacing="1.4">${d}</text>`
  ).join('');

  const cells = weeks.map((week, wi) => week.slice(0, 5).map((day, di) => {
    const x = margin + di * (cellW + cellGap);
    const y = gridY + headerH + wi * (cellH + cellGap);
    if (!day) return `<rect x="${x}" y="${y}" width="${cellW}" height="${cellH}" rx="12" fill="${SHARE_PALETTE.panel}" opacity=".28"/>`;
    const key = `${year}-${pad(month)}-${pad(day)}`;
    const data = dayData[key];
    const pnl = Number(data?.net_pnl || 0);
    const tone = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'neutral';
    const fill = tone === 'pos' ? '#0d3028' : tone === 'neg' ? '#32171e' : SHARE_PALETTE.panel;
    const accent = tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.border;
    const pnlText = data ? signedPnl(pnl) : '';
    const tradeText = data ? `${data.trade_count} trade${data.trade_count === 1 ? '' : 's'}` : 'No trades';
    return `
      <g>
        <rect x="${x}" y="${y}" width="${cellW}" height="${cellH}" rx="12" fill="${fill}" stroke="${SHARE_PALETTE.border}" />
        <rect x="${x}" y="${y}" width="4" height="${cellH}" rx="2" fill="${accent}" />
        <text x="${x + 16}" y="${y + 26}" fill="${SHARE_PALETTE.muted}" font-size="13" font-weight="700">${day}</text>
        <text x="${x + 16}" y="${y + 65}" fill="${tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.text}" font-size="22" font-weight="750">${escapeXml(pnlText)}</text>
        <text x="${x + 16}" y="${y + 91}" fill="${SHARE_PALETTE.dim}" font-size="11.5">${escapeXml(tradeText)}</text>
      </g>`;
  }).join('')).join('');

  return {
    width,
    height,
    filename: `trading-calendar-${year}-${pad(month)}.png`,
    title: `${monthName} ${year} Trading Calendar`,
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <rect width="100%" height="100%" fill="${SHARE_PALETTE.bg}" />
      <text x="${margin}" y="72" fill="${SHARE_PALETTE.blue}" font-size="13" font-weight="750" letter-spacing="2">TRADING PERFORMANCE SNAPSHOT</text>
      <text x="${margin}" y="124" fill="${SHARE_PALETTE.text}" font-size="48" font-weight="780">${escapeXml(monthName)} ${year}</text>
      <text x="${margin}" y="158" fill="${SHARE_PALETTE.muted}" font-size="16">A simple view of your monthly results, consistency, and trading activity.</text>
      ${metrics}
      ${weekdayHeaders}
      ${cells}
      <text x="${margin}" y="${height - 44}" fill="${SHARE_PALETTE.dim}" font-size="12">Green = profitable day   •   Red = losing day   •   Trade count shows how active the session was</text>
      <text x="${width - margin}" y="${height - 44}" text-anchor="end" fill="${SHARE_PALETTE.dim}" font-size="12">AI Journal</text>
    </svg>`,
  };
}

export function buildYearShareSvg({ year, yearData, yearPnl, yearWinRate, totalTrades, tradingDays, profitableMonths }) {
  const width = 1200;
  const height = 1460;
  const margin = 54;
  const inner = width - margin * 2;
  const metricGap = 10;
  const metricW = (inner - metricGap * 4) / 5;
  const metrics = [
    ['YTD NET P&L', signedPnl(yearPnl), metricHelp.pnl, yearPnl > 0 ? 'pos' : yearPnl < 0 ? 'neg' : 'neutral'],
    ['WIN RATE', yearWinRate === '--' ? '--' : `${yearWinRate}%`, metricHelp.win, 'neutral'],
    ['TRADES', totalTrades.toLocaleString('en-US'), 'Total closed trades recorded this year', 'neutral'],
    ['TRADING DAYS', tradingDays.toLocaleString('en-US'), metricHelp.days, 'neutral'],
    ['PROFITABLE MONTHS', `${profitableMonths}/12`, 'Months that finished with positive net P&L', 'neutral'],
  ].map((m, i) => shareMetricCard(margin + i * (metricW + metricGap), 204, metricW, ...m)).join('');

  const cardGap = 16;
  const cardW = (inner - cardGap * 2) / 3;
  const cardH = 225;
  const gridY = 360;
  const cards = Array.from({ length: 12 }, (_, i) => {
    const data = yearData?.[i];
    const x = margin + (i % 3) * (cardW + cardGap);
    const y = gridY + Math.floor(i / 3) * (cardH + cardGap);
    const has = Boolean(data?.has_data);
    const pnl = Number(data?.net_pnl || 0);
    const tone = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'neutral';
    const fill = tone === 'pos' ? '#0d3028' : tone === 'neg' ? '#32171e' : SHARE_PALETTE.panel;
    const accent = tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.border;
    const win = has ? `${Number(data.win_rate || 0).toFixed(1)}%` : '--';
    const pf = has ? (data.profit_factor == null ? '∞' : Number(data.profit_factor).toFixed(2)) : '--';
    return `
      <g>
        <rect x="${x}" y="${y}" width="${cardW}" height="${cardH}" rx="16" fill="${fill}" stroke="${SHARE_PALETTE.border}" />
        <rect x="${x}" y="${y}" width="5" height="${cardH}" rx="3" fill="${accent}" />
        <text x="${x + 22}" y="${y + 34}" fill="${SHARE_PALETTE.muted}" font-size="15" font-weight="750">${MONTHS_SHORT[i].toUpperCase()}</text>
        <text x="${x + 22}" y="${y + 87}" fill="${tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.text}" font-size="34" font-weight="780">${has ? escapeXml(signedPnl(pnl)) : 'No trades'}</text>
        <text x="${x + 22}" y="${y + 132}" fill="${SHARE_PALETTE.muted}" font-size="12">Win Rate</text>
        <text x="${x + 22}" y="${y + 157}" fill="${SHARE_PALETTE.text}" font-size="18" font-weight="700">${win}</text>
        <text x="${x + 150}" y="${y + 132}" fill="${SHARE_PALETTE.muted}" font-size="12">Profit Factor</text>
        <text x="${x + 150}" y="${y + 157}" fill="${SHARE_PALETTE.text}" font-size="18" font-weight="700">${pf}</text>
        <text x="${x + 22}" y="${y + 196}" fill="${SHARE_PALETTE.dim}" font-size="11.5">${has ? `${data.total_trades} trades · ${data.trading_days} trading days` : 'No trading activity recorded'}</text>
      </g>`;
  }).join('');

  return {
    width,
    height,
    filename: `trading-calendar-${year}.png`,
    title: `${year} Trading Calendar`,
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <rect width="100%" height="100%" fill="${SHARE_PALETTE.bg}" />
      <text x="${margin}" y="72" fill="${SHARE_PALETTE.blue}" font-size="13" font-weight="750" letter-spacing="2">YEARLY TRADING PERFORMANCE</text>
      <text x="${margin}" y="124" fill="${SHARE_PALETTE.text}" font-size="48" font-weight="780">${year}</text>
      <text x="${margin}" y="158" fill="${SHARE_PALETTE.muted}" font-size="16">A beginner-friendly look at your year: results, consistency, activity, and month-to-month progress.</text>
      ${metrics}
      ${cards}
      <text x="${margin}" y="${height - 40}" fill="${SHARE_PALETTE.dim}" font-size="12">Green = profitable month   •   Red = losing month   •   Profit Factor above 1.00 means gross wins exceeded gross losses</text>
      <text x="${width - margin}" y="${height - 40}" text-anchor="end" fill="${SHARE_PALETTE.dim}" font-size="12">AI Journal</text>
    </svg>`,
  };
}

async function svgToPngBlob(svg, width, height) {
  if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return null;
  const svgBlob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
  const svgUrl = URL.createObjectURL(svgBlob);
  try {
    const image = new Image();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = reject;
      image.src = svgUrl;
    });
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) return null;
    context.drawImage(image, 0, 0, width, height);
    return await new Promise(resolve => canvas.toBlob(resolve, 'image/png', 0.96));
  } finally {
    URL.revokeObjectURL(svgUrl);
  }
}

async function saveOrShareCalendarImage(documentSpec) {
  try {
    const pngBlob = await svgToPngBlob(documentSpec.svg, documentSpec.width, documentSpec.height);
    if (!pngBlob) return 'unsupported';

    const file = typeof File === 'function'
      ? new File([pngBlob], documentSpec.filename, { type: 'image/png' })
      : null;

    if (file && navigator?.share && navigator?.canShare?.({ files: [file] })) {
      try {
        await navigator.share({
          files: [file],
          title: documentSpec.title,
          text: 'Trading performance calendar',
        });
        return 'shared';
      } catch (error) {
        if (error?.name === 'AbortError') return 'cancelled';
      }
    }

    const pngUrl = URL.createObjectURL(pngBlob);
    try {
      const link = document.createElement('a');
      link.href = pngUrl;
      link.download = documentSpec.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      return 'downloaded';
    } finally {
      setTimeout(() => URL.revokeObjectURL(pngUrl), 1500);
    }
  } catch (error) {
    console.error('Calendar image export failed', error);
    return 'error';
  }
}

function ShareMetric({ label, value, help, tone }) {
  return (
    <div className="calendar-share-stat">
      <span className="calendar-share-stat-label">{label}</span>
      <strong className={`num ${tone || ''}`}>{value}</strong>
      <small>{help}</small>
    </div>
  );
}

function ViewToggle({ view, setView }) {
  return (
    <div className="seg" role="group" aria-label="Calendar view">
      <button type="button" className="seg-btn" aria-pressed={view === 'month'} onClick={() => setView('month')}>Month</button>
      <button type="button" className="seg-btn" aria-pressed={view === 'year'} onClick={() => setView('year')}>Year</button>
    </div>
  );
}

// ── Year view: grid of 12 month cards ─────────────────────────────────────────

function MonthCard({ data, monthIdx, year, onClick, isCurrent, isFuture }) {
  const name = MONTHS_SHORT[monthIdx];
  if (isFuture) {
    return (
      <div className="month-card" style={{ opacity: 0.45 }} aria-label={`${MONTHS[monthIdx]} ${year}, upcoming`}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)' }}>{name}</div>
        <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 'auto' }}>Upcoming</div>
      </div>
    );
  }

  if (!data || !data.has_data) {
    return (
      <div className="month-card" aria-label={`${MONTHS[monthIdx]} ${year}, no trades`}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)' }}>{name}</div>
        <div style={{ fontSize: 12.5, color: 'var(--text-tertiary)', marginTop: 'auto' }}>No trades</div>
      </div>
    );
  }

  const pnlTone = data.net_pnl > 0 ? 'pos' : data.net_pnl < 0 ? 'neg' : '';
  const pfTone = data.profit_factor == null || data.profit_factor >= 1.5 ? 'pos' : data.profit_factor >= 1 ? 'text-purple' : 'neg';
  const wrTone = data.win_rate >= 55 ? 'pos' : 'neg';
  const ratio = data.avg_loss !== 0 ? Math.abs(data.avg_win / data.avg_loss).toFixed(2) : '--';

  return (
    <button
      type="button"
      className={`month-card${isCurrent ? ' current' : ''}`}
      onClick={() => onClick(monthIdx + 1)}
      aria-label={`${MONTHS[monthIdx]} ${year}: ${signedPnl(data.net_pnl)}, ${data.total_trades} trades. Open month`}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', width: '100%', gap: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-secondary)' }}>{name}</span>
        <span className={`num ${pnlTone}`} style={{ fontSize: 20, fontWeight: 600, fontFamily: 'var(--font-display)' }}>
          {signedPnl(data.net_pnl)}
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, width: '100%' }}>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Win %</div>
          <div className={`num ${wrTone}`} style={{ fontSize: 15, fontWeight: 600 }}>{data.win_rate.toFixed(1)}%</div>
        </div>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Prof. F</div>
          <div className={`num ${pfTone}`} style={{ fontSize: 15, fontWeight: 600 }}>{data.profit_factor == null ? '∞' : data.profit_factor.toFixed(2)}</div>
        </div>
        <div>
          <div style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Avg W/L</div>
          <div className="num" style={{ fontSize: 15, fontWeight: 600 }}>{ratio}</div>
        </div>
      </div>

      <div style={{ marginTop: 'auto', display: 'flex', justifyContent: 'space-between', width: '100%', fontSize: 12.5, color: 'var(--text-secondary)' }}>
        <span><span className="num">{data.total_trades}</span> trades</span>
        <span><span className="num">{data.trading_days}</span> days</span>
      </div>
    </button>
  );
}

function YearView({ year, setYear, accountId, onMonthClick, view, setView }) {
  const [yearData, setYearData] = useState(null);
  const [loading, setLoading] = useState(true);
  const today = new Date();
  const currentYear = today.getFullYear();
  const currentMonth = today.getMonth() + 1;

  useEffect(() => {
    setLoading(true);
    const params = { year };
    if (accountId != null) params.account_id = accountId;
    yearlyKpisApi.get(params)
      .then(r => { setYearData(r.data); setLoading(false); })
      .catch(() => { setYearData(null); setLoading(false); });
  }, [year, accountId]);

  // Compute year totals from months with data
  const monthsWithData = yearData ? yearData.filter(m => m.has_data) : [];
  const yearPnl = monthsWithData.reduce((s, m) => s + m.net_pnl, 0);
  const totalTrades = monthsWithData.reduce((s, m) => s + m.total_trades, 0);
  const totalWinners = monthsWithData.reduce((s, m) => s + m.winning_trades, 0);
  const yearWinRate = totalTrades > 0 ? (totalWinners / totalTrades * 100).toFixed(1) : '--';

  return (
    <div>
      <PageHeader
        title={<span className="num">{year}</span>}
        subtitle="Year view. Select a month to open it."
        actions={<>
          <button type="button" className="cal-nav" onClick={() => setYear(y => y - 1)} aria-label="Previous year"><ChevronLeft size={18} /></button>
          <button type="button" className="cal-nav" onClick={() => setYear(y => y + 1)} aria-label="Next year"><ChevronRight size={18} /></button>
          <button type="button" className="btn btn-secondary" onClick={() => setYear(currentYear)}>This year</button>
          <ViewToggle view={view} setView={setView} />
        </>}
      />

      {monthsWithData.length > 0 && (
        <KpiStrip label="Year summary">
          <KpiCell label="YTD P&L" value={<span className="num">{signedPnl(yearPnl)}</span>} tone={yearPnl > 0 ? 'pos' : yearPnl < 0 ? 'neg' : undefined} />
          <KpiCell label="Win %" value={<span className="num">{yearWinRate !== '--' ? `${yearWinRate}%` : '--'}</span>} tone={Number(yearWinRate) >= 55 ? 'pos' : undefined} />
          <KpiCell label="Trades" value={<span className="num">{totalTrades.toLocaleString('en-US')}</span>} />
        </KpiStrip>
      )}

      {loading ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 12 }}>
          {[...Array(12)].map((_, i) => <div key={i} className="skeleton" style={{ height: 136, borderRadius: 8 }} />)}
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 12 }}>
          {(yearData || []).map((mData, i) => {
            const isFuture = year > currentYear || (year === currentYear && i + 1 > currentMonth);
            const isCurrent = year === currentYear && i + 1 === currentMonth;
            return (
              <MonthCard
                key={i}
                data={mData}
                monthIdx={i}
                year={year}
                onClick={onMonthClick}
                isCurrent={isCurrent}
                isFuture={isFuture}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Month view ─────────────────────────────────────────────────────────────────

function MonthView({ year, month, setYear, setMonth, accountId, onDayClick, view, setView, shareMode, onShareModeChange }) {
  const today = new Date();
  const [dayData, setDayData] = useState({});
  const [loading, setLoading] = useState(true);
  const [monthKpis, setMonthKpis] = useState(null);

  useEffect(() => {
    setLoading(true);
    const lastDay = new Date(year, month, 0).getDate();
    const dateFrom = `${year}-${pad(month)}-01`;
    const dateTo = `${year}-${pad(month)}-${pad(lastDay)}`;
    const params = { year, month };
    const kpiParams = { date_from: dateFrom, date_to: dateTo };
    if (accountId != null) { params.account_id = accountId; kpiParams.account_id = accountId; }

    calendarApi.get(params)
      .then(r => {
        const map = {};
        for (const d of r.data) map[d.date] = d;
        setDayData(map);
        setLoading(false);
      })
      .catch(() => setLoading(false));

    kpisApi.get(kpiParams)
      .then(r => setMonthKpis(r.data))
      .catch(() => setMonthKpis(null));
  }, [year, month, accountId]);

  const prevMonth = () => { if (month === 1) { setYear(y => y - 1); setMonth(12); } else setMonth(m => m - 1); };
  const nextMonth = () => { if (month === 12) { setYear(y => y + 1); setMonth(1); } else setMonth(m => m + 1); };
  const goToday = () => { setYear(today.getFullYear()); setMonth(today.getMonth() + 1); };

  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = getFirstDayOfMonth(year, month);
  const cells = [];
  for (let i = 0; i < firstDay; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);
  const allWeeks = [];
  for (let i = 0; i < cells.length; i += 7) allWeeks.push(cells.slice(i, i + 7));
  const weeks = allWeeks.filter(w => w.slice(0, 5).some(d => d !== null));

  const allDays = Object.values(dayData);
  const monthPnl = allDays.reduce((s, d) => s + d.net_pnl, 0);
  const tradingDays = allDays.length;

  const pf = monthKpis ? monthKpis.profit_factor : undefined;
  const pfTone = monthKpis && (pf == null || pf >= 1.5) ? 'pos' : undefined;

  const winRate = monthKpis ? Number(monthKpis.win_rate || 0).toFixed(1) : '--';
  const avgWinLoss = monthKpis && Math.abs(monthKpis.avg_loss || 0) > 0
    ? (Math.abs(monthKpis.avg_win || 0) / Math.abs(monthKpis.avg_loss)).toFixed(2)
    : '--';

  return (
    <div className={`calendar-month-view${shareMode ? ' calendar-share-mode' : ''}`}>
      {shareMode ? (
        <>
          <div className="calendar-share-head">
            <div>
              <div className="calendar-share-eyebrow">Trading Calendar</div>
              <h1>{MONTHS[month - 1]} <span className="num">{year}</span></h1>
            </div>
            <div className="calendar-share-head-actions">
              <button type="button" className="cal-nav" onClick={prevMonth} aria-label="Previous month"><ChevronLeft size={16} /></button>
              <button type="button" className="cal-nav" onClick={nextMonth} aria-label="Next month"><ChevronRight size={16} /></button>
              <button type="button" className="btn btn-ghost btn-sm calendar-share-exit" onClick={() => onShareModeChange(false)}>
                <X size={14} /> Exit
              </button>
            </div>
          </div>

          <section className="calendar-share-stats" aria-label="Month summary">
            <div className="calendar-share-stat">
              <span>MTD</span>
              <strong className={`num ${monthPnl > 0 ? 'pos' : monthPnl < 0 ? 'neg' : ''}`}>{signedPnl(monthPnl)}</strong>
            </div>
            <div className="calendar-share-stat">
              <span>Win</span>
              <strong className="num">{winRate === '--' ? '--' : `${winRate}%`}</strong>
            </div>
            <div className="calendar-share-stat">
              <span>PF</span>
              <strong className={`num ${monthKpis && pf != null && pf >= 1 && pf < 1.5 ? 'text-purple' : ''}`}>{monthKpis ? (pf == null ? '∞' : Number(pf).toFixed(2)) : '--'}</strong>
            </div>
            <div className="calendar-share-stat">
              <span>Avg W/L</span>
              <strong className="num">{avgWinLoss}</strong>
            </div>
            <div className="calendar-share-stat">
              <span>Days</span>
              <strong className="num">{tradingDays}</strong>
            </div>
          </section>
        </>
      ) : (
        <>
          <PageHeader
            title={<>{MONTHS[month - 1]} <span className="num">{year}</span></>}
            subtitle="Select a trading day to open its Day Review."
            actions={<>
              <button type="button" className="cal-nav" onClick={prevMonth} aria-label="Previous month"><ChevronLeft size={18} /></button>
              <button type="button" className="cal-nav" onClick={nextMonth} aria-label="Next month"><ChevronRight size={18} /></button>
              <button type="button" className="btn btn-secondary" onClick={goToday}>This month</button>
              <ViewToggle view={view} setView={setView} />
              <button type="button" className="btn btn-ghost calendar-share-trigger" onClick={() => onShareModeChange(true)}>
                <Share2 size={15} /> Share View
              </button>
            </>}
          />

          <KpiStrip label="Month summary">
            <KpiCell label="MTD P&L" value={<span className="num">{signedPnl(monthPnl)}</span>} tone={monthPnl > 0 ? 'pos' : monthPnl < 0 ? 'neg' : undefined} />
            <KpiCell
              label="Win %"
              value={<span className="num">{winRate === '--' ? '--' : `${winRate}%`}</span>}
              tone={monthKpis && monthKpis.win_rate >= 55 ? 'pos' : undefined}
            />
            <KpiCell
              label="Prof. Factor"
              value={<span className={`num ${monthKpis && pf != null && pf >= 1 && pf < 1.5 ? 'text-purple' : ''}`}>{monthKpis ? (pf == null ? '∞' : Number(pf).toFixed(2)) : '--'}</span>}
              tone={pfTone}
            />
            <KpiCell label="Avg W/L" value={<span className="num">{avgWinLoss}</span>} />
            <KpiCell label="Trading days" value={<span className="num">{tradingDays}</span>} />
          </KpiStrip>
        </>
      )}

      <section className={`card${shareMode ? ' calendar-share-card' : ''}`}>
        {loading ? (
          <div className="empty" role="status">Loading...</div>
        ) : (
          <CalendarGrid
            weeks={weeks}
            dayData={dayData}
            year={year}
            month={month}
            onDayClick={onDayClick}
            size="full"
            compact={shareMode}
          />
        )}
      </section>
    </div>
  );
}

// ── Main Calendar (view switcher) ──────────────────────────────────────────────

export default function Calendar({ accountId, onDayClick, shareMode = false, onShareModeChange = () => {} }) {
  const today = new Date();
  const [view, setView] = useState('month');
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState(today.getMonth() + 1);

  const switchToMonth = (m) => { setMonth(m); setView('month'); };

  useEffect(() => {
    if (view === 'year' && shareMode) onShareModeChange(false);
  }, [view, shareMode, onShareModeChange]);

  return (
    <div className={`calendar-page${shareMode ? ' calendar-page-share' : ''}`}>
      {view === 'year' ? (
        <YearView year={year} setYear={setYear} accountId={accountId} onMonthClick={switchToMonth} view={view} setView={setView} />
      ) : (
        <MonthView
          year={year}
          month={month}
          setYear={setYear}
          setMonth={setMonth}
          accountId={accountId}
          onDayClick={onDayClick}
          view={view}
          setView={setView}
          shareMode={shareMode}
          onShareModeChange={onShareModeChange}
        />
      )}

      {!shareMode && (
        <section className="v3-band" style={{ borderBottom: 0 }}>
          <div className="v3-sec-head">
            <div>
              <h2 className="v3-h">The year behind it</h2>
              <p className="v3-h-sub">Each month closing where the next one opens</p>
            </div>
          </div>
          <YearBehind accountId={accountId} />
        </section>
      )}
    </div>
  );
}
