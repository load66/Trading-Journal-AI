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

export const SHARE_BASELINES = {
  winRate: 50,
  profitFactor: 1.3,
  avgWinLoss: 1.2,
};

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
  pnl: 'Your total net trading result',
  win: 'How often a closed trade finished profitable',
  pf: 'Gross profits compared with gross losses',
  ratio: 'Size of the average winner vs. average loser',
  days: 'Sessions with at least one recorded trade',
};

const shareSvgStyle = `
  text { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
  .tabular { font-variant-numeric: tabular-nums; }
`;

function fmtShareDayPnl(value) {
  const n = Number(value || 0);
  const sign = n > 0 ? '+' : n < 0 ? '-' : '';
  const abs = Math.abs(n);
  const currency = String.fromCharCode(36);
  if (abs >= 1000) {
    return sign + currency + (abs / 1000).toFixed(abs >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'K';
  }
  return sign + currency + Math.round(abs).toLocaleString('en-US');
}

function localDateKey(date = new Date()) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function targetStatus(actual, target) {
  const n = Number(actual);
  if (!Number.isFinite(n)) return { label: 'No data', tone: 'muted' };
  return n >= target
    ? { label: 'Goal met', tone: 'pos' }
    : { label: 'Below goal', tone: 'amber' };
}

function targetLine(actual, target, suffix = '') {
  const status = targetStatus(actual, target);
  const targetText = suffix === '%' ? `${target.toFixed(0)}%` : target.toFixed(2);
  return {
    text: `Goal ≥ ${targetText} · ${status.label}`,
    tone: status.tone,
  };
}

function shareMetricCard(x, y, width, label, value, help, tone = 'neutral', target = null) {
  const valueColor = tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.text;
  const targetColor = target?.tone === 'pos'
    ? SHARE_PALETTE.green
    : target?.tone === 'amber'
      ? SHARE_PALETTE.amber
      : SHARE_PALETTE.dim;
  const footer = target?.text || help;
  return `
    <g transform="translate(${x} ${y})">
      <rect width="${width}" height="96" rx="14" fill="${SHARE_PALETTE.panel}" stroke="${SHARE_PALETTE.border}" />
      <text x="16" y="23" fill="${SHARE_PALETTE.muted}" font-size="11.5" font-weight="750" letter-spacing=".75">${escapeXml(label)}</text>
      <text class="tabular" x="16" y="55" fill="${valueColor}" font-size="25" font-weight="780">${escapeXml(value)}</text>
      <text x="16" y="78" fill="${target ? targetColor : SHARE_PALETTE.dim}" font-size="9.5" font-weight="${target ? 700 : 500}">${escapeXml(footer)}</text>
    </g>`;
}

function shareInsightCard(x, y, width, label, value, note, tone = 'neutral') {
  const valueColor = tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : tone === 'blue' ? SHARE_PALETTE.blue : SHARE_PALETTE.text;
  return `
    <g transform="translate(${x} ${y})">
      <rect width="${width}" height="62" rx="12" fill="${SHARE_PALETTE.panel2}" stroke="${SHARE_PALETTE.border}" />
      <text x="14" y="19" fill="${SHARE_PALETTE.dim}" font-size="9.5" font-weight="750" letter-spacing=".8">${escapeXml(label)}</text>
      <text class="tabular" x="14" y="42" fill="${valueColor}" font-size="17" font-weight="780">${escapeXml(value)}</text>
      <text x="${width - 14}" y="42" text-anchor="end" fill="${SHARE_PALETTE.muted}" font-size="9.5">${escapeXml(note)}</text>
    </g>`;
}

export function buildMonthShareSvg({
  year,
  month,
  weeks,
  dayData,
  monthPnl,
  winRate,
  profitFactor,
  avgWinLoss,
  tradingDays,
  asOfDate,
}) {
  const width = 1200;
  const margin = 50;
  const inner = width - margin * 2;
  const metricGap = 10;
  const metricW = (inner - metricGap * 4) / 5;
  const insightGap = 10;
  const insightW = (inner - insightGap * 3) / 4;
  const gridY = 366;
  const headerH = 34;
  const cellGap = 7;
  const cellW = (inner - cellGap * 4) / 5;
  const cellH = 100;
  const rows = Math.max(1, weeks.length);
  const height = gridY + headerH + rows * cellH + Math.max(0, rows - 1) * cellGap + 78;
  const pfValue = profitFactor == null ? '∞' : Number(profitFactor).toFixed(2);
  const winValue = winRate === '--' ? '--' : `${winRate}%`;
  const monthName = MONTHS[month - 1];
  const monthShort = MONTHS_SHORT[month - 1];
  const entries = Object.entries(dayData || {}).filter(([, data]) => data && Number(data.trade_count || 0) > 0);
  const greenDays = entries.filter(([, data]) => Number(data.net_pnl || 0) > 0).length;
  const redDays = entries.filter(([, data]) => Number(data.net_pnl || 0) < 0).length;
  const flatDays = Math.max(0, entries.length - greenDays - redDays);
  const totalTrades = entries.reduce((sum, [, data]) => sum + Number(data.trade_count || 0), 0);
  const best = entries.reduce((winner, current) => (
    !winner || Number(current[1].net_pnl || 0) > Number(winner[1].net_pnl || 0) ? current : winner
  ), null);
  const dayLabel = entry => entry ? `${monthShort} ${Number(entry[0].slice(-2))}` : '—';
  const asOfCandidate = asOfDate instanceof Date ? asOfDate : new Date(asOfDate || Date.now());
  const asOf = Number.isNaN(asOfCandidate.getTime()) ? new Date() : asOfCandidate;
  const todayKey = localDateKey(asOf);
  const greenRate = tradingDays > 0 ? Math.round((greenDays / tradingDays) * 100) : 0;

  const monthWinTarget = targetLine(Number(winRate), SHARE_BASELINES.winRate, '%');
  const monthPfTarget = targetLine(Number(profitFactor), SHARE_BASELINES.profitFactor);
  const monthRatioTarget = targetLine(Number(avgWinLoss), SHARE_BASELINES.avgWinLoss);

  const metrics = [
    ['NET P&L', signedPnl(monthPnl), metricHelp.pnl, monthPnl > 0 ? 'pos' : monthPnl < 0 ? 'neg' : 'neutral', null],
    ['WIN RATE', winValue, metricHelp.win, 'neutral', monthWinTarget],
    ['PROFIT FACTOR', pfValue, metricHelp.pf, 'neutral', monthPfTarget],
    ['AVG WIN / LOSS', avgWinLoss, metricHelp.ratio, 'neutral', monthRatioTarget],
    ['TRADING DAYS', tradingDays, metricHelp.days, 'neutral', null],
  ].map((m, i) => shareMetricCard(margin + i * (metricW + metricGap), 154, metricW, ...m)).join('');

  const avgTradingDay = tradingDays > 0 ? monthPnl / tradingDays : 0;
  const insights = [
    ['BEST DAY', best ? fmtShareDayPnl(best[1].net_pnl) : '—', dayLabel(best), 'pos'],
    ['AVG / TRADING DAY', tradingDays ? fmtShareDayPnl(avgTradingDay) : '—', 'Net P&L ÷ trading days', avgTradingDay > 0 ? 'pos' : 'neutral'],
    ['GREEN DAYS', `${greenDays}/${tradingDays || 0}`, tradingDays ? `${greenRate}% of sessions` : 'No sessions yet', 'blue'],
    ['TOTAL TRADES', totalTrades.toLocaleString('en-US'), `${redDays} red · ${flatDays} flat`, 'neutral'],
  ].map((m, i) => shareInsightCard(margin + i * (insightW + insightGap), 262, insightW, ...m)).join('');

  const weekdayHeaders = ['MON', 'TUE', 'WED', 'THU', 'FRI'].map((d, i) =>
    `<text x="${margin + i * (cellW + cellGap) + cellW / 2}" y="${gridY + 22}" text-anchor="middle" fill="${SHARE_PALETTE.muted}" font-size="11.5" font-weight="750" letter-spacing="1.5">${d}</text>`
  ).join('');

  const cells = weeks.map((week, wi) => week.slice(0, 5).map((day, di) => {
    const x = margin + di * (cellW + cellGap);
    const y = gridY + headerH + wi * (cellH + cellGap);
    if (!day) {
      return `<rect x="${x}" y="${y}" width="${cellW}" height="${cellH}" rx="12" fill="${SHARE_PALETTE.panel}" opacity=".18"/>`;
    }

    const key = `${year}-${pad(month)}-${pad(day)}`;
    const data = dayData[key];
    const isFuture = key > todayKey;
    const isToday = key === todayKey;
    const pnl = Number(data?.net_pnl || 0);
    const tone = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'neutral';
    const fill = data
      ? (tone === 'pos' ? '#0d3028' : tone === 'neg' ? '#32171e' : SHARE_PALETTE.panel)
      : (isFuture ? '#081722' : '#091923');
    const accent = data
      ? (tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.border)
      : (isFuture ? '#24465b' : '#173246');
    const pnlText = data ? fmtShareDayPnl(pnl) : (isFuture ? 'UPCOMING' : '—');
    const winDetail = data && data.win_rate != null ? ` · ${Number(data.win_rate).toFixed(0)}% win` : '';
    const tradeText = data
      ? `${data.trade_count} trade${data.trade_count === 1 ? '' : 's'}${winDetail}`
      : (isFuture ? 'Future session' : 'No trades recorded');
    const valueColor = data
      ? (tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.text)
      : (isFuture ? SHARE_PALETTE.muted : SHARE_PALETTE.dim);

    return `
      <g>
        <rect x="${x}" y="${y}" width="${cellW}" height="${cellH}" rx="12" fill="${fill}" stroke="${isToday ? SHARE_PALETTE.blue : SHARE_PALETTE.border}" stroke-width="${isToday ? 2 : 1}" />
        <rect x="${x}" y="${y}" width="4" height="${cellH}" rx="2" fill="${accent}" />
        <text class="tabular" x="${x + 15}" y="${y + 23}" fill="${SHARE_PALETTE.muted}" font-size="11.5" font-weight="750">${day}</text>
        <text class="tabular" x="${x + 15}" y="${y + 59}" fill="${valueColor}" font-size="${data ? 20 : 11}" font-weight="${data ? 780 : 750}" letter-spacing="${data ? '-.2' : '1'}">${escapeXml(pnlText)}</text>
        <text x="${x + 15}" y="${y + 82}" fill="${SHARE_PALETTE.dim}" font-size="9.7" font-weight="500">${escapeXml(tradeText)}</text>
      </g>`;
  }).join('')).join('');

  const activitySummary = `${tradingDays} trading day${tradingDays === 1 ? '' : 's'} · ${totalTrades} trade${totalTrades === 1 ? '' : 's'} · ${greenDays} green / ${redDays} red day${redDays === 1 ? '' : 's'}`;

  return {
    width,
    height,
    filename: `trading-calendar-${year}-${pad(month)}.png`,
    title: `${monthName} ${year} Trading Calendar`,
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <defs>
        <linearGradient id="shareBg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#071722" />
          <stop offset="58%" stop-color="${SHARE_PALETTE.bg}" />
          <stop offset="100%" stop-color="#041019" />
        </linearGradient>
      </defs>
      <style>${shareSvgStyle}</style>
      <rect width="100%" height="100%" fill="url(#shareBg)" />
      <rect x="${margin}" y="38" width="5" height="48" rx="2.5" fill="${SHARE_PALETTE.blue}" />
      <text x="${margin + 18}" y="50" fill="${SHARE_PALETTE.blue}" font-size="11.5" font-weight="780" letter-spacing="2">AI JOURNAL · MONTHLY PERFORMANCE</text>
      <text x="${margin + 18}" y="94" fill="${SHARE_PALETTE.text}" font-size="43" font-weight="800" letter-spacing="-1">${escapeXml(monthName)} ${year}</text>
      <text x="${margin + 18}" y="123" fill="${SHARE_PALETTE.muted}" font-size="14.5" font-weight="500">${escapeXml(activitySummary)}</text>
      <rect x="${width - 176}" y="46" width="126" height="30" rx="15" fill="${SHARE_PALETTE.panel2}" stroke="${SHARE_PALETTE.border}" />
      <text x="${width - 113}" y="66" text-anchor="middle" fill="${SHARE_PALETTE.muted}" font-size="10.5" font-weight="750" letter-spacing="1.2">MONTHLY SNAPSHOT</text>
      ${metrics}
      ${insights}
      <text x="${margin}" y="${gridY - 10}" fill="${SHARE_PALETTE.text}" font-size="12" font-weight="750" letter-spacing="1.2">DAILY PERFORMANCE</text>
      ${weekdayHeaders}
      ${cells}
      <line x1="${margin}" y1="${height - 54}" x2="${width - margin}" y2="${height - 54}" stroke="${SHARE_PALETTE.border}" />
      <text x="${margin}" y="${height - 28}" fill="${SHARE_PALETTE.dim}" font-size="10.5">Goals: Win Rate ≥ 50% · Profit Factor ≥ 1.30 · Avg Win/Loss ≥ 1.20 · Green = profitable · Upcoming = future session</text>
      <text x="${width - margin}" y="${height - 28}" text-anchor="end" fill="${SHARE_PALETTE.dim}" font-size="10.5" font-weight="650">AI Journal</text>
    </svg>`,
  };
}

export function buildYearShareSvg({
  year,
  yearData,
  yearPnl,
  yearWinRate,
  yearProfitFactor,
  yearAvgWinLoss,
  totalTrades,
  tradingDays,
  profitableMonths,
  asOfDate,
}) {
  const width = 1200;
  const margin = 50;
  const inner = width - margin * 2;
  const metricGap = 10;
  const metricW = (inner - metricGap * 4) / 5;
  const insightGap = 10;
  const insightW = (inner - insightGap * 3) / 4;
  const gridY = 356;
  const cardGap = 14;
  const cardW = (inner - cardGap * 2) / 3;
  const cardH = 204;
  const height = gridY + cardH * 4 + cardGap * 3 + 78;
  const asOfCandidate = asOfDate instanceof Date ? asOfDate : new Date(asOfDate || Date.now());
  const asOf = Number.isNaN(asOfCandidate.getTime()) ? new Date() : asOfCandidate;
  const currentYear = asOf.getFullYear();
  const currentMonth = asOf.getMonth() + 1;
  const months = Array.from({ length: 12 }, (_, i) => yearData?.[i] || null);
  const active = months
    .map((data, i) => ({ data, i }))
    .filter(({ data }) => Boolean(data?.has_data));
  const losingMonths = active.filter(({ data }) => Number(data.net_pnl || 0) < 0).length;
  const best = active.reduce((winner, current) => (
    !winner || Number(current.data.net_pnl || 0) > Number(winner.data.net_pnl || 0) ? current : winner
  ), null);
  const profitableRate = active.length ? Math.round((profitableMonths / active.length) * 100) : 0;

  const yearWinTarget = targetLine(Number(yearWinRate), SHARE_BASELINES.winRate, '%');
  const yearPfTarget = targetLine(Number(yearProfitFactor), SHARE_BASELINES.profitFactor);
  const yearRatioTarget = targetLine(Number(yearAvgWinLoss), SHARE_BASELINES.avgWinLoss);

  const metrics = [
    ['YTD NET P&L', signedPnl(yearPnl), metricHelp.pnl, yearPnl > 0 ? 'pos' : yearPnl < 0 ? 'neg' : 'neutral', null],
    ['WIN RATE', yearWinRate === '--' ? '--' : `${yearWinRate}%`, metricHelp.win, 'neutral', yearWinTarget],
    ['PROFIT FACTOR', yearProfitFactor == null ? '∞' : Number(yearProfitFactor).toFixed(2), metricHelp.pf, 'neutral', yearPfTarget],
    ['AVG WIN / LOSS', yearAvgWinLoss, metricHelp.ratio, 'neutral', yearRatioTarget],
    ['TRADING DAYS', tradingDays.toLocaleString('en-US'), metricHelp.days, 'neutral', null],
  ].map((m, i) => shareMetricCard(margin + i * (metricW + metricGap), 154, metricW, ...m)).join('');

  const avgActiveMonth = active.length > 0 ? yearPnl / active.length : 0;
  const insights = [
    ['BEST MONTH', best ? fmtShareDayPnl(best.data.net_pnl) : '—', best ? MONTHS_SHORT[best.i] : 'No data', 'pos'],
    ['AVG / ACTIVE MONTH', active.length ? fmtShareDayPnl(avgActiveMonth) : '—', 'YTD P&L ÷ active months', avgActiveMonth > 0 ? 'pos' : 'neutral'],
    ['PROFITABLE RATE', active.length ? `${profitableRate}%` : '—', `${profitableMonths} green · ${losingMonths} red`, 'blue'],
    ['TOTAL TRADES', totalTrades.toLocaleString('en-US'), `${active.length} active months`, 'neutral'],
  ].map((m, i) => shareInsightCard(margin + i * (insightW + insightGap), 262, insightW, ...m)).join('');

  const cards = months.map((data, i) => {
    const x = margin + (i % 3) * (cardW + cardGap);
    const y = gridY + Math.floor(i / 3) * (cardH + cardGap);
    const isFuture = year > currentYear || (year === currentYear && i + 1 > currentMonth);
    const has = Boolean(data?.has_data);
    const pnl = Number(data?.net_pnl || 0);
    const tone = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : 'neutral';
    const fill = has
      ? (tone === 'pos' ? '#0d3028' : tone === 'neg' ? '#32171e' : SHARE_PALETTE.panel)
      : (isFuture ? '#081722' : '#091923');
    const accent = has
      ? (tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.border)
      : (isFuture ? '#24465b' : '#173246');
    const valueColor = has
      ? (tone === 'pos' ? SHARE_PALETTE.green : tone === 'neg' ? SHARE_PALETTE.red : SHARE_PALETTE.text)
      : SHARE_PALETTE.muted;
    const primary = has ? fmtShareDayPnl(pnl) : (isFuture ? 'UPCOMING' : 'NO TRADES');
    const win = has ? `${Number(data.win_rate || 0).toFixed(1)}%` : '—';
    const pf = has ? (data.profit_factor == null ? '∞' : Number(data.profit_factor).toFixed(2)) : '—';
    const activity = has
      ? `${data.total_trades} trades · ${data.trading_days} trading days`
      : (isFuture ? 'Future month' : 'No trading activity recorded');

    return `
      <g>
        <rect x="${x}" y="${y}" width="${cardW}" height="${cardH}" rx="15" fill="${fill}" stroke="${SHARE_PALETTE.border}" />
        <rect x="${x}" y="${y}" width="5" height="${cardH}" rx="2.5" fill="${accent}" />
        <text x="${x + 20}" y="${y + 31}" fill="${SHARE_PALETTE.muted}" font-size="12" font-weight="780" letter-spacing="1.1">${MONTHS[i].toUpperCase()}</text>
        <text class="tabular" x="${x + 20}" y="${y + 78}" fill="${valueColor}" font-size="${has ? 31 : 15}" font-weight="800" letter-spacing="${has ? '-.5' : '1'}">${escapeXml(primary)}</text>
        <line x1="${x + 20}" y1="${y + 102}" x2="${x + cardW - 20}" y2="${y + 102}" stroke="${SHARE_PALETTE.border}" />
        <text x="${x + 20}" y="${y + 129}" fill="${SHARE_PALETTE.dim}" font-size="10">WIN RATE</text>
        <text class="tabular" x="${x + 20}" y="${y + 153}" fill="${SHARE_PALETTE.text}" font-size="17" font-weight="750">${win}</text>
        <text x="${x + 142}" y="${y + 129}" fill="${SHARE_PALETTE.dim}" font-size="10">PROFIT FACTOR</text>
        <text class="tabular" x="${x + 142}" y="${y + 153}" fill="${SHARE_PALETTE.text}" font-size="17" font-weight="750">${pf}</text>
        <text x="${x + 20}" y="${y + 183}" fill="${SHARE_PALETTE.muted}" font-size="10.5">${escapeXml(activity)}</text>
      </g>`;
  }).join('');

  const yearlySummary = `${active.length} active month${active.length === 1 ? '' : 's'} · ${tradingDays} trading day${tradingDays === 1 ? '' : 's'} · ${totalTrades.toLocaleString('en-US')} total trades`;

  return {
    width,
    height,
    filename: `trading-calendar-${year}.png`,
    title: `${year} Trading Calendar`,
    svg: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <defs>
        <linearGradient id="yearShareBg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#071722" />
          <stop offset="58%" stop-color="${SHARE_PALETTE.bg}" />
          <stop offset="100%" stop-color="#041019" />
        </linearGradient>
      </defs>
      <style>${shareSvgStyle}</style>
      <rect width="100%" height="100%" fill="url(#yearShareBg)" />
      <rect x="${margin}" y="38" width="5" height="48" rx="2.5" fill="${SHARE_PALETTE.blue}" />
      <text x="${margin + 18}" y="50" fill="${SHARE_PALETTE.blue}" font-size="11.5" font-weight="780" letter-spacing="2">AI JOURNAL · YEARLY PERFORMANCE</text>
      <text x="${margin + 18}" y="94" fill="${SHARE_PALETTE.text}" font-size="43" font-weight="800" letter-spacing="-1">${year} Trading Year</text>
      <text x="${margin + 18}" y="123" fill="${SHARE_PALETTE.muted}" font-size="14.5" font-weight="500">${escapeXml(yearlySummary)}</text>
      <rect x="${width - 168}" y="46" width="118" height="30" rx="15" fill="${SHARE_PALETTE.panel2}" stroke="${SHARE_PALETTE.border}" />
      <text x="${width - 109}" y="66" text-anchor="middle" fill="${SHARE_PALETTE.muted}" font-size="10.5" font-weight="750" letter-spacing="1.2">YEAR SNAPSHOT</text>
      ${metrics}
      ${insights}
      <text x="${margin}" y="${gridY - 10}" fill="${SHARE_PALETTE.text}" font-size="12" font-weight="750" letter-spacing="1.2">MONTH-BY-MONTH PERFORMANCE</text>
      ${cards}
      <line x1="${margin}" y1="${height - 54}" x2="${width - margin}" y2="${height - 54}" stroke="${SHARE_PALETTE.border}" />
      <text x="${margin}" y="${height - 28}" fill="${SHARE_PALETTE.dim}" font-size="10.5">Goals: Win Rate ≥ 50% · Profit Factor ≥ 1.30 · Avg Win/Loss ≥ 1.20 · Green = profitable · Upcoming = future month</text>
      <text x="${width - margin}" y="${height - 28}" text-anchor="end" fill="${SHARE_PALETTE.dim}" font-size="10.5" font-weight="650">AI Journal</text>
    </svg>`,
  };
}

async function svgToPngBlob(svg, width, height) {
  if (
    typeof document === 'undefined'
    || typeof URL === 'undefined'
    || typeof URL.createObjectURL !== 'function'
    || typeof CanvasRenderingContext2D === 'undefined'
  ) return null;
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

function ShareMetric({ label, value, help, tone, target }) {
  return (
    <div className="calendar-share-stat">
      <span className="calendar-share-stat-label">{label}</span>
      <strong className={`num ${tone || ''}`}>{value}</strong>
      <small className={target ? `calendar-share-target ${target.tone || ''}` : ''}>
        {target?.text || help}
      </small>
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

function YearView({ year, setYear, accountId, onMonthClick, view, setView, shareMode, onShareModeChange }) {
  const [yearData, setYearData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [shareStatus, setShareStatus] = useState('');
  const today = new Date();
  const currentYear = today.getFullYear();
  const currentMonth = today.getMonth() + 1;

  useEffect(() => {
    setLoading(true);
    const params = { year };
    if (accountId != null) params.account_id = accountId;
    yearlyKpisApi.get(params)
      .then(r => {
        const months = Array.isArray(r.data) ? r.data : (Array.isArray(r.data?.months) ? r.data.months : []);
        setYearData(months);
        setLoading(false);
      })
      .catch(() => { setYearData([]); setLoading(false); });
  }, [year, accountId]);

  const monthsWithData = yearData.filter(m => m?.has_data);
  const yearPnl = monthsWithData.reduce((s, m) => s + Number(m.net_pnl || 0), 0);
  const totalTrades = monthsWithData.reduce((s, m) => s + Number(m.total_trades || 0), 0);
  const totalWinners = monthsWithData.reduce((s, m) => s + Number(m.winning_trades || 0), 0);
  const tradingDays = monthsWithData.reduce((s, m) => s + Number(m.trading_days || 0), 0);
  const profitableMonths = monthsWithData.filter(m => Number(m.net_pnl || 0) > 0).length;
  const yearWinRate = totalTrades > 0 ? (totalWinners / totalTrades * 100).toFixed(1) : '--';

  const exportYearImage = async () => {
    if (loading) return;
    setShareStatus('saving');
    const spec = buildYearShareSvg({
      year,
      yearData,
      yearPnl,
      yearWinRate,
      totalTrades,
      tradingDays,
      profitableMonths,
    });
    const result = await saveOrShareCalendarImage(spec);
    if (result === 'error' || result === 'unsupported') setShareStatus('error');
    else if (result === 'cancelled') setShareStatus('');
    else setShareStatus(result === 'shared' ? 'shared' : 'saved');
  };

  const startYearShare = () => {
    onShareModeChange(true);
    void exportYearImage();
  };

  const yearGrid = (
    <div className={shareMode ? 'calendar-year-share-grid' : 'calendar-year-grid'}>
      {loading ? (
        [...Array(12)].map((_, i) => <div key={i} className="skeleton" style={{ height: shareMode ? 96 : 136, borderRadius: 8 }} />)
      ) : (
        Array.from({ length: 12 }, (_, i) => {
          const mData = yearData[i];
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
        })
      )}
    </div>
  );

  return (
    <div className={`calendar-year-view${shareMode ? ' calendar-share-mode calendar-year-share-mode' : ''}`}>
      {shareMode ? (
        <>
          <div className="calendar-share-head calendar-share-head-detailed">
            <div className="calendar-share-title-block">
              <div className="calendar-share-eyebrow">Yearly Trading Performance</div>
              <h1><span className="num">{year}</span> Trading Year</h1>
              <p>See the big picture: total results, consistency, activity, and how each month contributed.</p>
            </div>
            <div className="calendar-share-head-actions">
              <button type="button" className="cal-nav" onClick={() => setYear(y => y - 1)} aria-label="Previous year"><ChevronLeft size={16} /></button>
              <button type="button" className="cal-nav" onClick={() => setYear(y => y + 1)} aria-label="Next year"><ChevronRight size={16} /></button>
              <button type="button" className="btn btn-primary btn-sm calendar-share-save" aria-label="Save Image" disabled={shareStatus === 'saving' || loading} onClick={exportYearImage}>
                <Download size={14} /> {shareStatus === 'saving' ? 'Creating…' : 'Save Image'}
              </button>
              <button type="button" className="btn btn-ghost btn-sm calendar-share-exit" onClick={() => onShareModeChange(false)}>
                <X size={14} /> Exit
              </button>
            </div>
          </div>

          <section className="calendar-share-stats calendar-share-stats-detailed" aria-label="Year summary">
            <ShareMetric label="YTD Net P&L" value={signedPnl(yearPnl)} help="Total profit or loss for the year" tone={yearPnl > 0 ? 'pos' : yearPnl < 0 ? 'neg' : ''} />
            <ShareMetric label="Win Rate" value={yearWinRate === '--' ? '--' : `${yearWinRate}%`} help="Percent of closed trades that won" />
            <ShareMetric label="Total Trades" value={totalTrades.toLocaleString('en-US')} help="Closed trades recorded this year" />
            <ShareMetric label="Trading Days" value={tradingDays.toLocaleString('en-US')} help="Days with at least one trade" />
            <ShareMetric label="Profitable Months" value={`${profitableMonths}/12`} help="Months that finished net positive" />
          </section>

          {shareStatus === 'saved' && <div className="calendar-share-status" role="status">PNG saved to your device.</div>}
          {shareStatus === 'shared' && <div className="calendar-share-status" role="status">Image opened in your share sheet.</div>}
          {shareStatus === 'error' && <div className="calendar-share-status neg" role="alert">Could not create the image on this browser.</div>}

          {yearGrid}
        </>
      ) : (
        <>
          <PageHeader
            title={<><span className="num">{year}</span> Trading Year</>}
            subtitle="Your 12-month performance overview. Tap a month to open its daily calendar."
            actions={<>
              <button type="button" className="cal-nav" onClick={() => setYear(y => y - 1)} aria-label="Previous year"><ChevronLeft size={18} /></button>
              <button type="button" className="cal-nav" onClick={() => setYear(y => y + 1)} aria-label="Next year"><ChevronRight size={18} /></button>
              <button type="button" className="btn btn-secondary" onClick={() => setYear(currentYear)}>This year</button>
              <ViewToggle view={view} setView={setView} />
              <button type="button" className="btn btn-ghost calendar-share-trigger" disabled={loading} onClick={startYearShare}>
                <Share2 size={15} /> Share Image
              </button>
            </>}
          />

          {monthsWithData.length > 0 && (
            <KpiStrip label="Year summary">
              <KpiCell label="YTD Net P&L" value={<span className="num">{signedPnl(yearPnl)}</span>} tone={yearPnl > 0 ? 'pos' : yearPnl < 0 ? 'neg' : undefined} />
              <KpiCell label="Win Rate" value={<span className="num">{yearWinRate !== '--' ? `${yearWinRate}%` : '--'}</span>} tone={Number(yearWinRate) >= 55 ? 'pos' : undefined} />
              <KpiCell label="Total Trades" value={<span className="num">{totalTrades.toLocaleString('en-US')}</span>} />
              <KpiCell label="Trading Days" value={<span className="num">{tradingDays.toLocaleString('en-US')}</span>} />
              <KpiCell label="Profitable Months" value={<span className="num">{profitableMonths}/12</span>} />
            </KpiStrip>
          )}

          {yearGrid}
        </>
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
  const [shareStatus, setShareStatus] = useState('');

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

  const exportMonthImage = async () => {
    if (loading) return;
    setShareStatus('saving');
    const spec = buildMonthShareSvg({
      year,
      month,
      weeks,
      dayData,
      monthPnl,
      winRate,
      profitFactor: monthKpis ? monthKpis.profit_factor : undefined,
      avgWinLoss,
      tradingDays,
    });
    const result = await saveOrShareCalendarImage(spec);
    if (result === 'error' || result === 'unsupported') setShareStatus('error');
    else if (result === 'cancelled') setShareStatus('');
    else setShareStatus(result === 'shared' ? 'shared' : 'saved');
  };

  const startMonthShare = () => {
    onShareModeChange(true);
    void exportMonthImage();
  };

  return (
    <div className={`calendar-month-view${shareMode ? ' calendar-share-mode' : ''}`}>
      {shareMode ? (
        <>
          <div className="calendar-share-head calendar-share-head-detailed">
            <div className="calendar-share-title-block">
              <div className="calendar-share-eyebrow">Monthly Trading Performance</div>
              <h1>{MONTHS[month - 1]} <span className="num">{year}</span></h1>
              <p>{tradingDays} trading day{tradingDays === 1 ? '' : 's'} · {winRate === '--' ? 'No closed trades yet' : `${winRate}% win rate`} · <strong className={monthPnl > 0 ? 'pos' : monthPnl < 0 ? 'neg' : ''}>{signedPnl(monthPnl)} net P&amp;L</strong></p>
            </div>
            <div className="calendar-share-head-actions">
              <button type="button" className="cal-nav" onClick={prevMonth} aria-label="Previous month"><ChevronLeft size={16} /></button>
              <button type="button" className="cal-nav" onClick={nextMonth} aria-label="Next month"><ChevronRight size={16} /></button>
              <button type="button" className="btn btn-primary btn-sm calendar-share-save" aria-label="Save Image" disabled={shareStatus === 'saving' || loading} onClick={exportMonthImage}>
                <Download size={14} /> {shareStatus === 'saving' ? 'Creating…' : 'Save Image'}
              </button>
              <button type="button" className="btn btn-ghost btn-sm calendar-share-exit" onClick={() => onShareModeChange(false)}>
                <X size={14} /> Exit
              </button>
            </div>
          </div>

          <section className="calendar-share-stats calendar-share-stats-detailed" aria-label="Month summary">
            <ShareMetric label="Net P&L" value={signedPnl(monthPnl)} help="Total profit or loss for this month" tone={monthPnl > 0 ? 'pos' : monthPnl < 0 ? 'neg' : ''} />
            <ShareMetric label="Win Rate" value={winRate === '--' ? '--' : `${winRate}%`} help="Percent of closed trades that won" />
            <ShareMetric label="Profit Factor" value={monthKpis ? (pf == null ? '∞' : Number(pf).toFixed(2)) : '--'} help="Gross profit ÷ gross loss" />
            <ShareMetric label="Avg Win / Loss" value={avgWinLoss} help="Average winner compared with loser" />
            <ShareMetric label="Trading Days" value={tradingDays} help="Days with at least one trade" />
          </section>

          {shareStatus === 'saved' && <div className="calendar-share-status" role="status">PNG saved to your device.</div>}
          {shareStatus === 'shared' && <div className="calendar-share-status" role="status">Image opened in your share sheet.</div>}
          {shareStatus === 'error' && <div className="calendar-share-status neg" role="alert">Could not create the image on this browser.</div>}
        </>
      ) : (
        <>
          <PageHeader
            title={<>{MONTHS[month - 1]} <span className="num">{year}</span></>}
            subtitle="Daily P&L calendar. Tap any traded day to open its Day Review."
            actions={<>
              <button type="button" className="cal-nav" onClick={prevMonth} aria-label="Previous month"><ChevronLeft size={18} /></button>
              <button type="button" className="cal-nav" onClick={nextMonth} aria-label="Next month"><ChevronRight size={18} /></button>
              <button type="button" className="btn btn-secondary" onClick={goToday}>This month</button>
              <ViewToggle view={view} setView={setView} />
              <button type="button" className="btn btn-ghost calendar-share-trigger" disabled={loading} onClick={startMonthShare}>
                <Share2 size={15} /> Share Image
              </button>
            </>}
          />

          <KpiStrip label="Month summary">
            <KpiCell label="Net P&L" value={<span className="num">{signedPnl(monthPnl)}</span>} tone={monthPnl > 0 ? 'pos' : monthPnl < 0 ? 'neg' : undefined} />
            <KpiCell
              label="Win Rate"
              value={<span className="num">{winRate === '--' ? '--' : `${winRate}%`}</span>}
              tone={monthKpis && monthKpis.win_rate >= 55 ? 'pos' : undefined}
            />
            <KpiCell
              label="Profit Factor"
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

  return (
    <div className={`calendar-page${shareMode ? ' calendar-page-share' : ''}`}>
      {view === 'year' ? (
        <YearView
          year={year}
          setYear={setYear}
          accountId={accountId}
          onMonthClick={switchToMonth}
          view={view}
          setView={setView}
          shareMode={shareMode}
          onShareModeChange={onShareModeChange}
        />
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
