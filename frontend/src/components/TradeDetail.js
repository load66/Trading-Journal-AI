import { useState, useEffect, useCallback, useRef } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, PlusCircle, Trash2, Pencil, Sparkles, Target, AlertTriangle, CheckCircle2, Upload, Maximize2 } from 'lucide-react';
import { tradesApi } from '../api';
import TradingChart from './TradingChart';
import LEReview from './LEReview';
import { PageHeader, KpiStrip, KpiCell, MoneyValue, PanelHead } from './ui';

const fmt$ = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  return (n >= 0 ? '' : '-') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const fmtSigned$ = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const SCREENSHOT_MAX_DIMENSION = 1200;
const SCREENSHOT_TARGET_BYTES = 500 * 1024;

function canvasBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error('Could not compress screenshot.'));
    }, type, quality);
  });
}

async function loadScreenshotImage(file) {
  if (typeof createImageBitmap === 'function') {
    const bitmap = await createImageBitmap(file);
    return {
      source: bitmap,
      width: bitmap.width,
      height: bitmap.height,
      close: () => bitmap.close?.(),
    };
  }

  const url = URL.createObjectURL(file);
  const image = new Image();
  image.decoding = 'async';
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = () => reject(new Error('Could not read screenshot image.'));
    image.src = url;
  });
  URL.revokeObjectURL(url);
  return { source: image, width: image.naturalWidth, height: image.naturalHeight, close: () => {} };
}

export async function optimizeChartScreenshot(file) {
  if (!file || !String(file.type || '').startsWith('image/')) {
    throw new Error('Paste or choose an image file.');
  }
  if (file.type === 'image/webp' && file.size <= SCREENSHOT_TARGET_BYTES) return file;

  const image = await loadScreenshotImage(file);
  try {
    const originalLongEdge = Math.max(image.width, image.height);
    const dimensionSteps = [SCREENSHOT_MAX_DIMENSION, 1000, 800]
      .map(maxDimension => Math.min(maxDimension, originalLongEdge))
      .filter((value, index, arr) => value > 0 && arr.indexOf(value) === index);
    const qualitySteps = [0.78, 0.66, 0.54];

    let bestBlob = null;
    for (const maxDimension of dimensionSteps) {
      const scale = Math.min(1, maxDimension / originalLongEdge);
      const width = Math.max(1, Math.round(image.width * scale));
      const height = Math.max(1, Math.round(image.height * scale));
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Screenshot compression is unavailable in this browser.');
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(image.source, 0, 0, width, height);

      for (const quality of qualitySteps) {
        const blob = await canvasBlob(canvas, 'image/webp', quality);
        if (!bestBlob || blob.size < bestBlob.size) bestBlob = blob;
        if (blob.size <= SCREENSHOT_TARGET_BYTES) {
          return new File([blob], 'trade-review.webp', { type: 'image/webp', lastModified: Date.now() });
        }
      }
    }

    if (!bestBlob) throw new Error('Could not compress screenshot.');
    return new File([bestBlob], 'trade-review.webp', { type: 'image/webp', lastModified: Date.now() });
  } finally {
    image.close();
  }
}

function parseExecs(trade) {
  const raw = trade.executions;
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  try { return JSON.parse(raw); } catch { return []; }
}

function computeStats(trade) {
  const execs = parseExecs(trade);
  const side = trade.side;
  const entryFills = execs.filter(e => side === 'LONG' ? e.action === 'BOT' : e.action === 'SOLD');
  const exitFills  = execs.filter(e => side === 'LONG' ? e.action === 'SOLD' : e.action === 'BOT');

  const avgPrice = (fills) => {
    const qty = fills.reduce((s, f) => s + (f.qty || 0), 0);
    if (!qty) return null;
    return fills.reduce((s, f) => s + (f.qty || 0) * (f.price || 0), 0) / qty;
  };

  const avgEntry = avgPrice(entryFills);
  const avgExit  = avgPrice(exitFills);
  const totalQty = entryFills.reduce((s, f) => s + (f.qty || 0), 0);
  const instrument = (trade.instrument_type || 'STOCK').toUpperCase();
  const multiplier = instrument === 'OPTION' ? 100 : 1;
  const adjustedCost = avgEntry ? avgEntry * totalQty * multiplier : null;
  const plPercent = trade.pl_pct != null
    ? Number(trade.pl_pct)
    : adjustedCost ? (trade.net_pnl / adjustedCost * 100) : null;

  const sortedTimes = [...execs].map(e => e.time).filter(Boolean).sort();
  const openTime  = sortedTimes[0];
  const closeTime = sortedTimes[sortedTimes.length - 1];

  let holdMinutes = null;
  if (openTime && closeTime && exitFills.length > 0) {
    const [oh, om] = openTime.split(':').map(Number);
    const [ch, cm] = closeTime.split(':').map(Number);
    holdMinutes = (ch * 60 + cm) - (oh * 60 + om);
  }

  const fmtHold = (m) => {
    if (m == null) return '—';
    if (m < 60) return `${m}m`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  };

  const isClosed = exitFills.length > 0;
  const isWin = (trade.net_pnl || 0) > 0;

  return { avgEntry, avgExit, totalQty, adjustedCost, plPercent, openTime, closeTime, holdMinutes, fmtHold, isClosed, isWin, entryFills, exitFills };
}

export function calculateDefaultPlannedRisk(trade) {
  if (!trade) return null;
  const instrument = String(trade.instrument_type || '').toUpperCase();
  const side = String(trade.side || '').toUpperCase();

  // Long options have a mechanically knowable maximum loss: premium paid.
  // Do not guess short-option, stock, or futures risk without an explicit
  // premium/price stop because their actual loss can differ materially.
  if (instrument !== 'OPTION' || side !== 'LONG') return null;

  const { adjustedCost } = computeStats(trade);
  if (!Number.isFinite(adjustedCost) || adjustedCost <= 0) return null;
  return Math.round(adjustedCost * 100) / 100;
}

// ── Stat row helper ────────────────────────────────────────────────────────────

function StatRow({ label, value, valueColor }) {
  if (value == null || value === '—' || value === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, padding: '9px 0', borderBottom: '1px solid var(--divider-soft)' }}>
      <span style={{ color: 'var(--text-secondary)', fontSize: 14 }}>{label}</span>
      <span className="num" style={{ fontSize: 14, fontWeight: 500, color: valueColor || 'var(--text-primary)', textAlign: 'right' }}>{value}</span>
    </div>
  );
}

// ── Shared edit-field helpers ──────────────────────────────────────────────────

const inputStyle = {
  width: '100%', fontSize: 14, minHeight: 34, padding: '5px 9px', boxSizing: 'border-box',
};

function EditField({ label, value, onChange, type = 'text', options, inputRef, autoFocus = false }) {
  return (
    <label style={{ display: 'block' }}>
      <span className="field-label" style={{ marginBottom: 4 }}>{label}</span>
      {options ? (
        <select ref={inputRef} autoFocus={autoFocus} value={value} onChange={e => onChange(e.target.value)} style={inputStyle}>
          <option value="">—</option>
          {options.map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      ) : (
        <input ref={inputRef} autoFocus={autoFocus} type={type} value={value} onChange={e => onChange(e.target.value)} style={inputStyle} />
      )}
    </label>
  );
}

function EditTextarea({ label, value, onChange }) {
  return (
    <label style={{ display: 'block' }}>
      <span className="field-label" style={{ marginBottom: 4 }}>{label}</span>
      <textarea value={value} onChange={e => onChange(e.target.value)} rows={3}
        style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit' }} />
    </label>
  );
}

function QuickPickGroup({ title, subtitle, suggestions, value, onToggle, tone = 'accent' }) {
  return (
    <section className={`trade-review-pick-group ${tone}`} aria-label={title}>
      <div className="trade-review-pick-head">
        <div>
          <div className="trade-review-pick-title">{title}</div>
          <div className="trade-review-pick-subtitle">{subtitle}</div>
        </div>
      </div>
      <div className="trade-review-chip-row">
        {suggestions.map(item => {
          const active = hasSuggestedPhrase(value, item.text);
          return (
            <button
              key={item.label}
              type="button"
              className={`trade-review-chip${active ? ' active' : ''}`}
              aria-pressed={active}
              onClick={() => onToggle(item)}
              title={item.text}
            >
              {active && <CheckCircle2 size={13} />}
              {item.label}
            </button>
          );
        })}
      </div>
    </section>
  );
}

function TradeReviewSummary({ trade, entryReason, exitReason, mistakes }) {
  const completion = reviewCompletion(entryReason, exitReason, mistakes);
  const selectedMistakes = selectedSuggestions(mistakes, MISTAKE_REVIEW_SUGGESTIONS);
  const primary = selectedMistakes[0] || null;
  const pnl = Number(trade?.net_pnl || 0);

  const fallbackFocus = completion < 3
    ? 'Complete the entry, exit, and mistake review so this trade can contribute to your pattern analysis.'
    : pnl < 0
      ? 'Convert the loss into one specific rule you can recognize and execute earlier next time.'
      : 'Confirm that the profitable outcome came from repeatable process rather than outcome alone.';

  return (
    <section className="trade-review-summary" aria-label="Trade review summary">
      <div className="trade-review-summary-head">
        <div className="trade-review-summary-icon"><Target size={19} /></div>
        <div>
          <div className="trade-review-summary-title">Review summary</div>
          <div className="trade-review-summary-subtitle">{completion}/3 review areas documented</div>
        </div>
        <div className={`trade-review-completion c${completion}`}>
          {completion === 3 ? 'Complete' : 'In progress'}
        </div>
      </div>

      <div className="trade-review-summary-grid">
        <div className="trade-review-summary-cell">
          <span>Primary improvement</span>
          <strong className={primary ? 'neg' : ''}>
            {primary ? primary.label : completion === 3 ? 'No tagged rule violation' : 'Finish the review'}
          </strong>
          {primary && <small>{primary.category} execution</small>}
        </div>
        <div className="trade-review-summary-cell focus">
          <span>Next-trade rule</span>
          <strong>{primary?.correction || fallbackFocus}</strong>
        </div>
      </div>

      {selectedMistakes.length > 1 && (
        <div className="trade-review-patterns">
          <span>Also flagged</span>
          {selectedMistakes.slice(1, 4).map(item => (
            <b key={item.label}>{item.label}</b>
          ))}
        </div>
      )}
    </section>
  );
}

const EMOTIONAL_STATES = ['Focused', 'Confident', 'Calm', 'Anxious', 'FOMO', 'Frustrated', 'Greedy', 'Fearful', 'Undisciplined', 'Overconfident'];
const DEFAULT_SOURCES   = ['Watchlist', 'Scanner', 'Alert', 'News', 'Social Media', 'Own Research'];

const TAG_TYPES = ['strategy', 'setup', 'execution', 'mistake', 'emotion', 'outcome', 'source'];

const ENTRY_REVIEW_SUGGESTIONS = [
  { label: 'Key level breakout', text: 'Entered on a confirmed break of a key level with momentum and follow-through.' },
  { label: 'PDH / PMH breakout', text: 'Entered on a confirmed break of the prior-day or premarket high.' },
  { label: 'Break + retest', text: 'Entered after the breakout level held on a retest.' },
  { label: 'VWAP reclaim', text: 'Entered after VWAP was reclaimed and held with confirmation.' },
  { label: '8 EMA pullback · 10m', text: 'Entered on a controlled pullback into the 8 EMA on the 10-minute timeframe.' },
  { label: 'Opening range breakout', text: 'Entered on an opening-range breakout with confirmation.' },
  { label: 'Trend continuation', text: 'Entered with the established intraday trend after consolidation.' },
  { label: 'Reversal at key level', text: 'Entered on a confirmed reversal from a defined support or resistance level.' },
  { label: 'Waited for confirmation', text: 'Waited for confirmation before entering instead of anticipating the move.' },
];

const EXIT_REVIEW_SUGGESTIONS = [
  { label: 'HOD trim + 8 EMA trail', text: 'Trimmed into the high of day, then trailed the remainder using the 8 EMA on the 10-minute timeframe.' },
  { label: 'Key-level trim + trail', text: 'Trimmed at the next key level, then trailed the remaining position.' },
  { label: 'Partial at 1R + trail', text: 'Took a partial at 1R, then managed the remainder with a trailing stop.' },
  { label: 'Technical invalidation', text: 'Exited when the original technical thesis was invalidated.' },
  { label: 'Lost 8 EMA · 10m', text: 'Exited after price lost the 8 EMA on the 10-minute timeframe and failed to reclaim it.' },
  { label: 'Lost VWAP', text: 'Exited after VWAP was lost and the reclaim failed.' },
  { label: 'Momentum stalled', text: 'Exited when momentum stalled and follow-through failed to develop.' },
  { label: 'Target reached', text: 'Exited into the planned target area.' },
  { label: 'Time-based exit', text: 'Exited because the trade failed to progress within the planned time window.' },
];

const MISTAKE_REVIEW_SUGGESTIONS = [
  { label: 'Entered before confirmation', category: 'Entry', text: 'Entered before confirmation and anticipated the setup.', correction: 'Wait for the setup to confirm before committing risk.' },
  { label: 'Chased extended move', category: 'Entry', text: 'Chased an extended move instead of waiting for a cleaner entry.', correction: 'Wait for a pullback, retest, or fresh base instead of chasing extension.' },
  { label: 'Entered into key level', category: 'Entry', text: 'Entered too close to opposing support or resistance.', correction: 'Require enough room to the next key level before entering.' },
  { label: 'No volume confirmation', category: 'Entry', text: 'Entered without sufficient volume or momentum confirmation.', correction: 'Require volume and momentum confirmation before entry.' },
  { label: 'Poor risk/reward', category: 'Entry', text: 'Accepted a trade with poor reward relative to the planned risk.', correction: 'Skip trades that do not offer enough reward to the next realistic target.' },
  { label: 'Oversized position', category: 'Risk', text: 'Position size was too large for the setup quality or stop distance.', correction: 'Size from the invalidation level and planned dollar risk before entry.' },
  { label: 'Added to loser', category: 'Risk', text: 'Added to a losing position after the original entry was already under pressure.', correction: 'Do not add risk after the original setup begins failing unless a separate planned add condition is met.' },
  { label: 'Moved / ignored stop', category: 'Risk', text: 'Moved or ignored the planned stop after the trade invalidated.', correction: 'Honor the predefined invalidation without widening risk after entry.' },
  { label: 'Held loser too long', category: 'Exit', text: 'Held a losing trade too long after the setup stopped working.', correction: 'Exit sooner when favorable progress fails and technical invalidation begins.' },
  { label: 'Cut winner too early', category: 'Exit', text: 'Exited a winning trade too early before the planned management signal triggered.', correction: 'Let the planned trailing rule manage the remainder instead of exiting from noise.' },
  { label: 'Skipped partials', category: 'Exit', text: 'Failed to take planned partial profits into strength.', correction: 'Use the planned partial level and trail the remaining position mechanically.' },
  { label: 'No exit plan', category: 'Exit', text: 'Entered without a clearly defined profit-taking and invalidation plan.', correction: 'Define the initial stop, first trim, and trailing rule before entering.' },
  { label: 'FOMO entry', category: 'Discipline', text: 'Entered because of FOMO instead of waiting for the planned setup.', correction: 'If the planned entry is missed, wait for a new setup instead of chasing.' },
  { label: 'Revenge trade', category: 'Discipline', text: 'Took the trade to recover a prior loss rather than because the setup qualified.', correction: 'Reset after a loss and require the full checklist before the next trade.' },
  { label: 'Overtraded', category: 'Discipline', text: 'Took an extra trade that did not meet the normal quality threshold.', correction: 'Respect the daily trade limit and only take qualified setups.' },
];

function phraseLine(text) {
  return `• ${text}`;
}

function hasSuggestedPhrase(value, text) {
  const lines = String(value || '').split('\n').map(line => line.trim());
  return lines.includes(text) || lines.includes(phraseLine(text));
}

function toggleSuggestedPhrase(value, text) {
  const bullet = phraseLine(text);
  const lines = String(value || '').split('\n').map(line => line.trim()).filter(Boolean);
  const exists = lines.some(line => line === text || line === bullet);
  const next = exists
    ? lines.filter(line => line !== text && line !== bullet)
    : [...lines, bullet];
  return next.join('\n');
}

function selectedSuggestions(value, suggestions) {
  return suggestions.filter(item => hasSuggestedPhrase(value, item.text));
}

function reviewCompletion(entryReason, exitReason, mistakes) {
  return [entryReason, exitReason, mistakes].filter(value => String(value || '').trim()).length;
}


// ── Dropdown with add-new option ──────────────────────────────────────────────

function SelectWithAdd({ value, onChange, options, placeholder = 'Select', label }) {
  const [adding, setAdding] = useState(false);
  const [newVal, setNewVal] = useState('');

  // Always include current value even if not in options list yet
  const merged = value && !options.includes(value) ? [value, ...options] : options;

  const handleAdd = () => {
    const trimmed = newVal.trim();
    if (!trimmed) return;
    onChange(trimmed);
    setAdding(false);
    setNewVal('');
  };

  if (adding) {
    return (
      <div style={{ display: 'flex', gap: 4 }}>
        <input
          autoFocus
          type="text"
          value={newVal}
          onChange={e => setNewVal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') handleAdd(); if (e.key === 'Escape') { setAdding(false); setNewVal(''); } }}
          placeholder="Type new value…"
          aria-label={label ? `New ${label.toLowerCase()}` : 'New value'}
          style={{ ...inputStyle, flex: 1 }}
        />
        <button type="button" onClick={handleAdd} className="btn btn-primary btn-sm">Add</button>
        <button type="button" onClick={() => { setAdding(false); setNewVal(''); }} className="btn btn-ghost btn-sm" aria-label="Cancel new value">✕</button>
      </div>
    );
  }

  return (
    <select
      aria-label={label}
      value={value || ''}
      onChange={e => e.target.value === '__add__' ? setAdding(true) : onChange(e.target.value)}
      style={inputStyle}
    >
      <option value="">{placeholder}</option>
      {merged.map(o => <option key={o} value={o}>{o}</option>)}
      <option value="__add__">+ Add new…</option>
    </select>
  );
}

const editPanelStyle = { marginTop: 12, padding: 14, background: 'var(--surface-inset)', borderRadius: 'var(--radius-md)', border: '1px solid var(--divider)' };
const editGridStyle  = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 10, marginBottom: 10 };

// ── Tag badge ─────────────────────────────────────────────────────────────────

// Tag categories map onto the semantic palette (same mapping as Trade View).
const TAG_CLASS = {
  strategy: 'accent', setup: '', execution: 'pos',
  mistake: 'neg', emotion: 'caution', outcome: 'accent', source: '',
};

function TagBadge({ tag, onDelete }) {
  return (
    <span className={`chip ${TAG_CLASS[tag.tag_type] || ''}`} title={tag.tag_type} style={{ fontSize: 13, padding: onDelete ? '2px 4px 2px 10px' : '3px 10px' }}>
      {tag.tag_value}
      {onDelete && (
        <button type="button" onClick={onDelete} aria-label={`Remove tag ${tag.tag_value}`} title="Remove"
          style={{ background: 'none', border: 'none', color: 'inherit', opacity: 0.75, padding: '0 4px', lineHeight: 1, fontSize: 16, display: 'flex', alignItems: 'center' }}>
          ×
        </button>
      )}
    </span>
  );
}

// ── What If helpers ───────────────────────────────────────────────────────────

const BROKER_EXECUTION_TIME_ZONE = 'America/Chicago';
const DISPLAY_TIME_ZONE = 'America/New_York';

export function zonedWallTimeToDate(dateStr, timeStr, timeZone = BROKER_EXECUTION_TIME_ZONE) {
  if (!dateStr || !timeStr) return null;
  const [year, month, day] = dateStr.split('-').map(Number);
  const [hour, minute, second = 0] = timeStr.split(':').map(Number);
  if (![year, month, day, hour, minute, second].every(Number.isFinite)) return null;

  const wallUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
  let utcMs = wallUtcMs;
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hourCycle: 'h23',
  });

  // Two passes resolve the zone offset without assuming CST/CDT.
  for (let i = 0; i < 2; i += 1) {
    const parts = Object.fromEntries(
      formatter.formatToParts(new Date(utcMs))
        .filter(p => p.type !== 'literal')
        .map(p => [p.type, p.value])
    );
    const renderedAsUtc = Date.UTC(
      Number(parts.year), Number(parts.month) - 1, Number(parts.day),
      Number(parts.hour), Number(parts.minute), Number(parts.second)
    );
    utcMs += wallUtcMs - renderedAsUtc;
  }
  return new Date(utcMs);
}

function etPartsForExecution(dateStr, timeStr) {
  const instant = zonedWallTimeToDate(dateStr, timeStr);
  if (!instant) return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone: DISPLAY_TIME_ZONE,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(instant)
      .filter(p => p.type !== 'literal')
      .map(p => [p.type, p.value])
  );
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    hour: Number(parts.hour),
    minute: Number(parts.minute),
    hhmm: `${parts.hour}:${parts.minute}`,
  };
}

export function formatExecutionTimeET(dateStr, timeStr) {
  const parts = etPartsForExecution(dateStr, timeStr);
  return parts ? `${parts.hhmm} ET` : '—';
}

export function executionTimeETMinutes(dateStr, timeStr) {
  const parts = etPartsForExecution(dateStr, timeStr);
  return parts ? parts.hour * 60 + parts.minute : null;
}


const TABS = ['Stats', 'Review', 'Tags', 'LE Review', 'Executions', 'Chart Review'];
const TRADE_DETAIL_TAB_KEY = 'trading-journal:trade-detail-tab';

const EMPTY_EXEC = { action: 'BOT', qty: '', price: '0.00', commission: '0.00', date: '', time: '' };

// ── Main TradeDetail component ────────────────────────────────────────────────

function getDayTradeTime(t, which) {
  const execs = Array.isArray(t.executions) ? t.executions : [];
  const times = execs.map(e => e.time).filter(Boolean).sort();
  const raw = which === 'open' ? times[0] : times[times.length - 1];
  return raw ? formatExecutionTimeET(t.date, raw) : null;
}

function DaySidebar({ currentTrade, onOpenDetail }) {
  const [dayTrades, setDayTrades] = useState([]);

  useEffect(() => {
    tradesApi.list({ date_from: currentTrade.date, date_to: currentTrade.date, account_id: currentTrade.account_id })
      .then(r => setDayTrades(r.data))
      .catch(() => {});
  }, [currentTrade.date, currentTrade.account_id]);

  const dayPnl = dayTrades.reduce((s, t) => s + (t.net_pnl || 0), 0);

  return (
    <section className="card panel-flush" aria-label="This session">
      <div style={{ padding: '16px 16px 12px', borderBottom: '1px solid var(--divider-soft)' }}>
        <h2 className="section-title" style={{ fontSize: 17 }}>This session</h2>
        <div className="num text-muted" style={{ fontSize: 13, marginTop: 2 }}>{currentTrade.date}</div>
      </div>
      <div style={{ overflowY: 'auto', maxHeight: 560 }}>
        {dayTrades.map(t => {
          const pnl = t.net_pnl ?? 0;
          const isActive = t.id === currentTrade.id;
          const openT = getDayTradeTime(t, 'open');
          const closeT = getDayTradeTime(t, 'close');
          return (
            <button
              type="button"
              key={t.id}
              className={`list-row${isActive ? ' active' : ''}`}
              aria-current={isActive ? 'true' : undefined}
              onClick={() => onOpenDetail && onOpenDetail(t)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 15 }}>{t.ticker}</span>
                <span className={`num ${pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : ''}`} style={{ fontSize: 14, fontWeight: 600 }}>
                  {pnl >= 0 ? '+' : '-'}${Math.abs(pnl).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
                </span>
              </div>
              {(openT || closeT) && (
                <div className="num text-muted" style={{ fontSize: 12.5, marginTop: 2 }}>
                  {openT}{closeT && openT !== closeT ? ` to ${closeT}` : ''}
                  {isActive && <span className="text-purple" style={{ marginLeft: 6 }}>Selected</span>}
                </div>
              )}
            </button>
          );
        })}
      </div>
      <div style={{ padding: '14px 16px', borderTop: '1px solid var(--divider-soft)' }}>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Day net P&L · <span className="num">{dayTrades.length}</span> trades</div>
        <div className={`num ${dayPnl >= 0 ? 'pos' : 'neg'}`} style={{ fontSize: 22, fontWeight: 600, fontFamily: 'var(--font-display)', lineHeight: 1.2, marginTop: 2 }}>
          {dayPnl >= 0 ? '+' : '-'}${Math.abs(dayPnl).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </div>
      </div>
    </section>
  );
}

export default function TradeDetail({ trade: initialTrade, tradeNavList = [], onBack, onTradeUpdate, onNavigate, onOpenDetail, focusPlannedRisk = false }) {
  const [trade, setTrade] = useState(initialTrade);
  const [tab, setTab] = useState(() => {
    try {
      const saved = sessionStorage.getItem(TRADE_DETAIL_TAB_KEY);
      return TABS.includes(saved) ? saved : 'Stats';
    } catch {
      return 'Stats';
    }
  });
  const [analysis, setAnalysis] = useState(null);
  const [tags, setTags] = useState([]);

  useEffect(() => {
    try {
      sessionStorage.setItem(TRADE_DETAIL_TAB_KEY, tab);
    } catch {
      // Preserve normal review behavior if browser storage is unavailable.
    }
  }, [tab]);

  // Executions
  const [showAddExec, setShowAddExec]     = useState(false);
  const [execForm, setExecForm]           = useState(EMPTY_EXEC);
  const [addingExec, setAddingExec]       = useState(false);
  const [execError, setExecError]         = useState(null);
  const [editingExecIdx, setEditingExecIdx] = useState(null);
  const [editExecForm, setEditExecForm]   = useState(null);
  const [savingEditExec, setSavingEditExec] = useState(false);

  // Visual chart review screenshot
  const [chartScreenshotUrl, setChartScreenshotUrl] = useState('');
  const [chartScreenshotLoading, setChartScreenshotLoading] = useState(false);
  const [chartScreenshotUploading, setChartScreenshotUploading] = useState(false);
  const [chartScreenshotError, setChartScreenshotError] = useState(null);
  const [chartScreenshotExpanded, setChartScreenshotExpanded] = useState(false);
  const [chartScreenshotRevision, setChartScreenshotRevision] = useState(0);

  useEffect(() => {
    if (!chartScreenshotExpanded) return undefined;

    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setChartScreenshotExpanded(false);
    };

    document.body.style.overflow = 'hidden';
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [chartScreenshotExpanded]);

  // Stats edit
  const [editingStats, setEditingStats]   = useState(false);
  const [statsForm, setStatsForm]         = useState({});
  const [savingStats, setSavingStats]     = useState(false);
  const rrPlanInputRef = useRef(null);
  const plannedRiskFocusHandled = useRef(false);

  // Strategy edit
  const [editingStrategy, setEditingStrategy] = useState(false);
  const [strategyForm, setStrategyForm]       = useState({});
  const [savingStrategy, setSavingStrategy]   = useState(false);

  // Tags
  const [addingTag, setAddingTag]   = useState(false);
  const [tagForm, setTagForm]       = useState({ tag_type: 'strategy', tag_value: '' });
  const [tagError, setTagError]     = useState(null);
  const [savingTag, setSavingTag]   = useState(false);

  // Dropdown options (fetched from DB)
  const [analysisOptions, setAnalysisOptions] = useState({ strategies: [], idea_sources: [] });

  useEffect(() => {
    tradesApi.getAnalysisOptions().then(r => setAnalysisOptions(r.data)).catch(() => {});
  }, []);

  useEffect(() => {
    tradesApi.getAnalysis(trade.trade_group).then(r => {
      setAnalysis(r.data.analysis || {});
      setTags(r.data.tags || []);
    }).catch(() => setAnalysis({}));
  }, [trade.trade_group]);

  useEffect(() => {
    plannedRiskFocusHandled.current = false;
  }, [trade.trade_group, focusPlannedRisk]);

  useEffect(() => {
    if (!focusPlannedRisk || analysis == null || plannedRiskFocusHandled.current) return;
    plannedRiskFocusHandled.current = true;
    setTab('Stats');
    setStatsForm({
      strategy: analysis?.strategy || '',
      idea_source: analysis?.idea_source || 'Watchlist',
      stop_loss: analysis?.stop_loss ?? '',
      risk_per_trade: analysis?.risk_per_trade ?? calculateDefaultPlannedRisk(trade) ?? '',
      target_price: analysis?.target_price ?? '',
      emotional_state: analysis?.emotional_state || '',
    });
    setEditingStats(true);
    window.setTimeout(() => {
      rrPlanInputRef.current?.focus();
      rrPlanInputRef.current?.select?.();
    }, 0);
  }, [focusPlannedRisk, analysis, trade]);

  // ── Execution handlers ────────────────────────────────────────────────────

  const handleAddExecution = async () => {
    if (!execForm.qty || execForm.price === '') return;
    setAddingExec(true);
    setExecError(null);
    try {
      const res = await tradesApi.addExecution(trade.id, {
        ...execForm,
        qty: Number(execForm.qty),
        price: Number(execForm.price),
        commission: Number(execForm.commission || 0),
        date: execForm.date || trade.date,
      });
      setTrade(res.data);
      if (onTradeUpdate) onTradeUpdate(res.data);
      setShowAddExec(false);
      setExecForm(EMPTY_EXEC);
    } catch (e) {
      setExecError(e.response?.data?.detail || e.message);
    } finally {
      setAddingExec(false);
    }
  };

  const handleDeleteExecution = async (idx) => {
    try {
      const res = await tradesApi.deleteExecution(trade.id, idx);
      setTrade(res.data);
      if (onTradeUpdate) onTradeUpdate(res.data);
    } catch (e) {
      setExecError(e.response?.data?.detail || e.message);
    }
  };

  const handleSaveEditExec = async () => {
    if (!editExecForm) return;
    setSavingEditExec(true);
    setExecError(null);
    try {
      const res = await tradesApi.updateExecution(trade.id, editingExecIdx, {
        ...editExecForm,
        qty: Number(editExecForm.qty),
        price: Number(editExecForm.price),
        commission: Number(editExecForm.commission || 0),
        date: editExecForm.date || trade.date,
      });
      setTrade(res.data);
      if (onTradeUpdate) onTradeUpdate(res.data);
      setEditingExecIdx(null);
      setEditExecForm(null);
    } catch (e) {
      setExecError(e.response?.data?.detail || e.message);
    } finally {
      setSavingEditExec(false);
    }
  };

  // ── Analysis handlers ─────────────────────────────────────────────────────

  const toNum = v => (v === '' || v == null) ? null : (parseFloat(v) || null);

  const handleSaveStats = async () => {
    setSavingStats(true);
    try {
      const res = await tradesApi.updateAnalysis(trade.trade_group, {
        strategy: statsForm.strategy || null,
        idea_source: statsForm.idea_source || null,
        stop_loss: toNum(statsForm.stop_loss),
        risk_per_trade: toNum(statsForm.risk_per_trade),
        target_price: toNum(statsForm.target_price),
        emotional_state: statsForm.emotional_state || null,
      });
      setAnalysis(res.data);
      setEditingStats(false);
    } catch (e) {
      console.error(e);
    } finally {
      setSavingStats(false);
    }
  };

  const handleSaveStrategy = async () => {
    setSavingStrategy(true);
    try {
      const res = await tradesApi.updateAnalysis(trade.trade_group, {
        entry_reason: strategyForm.entry_reason || null,
        exit_reason:  strategyForm.exit_reason  || null,
        mistakes:     strategyForm.mistakes     || null,
      });
      setAnalysis(res.data);
      setEditingStrategy(false);
    } catch (e) {
      console.error(e);
    } finally {
      setSavingStrategy(false);
    }
  };

  const handleReviewSuggestion = (field, item) => {
    setStrategyForm(prev => ({
      ...prev,
      [field]: toggleSuggestedPhrase(prev?.[field] || '', item.text),
    }));
  };

  // ── Tag handlers ──────────────────────────────────────────────────────────

  const handleAddTag = async () => {
    if (!tagForm.tag_value.trim()) return;
    setSavingTag(true);
    setTagError(null);
    try {
      const res = await tradesApi.addTag(trade.trade_group, tagForm);
      setTags(prev => [...prev, res.data]);
      setTagForm({ tag_type: 'strategy', tag_value: '' });
      setAddingTag(false);
    } catch (e) {
      setTagError(e.response?.data?.error || e.message);
    } finally {
      setSavingTag(false);
    }
  };

  const handleDeleteTag = async (tagId) => {
    try {
      await tradesApi.deleteTag(tagId);
      setTags(prev => prev.filter(t => t.id !== tagId));
    } catch (e) {
      console.error(e);
    }
  };

  // ── Computed values ───────────────────────────────────────────────────────

  const stats = computeStats(trade);

  useEffect(() => {
    let active = true;
    let objectUrl = '';
    setChartScreenshotError(null);
    setChartScreenshotUrl('');

    if (!analysis?.chart_screenshot_path) {
      setChartScreenshotLoading(false);
      return () => {};
    }

    setChartScreenshotLoading(true);
    tradesApi.getChartScreenshot(trade.trade_group)
      .then(response => {
        if (!active) return;
        objectUrl = URL.createObjectURL(response.data);
        setChartScreenshotUrl(objectUrl);
      })
      .catch(() => {
        if (active) setChartScreenshotError('Could not load the saved chart screenshot.');
      })
      .finally(() => {
        if (active) setChartScreenshotLoading(false);
      });

    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [trade.trade_group, analysis?.chart_screenshot_path, chartScreenshotRevision]);

  const handleChartScreenshotUpload = useCallback(async (file) => {
    if (!file) return;
    setChartScreenshotUploading(true);
    setChartScreenshotError(null);
    try {
      const optimized = await optimizeChartScreenshot(file);
      const form = new FormData();
      form.append('file', optimized, optimized.name);
      const res = await tradesApi.uploadChartScreenshot(trade.trade_group, form);
      setAnalysis(prev => ({ ...(prev || {}), chart_screenshot_path: res.data.chart_screenshot_path }));
      setChartScreenshotRevision(value => value + 1);
    } catch (e) {
      setChartScreenshotError(e.response?.data?.error || e.response?.data?.detail || e.message || 'Upload failed.');
    } finally {
      setChartScreenshotUploading(false);
    }
  }, [trade.trade_group]);

  useEffect(() => {
    const handlePaste = (event) => {
      const target = event.target;
      if (target instanceof HTMLElement && (
        target.tagName === 'INPUT'
        || target.tagName === 'TEXTAREA'
        || target.isContentEditable
      )) return;

      const clipboardFiles = Array.from(event.clipboardData?.files || []);
      const itemFiles = Array.from(event.clipboardData?.items || [])
        .filter(item => item.kind === 'file' && String(item.type || '').startsWith('image/'))
        .map(item => item.getAsFile?.())
        .filter(Boolean);
      const file = [...clipboardFiles, ...itemFiles]
        .find(item => String(item.type || '').startsWith('image/'));
      if (!file) return;

      event.preventDefault();
      handleChartScreenshotUpload(file);
    };

    document.addEventListener('paste', handlePaste);
    return () => document.removeEventListener('paste', handlePaste);
  }, [handleChartScreenshotUpload]);

  const handleChartScreenshotDelete = async () => {
    setChartScreenshotError(null);
    try {
      await tradesApi.deleteChartScreenshot(trade.trade_group);
      setAnalysis(prev => ({ ...(prev || {}), chart_screenshot_path: null }));
      setChartScreenshotExpanded(false);
    } catch (e) {
      setChartScreenshotError(e.response?.data?.error || e.response?.data?.detail || e.message || 'Could not remove screenshot.');
    }
  };

  const pnl = trade.net_pnl ?? 0;

  const plannedRisk = analysis?.risk_per_trade != null && Number(analysis.risk_per_trade) > 0
    ? Math.abs(Number(analysis.risk_per_trade)) : null;
  const realizedRValue = analysis?.r_multiple != null
    ? Number(analysis.r_multiple)
    : plannedRisk && trade.net_pnl != null
      ? Number(trade.net_pnl) / plannedRisk
      : null;
  const realizedR = realizedRValue != null && Number.isFinite(realizedRValue)
    ? `${realizedRValue >= 0 ? '+' : ''}${realizedRValue.toFixed(2)}R`
    : null;

  // ── Render ────────────────────────────────────────────────────────────────

  const navIdx = tradeNavList.findIndex(t => t.id === trade.id);
  const hasPrev = navIdx > 0;
  const hasNext = navIdx !== -1 && navIdx < tradeNavList.length - 1;

  const goTo = (idx) => {
    const t = tradeNavList[idx];
    if (!t || !onNavigate) return;
    onNavigate(t);
    if (onTradeUpdate) onTradeUpdate(t);
  };

  return (
    <div>
      {/* Back nav + prev/next */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        <button type="button" onClick={onBack} className="btn btn-ghost" style={{ paddingLeft: 8 }}>
          <ArrowLeft size={16} /> Back to trades
        </button>
      </div>

      <PageHeader
        title={trade.ticker}
        subtitle={<>
          <span className="num">{trade.date}</span>
          {' / '}{trade.instrument_type ? trade.instrument_type.charAt(0) + trade.instrument_type.slice(1).toLowerCase() : 'Stock'}
          {' / '}{trade.side === 'LONG' ? 'Long' : trade.side === 'SHORT' ? 'Short' : trade.side}
          {stats.openTime && <> · Opened <span className="num">{formatExecutionTimeET(trade.date, stats.openTime)}</span></>}
          {stats.closeTime && stats.isClosed && <> · Closed <span className="num">{formatExecutionTimeET(trade.date, stats.closeTime)}</span></>}
          {stats.holdMinutes != null && <> · Held <span className="num">{stats.fmtHold(stats.holdMinutes)}</span></>}
        </>}
        actions={tradeNavList.length > 1 ? <>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => goTo(navIdx - 1)}
            disabled={!hasPrev}
            title="Previous trade"
          >
            <ChevronLeft size={16} /> Previous trade
          </button>
          <span className="num text-muted" style={{ fontSize: 13, minWidth: 54, textAlign: 'center' }} aria-live="polite">
            {navIdx + 1} / {tradeNavList.length}
          </span>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => goTo(navIdx + 1)}
            disabled={!hasNext}
            title="Next trade"
          >
            Next trade <ChevronRight size={16} />
          </button>
        </> : null}
      >
        <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
          <span className="chip">{trade.side}</span>
          <span className="chip">{stats.isClosed ? 'Closed' : 'Open'}</span>
          {stats.isClosed && (
            <span className={`chip ${stats.isWin ? 'pos' : 'neg'}`}>{stats.isWin ? 'Win' : 'Loss'}</span>
          )}
        </div>
      </PageHeader>

      <KpiStrip label="Trade metrics">
        <KpiCell
          label="Net P&L"
          value={<MoneyValue value={pnl} />}
          tone={pnl >= 0 ? 'pos' : 'neg'}
          foot={<>
            {stats.plPercent != null && <>ROI <span className={`num ${stats.plPercent >= 0 ? 'pos' : 'neg'}`}>{stats.plPercent >= 0 ? '+' : ''}{stats.plPercent.toFixed(2)}%</span></>}
            {stats.plPercent != null && trade.gross_pnl != null && ' · '}
            {trade.gross_pnl != null && <>Gross <span className="num">{fmtSigned$(trade.gross_pnl)}</span></>}
          </>}
        />
        <KpiCell
          label="Realized R"
          value={<span className="num">{realizedR || 'Set risk'}</span>}
          tone={realizedRValue != null ? (realizedRValue >= 0 ? 'pos' : 'neg') : undefined}
          foot={plannedRisk
            ? <>Planned risk <span className="num">{fmt$(plannedRisk)}</span></>
            : <>Add planned risk to calculate R</>}
        />
        <KpiCell label="Avg entry" value={<span className="num">{stats.avgEntry ? `${stats.avgEntry.toFixed(2)}` : 'n/a'}</span>} />
        <KpiCell label="Avg exit" value={<span className="num">{stats.avgExit ? `${stats.avgExit.toFixed(2)}` : 'n/a'}</span>} />
        <KpiCell
          label={trade.instrument_type === 'STOCK' ? 'Shares' : 'Contracts'}
          value={<span className="num">{stats.totalQty || 'n/a'}</span>}
          foot={trade.commissions ? <>Comm <span className="num">{fmt$(trade.commissions)}</span></> : null}
        />
        <KpiCell
          label="Exit efficiency"
          value={<span className="num">{trade.exit_efficiency != null ? `${Number(trade.exit_efficiency).toFixed(1)}%` : 'n/a'}</span>}
          tone={trade.exit_efficiency != null ? (Number(trade.exit_efficiency) >= 50 ? 'pos' : 'neg') : undefined}
        />
      </KpiStrip>

      {/* Layout: session list | chart, then tabs beside notes */}
      <div className="td-grid">
        <div className="td-session">
          <DaySidebar currentTrade={trade} onOpenDetail={onOpenDetail} />
        </div>

        <div className="td-main">
          <section className="card td-live-chart" aria-label="Trade chart">
            <TradingChart
              ticker={trade.ticker}
              date={trade.date}
              tradeGroup={trade.trade_group}
              defaultTimeframe="10Min"
              executions={parseExecs(trade)}
              side={trade.side}
              height={520}
            />
          </section>

          <div className="td-lower">
        {/* Middle: tabs + content */}
        <section className="card panel-flush" aria-label="Trade review">
          {/* Tab bar */}
          <div className="tabs" role="tablist" aria-label="Trade review sections" style={{ padding: '0 12px' }}>
            {TABS.map(t => (
              <button
                type="button"
                key={t}
                role="tab"
                id={`td-tab-${t}`}
                aria-selected={tab === t}
                aria-controls="td-panel"
                tabIndex={tab === t ? 0 : -1}
                className="tab"
                onClick={() => setTab(t)}
                onKeyDown={e => {
                  const i = TABS.indexOf(tab);
                  if (e.key === 'ArrowRight') setTab(TABS[(i + 1) % TABS.length]);
                  if (e.key === 'ArrowLeft') setTab(TABS[(i - 1 + TABS.length) % TABS.length]);
                }}
              >
                {t}
              </button>
            ))}
          </div>

          <div style={{ padding: '6px 20px 20px' }} role="tabpanel" id="td-panel" aria-labelledby={`td-tab-${tab}`}>

            {/* ── Stats tab ─────────────────────────────────────────────── */}
            {tab === 'Stats' && (
              <div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', paddingTop: 10 }}>
                  {!editingStats ? (
                    <button
                      onClick={() => {
                        setStatsForm({
                          strategy: analysis?.strategy || '',
                          idea_source: analysis?.idea_source || 'Watchlist',
                          stop_loss: analysis?.stop_loss ?? '',
                          risk_per_trade: analysis?.risk_per_trade ?? calculateDefaultPlannedRisk(trade) ?? '',
                          target_price: analysis?.target_price ?? '',
                          emotional_state: analysis?.emotional_state || '',
                        });
                        setEditingStats(true);
                      }}
                      className="btn btn-ghost btn-sm"
                      type="button"
                    >
                      <Pencil size={13} /> Edit
                    </button>
                  ) : (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button type="button" onClick={handleSaveStats} disabled={savingStats} className="btn btn-primary btn-sm">{savingStats ? 'Saving…' : 'Save'}</button>
                      <button type="button" onClick={() => setEditingStats(false)} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  )}
                </div>

                <StatRow label="Side" value={trade.side} />
                <StatRow
                  label={trade.instrument_type === 'STOCK' ? 'Shares traded' : 'Contracts traded'}
                  value={stats.totalQty || '—'}
                />
                <StatRow label="Commissions & Fees" value={trade.commissions ? fmt$(trade.commissions) : '—'} />
                <StatRow label="P/L %" value={stats.plPercent != null ? `${stats.plPercent >= 0 ? '+' : ''}${stats.plPercent.toFixed(2)}%` : '—'} valueColor={stats.plPercent != null ? (stats.plPercent >= 0 ? 'var(--green)' : 'var(--red)') : undefined} />
                <StatRow label="Gross P&L" value={trade.gross_pnl != null ? fmt$(trade.gross_pnl) : '—'} valueColor={trade.gross_pnl >= 0 ? 'var(--green)' : 'var(--red)'} />
                <StatRow label="Adjusted Cost" value={stats.adjustedCost ? fmt$(stats.adjustedCost) : '—'} />
                <StatRow label="Average Entry" value={stats.avgEntry ? `$${stats.avgEntry.toFixed(2)}` : '—'} />
                <StatRow label="Average Exit" value={stats.avgExit ? `$${stats.avgExit.toFixed(2)}` : '—'} />
                <StatRow label="Entry Time" value={stats.openTime ? formatExecutionTimeET(trade.date, stats.openTime) : '—'} />
                <StatRow label="Exit Time" value={(stats.isClosed && stats.closeTime) ? formatExecutionTimeET(trade.date, stats.closeTime) : '—'} />
                <StatRow label="Hold Time" value={stats.fmtHold(stats.holdMinutes)} />

                {editingStats ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--divider)' }}>
                    <div>
                      <div className="field-label" style={{ marginBottom: 4 }}>Strategy</div>
                      <SelectWithAdd
                        value={statsForm.strategy}
                        onChange={v => setStatsForm(f => ({ ...f, strategy: v }))}
                        options={analysisOptions.strategies}
                        label="Strategy"
                        placeholder="Select strategy"
                      />
                    </div>
                    <div>
                      <div className="field-label" style={{ marginBottom: 4 }}>Source (idea origin)</div>
                      <SelectWithAdd
                        value={statsForm.idea_source}
                        onChange={v => setStatsForm(f => ({ ...f, idea_source: v }))}
                        options={[...new Set([...DEFAULT_SOURCES, ...analysisOptions.idea_sources])]}
                        label="Source"
                        placeholder="Select source"
                      />
                    </div>
                    <div>
                      <EditField inputRef={rrPlanInputRef} autoFocus={focusPlannedRisk} label="Stop Distance ($)" type="number" value={String(statsForm.stop_loss)} onChange={v => setStatsForm(f => ({ ...f, stop_loss: v }))} />
                      <div className="text-muted" style={{ fontSize: 11.5, marginTop: 4, lineHeight: 1.35 }}>
                        Distance from entry to invalidation. For options, use the underlying-chart distance for chart R:R, not as contract premium loss.
                      </div>
                    </div>
                    <EditField label="Target Distance ($)" type="number" value={String(statsForm.target_price)} onChange={v => setStatsForm(f => ({ ...f, target_price: v }))} />
                    <div className="notice accent" style={{ margin: 0, padding: '9px 11px' }}>
                      Planned R:R: <strong className="num">{
                        Number(statsForm.stop_loss) > 0 && Number(statsForm.target_price) > 0
                          ? `1:${(Number(statsForm.target_price) / Number(statsForm.stop_loss)).toFixed(2)}`
                          : 'Enter stop and target distance'
                      }</strong>
                    </div>
                    <div>
                      <EditField label="Planned Risk ($)" type="number" value={String(statsForm.risk_per_trade)} onChange={v => setStatsForm(f => ({ ...f, risk_per_trade: v }))} />
                      <div className="text-muted" style={{ fontSize: 11.5, marginTop: 4, lineHeight: 1.35 }}>
                        {calculateDefaultPlannedRisk(trade) != null
                          ? `Auto-filled from total entry premium: ${stats.totalQty} contract${stats.totalQty === 1 ? '' : 's'} × ${stats.avgEntry.toFixed(2)} × 100 = ${calculateDefaultPlannedRisk(trade).toFixed(2)} max premium at risk. Edit this lower if your planned option-premium stop risk was smaller.`
                          : 'Total dollars accepted at risk for the full position. Enter this manually when max loss cannot be inferred safely.'}
                      </div>
                    </div>
                    <EditField label="Emotional State" value={statsForm.emotional_state} onChange={v => setStatsForm(f => ({ ...f, emotional_state: v }))} options={EMOTIONAL_STATES} />
                  </div>
                ) : analysis && (
                  <>
                    <StatRow label="Strategy" value={analysis.strategy} valueColor="var(--accent-line)" />
                    <StatRow label="Source" value={analysis.idea_source} valueColor="var(--text-secondary)" />
                    <StatRow label="Stop Distance" value={analysis.stop_loss ? '$' + Number(analysis.stop_loss).toFixed(2) : null} valueColor="var(--caution)" />
                    <StatRow label="Target Distance" value={analysis.target_price ? '$' + Number(analysis.target_price).toFixed(2) : null} valueColor="var(--accent-line)" />
                    <StatRow
                      label="Planned R:R"
                      value={
                        Number(analysis.stop_loss) > 0 && Number(analysis.target_price) > 0
                          ? `1:${(Number(analysis.target_price) / Number(analysis.stop_loss)).toFixed(2)}`
                          : null
                      }
                      valueColor="var(--accent-line)"
                    />
                    <StatRow label="Planned Risk" value={plannedRisk ? fmt$(-plannedRisk) : 'Not set'} valueColor={plannedRisk ? 'var(--caution)' : 'var(--text-secondary)'} />
                    <StatRow label="Realized R" value={realizedR || 'Set planned risk to calculate'} valueColor={realizedRValue == null ? 'var(--text-secondary)' : realizedRValue >= 0 ? 'var(--green)' : 'var(--red)'} />
                    {/* Excursion: how far the trade went your way and against you,
                        and how much of the favourable move you actually kept. */}
                    <StatRow
                      label="Max Favourable (MFE)"
                      value={trade.mfe_pct == null ? null : `+${Number(trade.mfe_pct).toFixed(2)}%`}
                      valueColor="var(--result-pos)"
                    />
                    <StatRow
                      label="Max Adverse (MAE)"
                      value={trade.mae_pct == null ? null : `${Number(trade.mae_pct).toFixed(2)}%`}
                      valueColor="var(--result-neg)"
                    />
                    <StatRow
                      label="Exit Efficiency"
                      value={trade.exit_efficiency == null ? null : `${Number(trade.exit_efficiency).toFixed(1)}%`}
                      valueColor={trade.exit_efficiency == null ? undefined
                        : trade.exit_efficiency < 0 ? 'var(--result-neg)'
                          : trade.exit_efficiency >= 50 ? 'var(--result-pos)' : 'var(--caution)'}
                    />
                    <StatRow label="Emotional State" value={analysis.emotional_state} />
                  </>
                )}
              </div>
            )}

            {/* ── Guided trade review tab ─────────────────────────────── */}
            {tab === 'Review' && (
              <div className="trade-review-shell">
                <div className="trade-review-toolbar">
                  <div>
                    <div className="trade-review-kicker"><Sparkles size={14} /> Guided journal</div>
                    <div className="trade-review-toolbar-copy">Use quick picks to document the trade consistently, then add any detail that matters.</div>
                  </div>
                  {!editingStrategy ? (
                    <button
                      onClick={() => {
                        setStrategyForm({
                          entry_reason: analysis?.entry_reason || '',
                          exit_reason:  analysis?.exit_reason  || '',
                          mistakes:     analysis?.mistakes     || '',
                        });
                        setEditingStrategy(true);
                      }}
                      className="btn btn-primary btn-sm"
                      type="button"
                    >
                      <Pencil size={13} /> {(analysis?.entry_reason || analysis?.exit_reason || analysis?.mistakes) ? 'Edit review' : 'Start review'}
                    </button>
                  ) : (
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button type="button" onClick={handleSaveStrategy} disabled={savingStrategy} className="btn btn-primary btn-sm">{savingStrategy ? 'Saving…' : 'Save review'}</button>
                      <button type="button" onClick={() => setEditingStrategy(false)} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  )}
                </div>

                <TradeReviewSummary
                  trade={trade}
                  entryReason={editingStrategy ? strategyForm.entry_reason : analysis?.entry_reason}
                  exitReason={editingStrategy ? strategyForm.exit_reason : analysis?.exit_reason}
                  mistakes={editingStrategy ? strategyForm.mistakes : analysis?.mistakes}
                />

                {editingStrategy ? (
                  <div className="trade-review-editor">
                    <div className="trade-review-editor-head">
                      <div>
                        <h3>Build the review</h3>
                        <p>Click any suggestion to add it. Click it again to remove it. You can still type your own notes.</p>
                      </div>
                    </div>

                    <QuickPickGroup
                      title="Entry"
                      subtitle="What justified the entry?"
                      suggestions={ENTRY_REVIEW_SUGGESTIONS}
                      value={strategyForm.entry_reason}
                      onToggle={item => handleReviewSuggestion('entry_reason', item)}
                      tone="entry"
                    />
                    <EditTextarea label="Entry notes" value={strategyForm.entry_reason} onChange={v => setStrategyForm(f => ({ ...f, entry_reason: v }))} />

                    <QuickPickGroup
                      title="Exit"
                      subtitle="How did you manage or close the trade?"
                      suggestions={EXIT_REVIEW_SUGGESTIONS}
                      value={strategyForm.exit_reason}
                      onToggle={item => handleReviewSuggestion('exit_reason', item)}
                      tone="exit"
                    />
                    <EditTextarea label="Exit notes" value={strategyForm.exit_reason} onChange={v => setStrategyForm(f => ({ ...f, exit_reason: v }))} />

                    <QuickPickGroup
                      title="Mistake / improvement"
                      subtitle="What should change next time?"
                      suggestions={MISTAKE_REVIEW_SUGGESTIONS}
                      value={strategyForm.mistakes}
                      onToggle={item => handleReviewSuggestion('mistakes', item)}
                      tone="mistake"
                    />
                    <EditTextarea label="Mistake / improvement notes" value={strategyForm.mistakes} onChange={v => setStrategyForm(f => ({ ...f, mistakes: v }))} />
                  </div>
                ) : (
                  <div className="trade-review-readonly">
                    {(analysis?.strategy || analysis?.idea_source) && (
                      <div className="trade-review-context">
                        {analysis?.strategy && <div><span>Strategy</span><strong>{analysis.strategy}</strong></div>}
                        {analysis?.idea_source && <div><span>Source</span><strong>{analysis.idea_source}</strong></div>}
                      </div>
                    )}

                    <div className="trade-review-note-grid">
                      <article>
                        <div className="trade-review-note-title"><Target size={15} /> Entry</div>
                        <div className="trade-review-note-copy">{analysis?.entry_reason || 'Not documented yet.'}</div>
                      </article>
                      <article>
                        <div className="trade-review-note-title"><CheckCircle2 size={15} /> Exit</div>
                        <div className="trade-review-note-copy">{analysis?.exit_reason || 'Not documented yet.'}</div>
                      </article>
                      <article className={analysis?.mistakes ? 'has-mistake' : ''}>
                        <div className="trade-review-note-title"><AlertTriangle size={15} /> Mistake / improvement</div>
                        <div className="trade-review-note-copy">{analysis?.mistakes || 'No improvement note documented yet.'}</div>
                      </article>
                    </div>

                    {analysis?.ai_feedback && (
                      <div className="notice accent">
                        {analysis.ai_feedback}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* ── Tags tab ──────────────────────────────────────────────── */}
            {tab === 'Tags' && (
              <div style={{ paddingTop: 8 }}>
                {tags.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
                    {tags.map(tag => (
                      <TagBadge key={tag.id} tag={tag} onDelete={() => handleDeleteTag(tag.id)} />
                    ))}
                  </div>
                )}
                {!addingTag ? (
                  <button type="button" onClick={() => setAddingTag(true)} className="btn btn-ghost" style={{ color: 'var(--accent-line)', paddingLeft: 6 }}>
                    <PlusCircle size={15} /> Add Tag
                  </button>
                ) : (
                  <div style={editPanelStyle}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--text-primary)' }}>Add Tag</div>
                    <div style={editGridStyle}>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Type</div>
                        <select aria-label="Tag type" value={tagForm.tag_type} onChange={e => setTagForm(f => ({ ...f, tag_type: e.target.value }))} style={inputStyle}>
                          {TAG_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                        </select>
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Value</div>
                        <input
                          type="text" placeholder="tag value" aria-label="Tag value" value={tagForm.tag_value}
                          onChange={e => setTagForm(f => ({ ...f, tag_value: e.target.value }))}
                          onKeyDown={e => e.key === 'Enter' && handleAddTag()}
                          style={inputStyle}
                        />
                      </div>
                    </div>
                    {tagError && (
                      <div className="notice neg" role="alert" style={{ marginBottom: 10 }}>{tagError}</div>
                    )}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button type="button" onClick={handleAddTag} disabled={savingTag} className="btn btn-primary btn-sm">{savingTag ? 'Saving…' : 'Add'}</button>
                      <button type="button" onClick={() => { setAddingTag(false); setTagForm({ tag_type: 'strategy', tag_value: '' }); setTagError(null); }} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  </div>
                )}
                {tags.length === 0 && !addingTag && (
                  <div className="text-muted" style={{ fontSize: 14, marginTop: 8 }}>No tags yet.</div>
                )}
              </div>
            )}

            {/* ── LE Review tab ────────────────────────────────────────── */}
            {tab === 'LE Review' && (
              <LEReview
                trade={trade}
                analysis={analysis}
                tags={tags}
                onAnalysisChange={setAnalysis}
                onTagsChange={setTags}
              />
            )}

            {/* ── Executions tab ────────────────────────────────────────── */}
            {tab === 'Executions' && (
              <div style={{ paddingTop: 8 }}>
                {/* The card wrapping this panel clips overflow with no scrollbar, so the
                    edit/delete column silently disappeared off the right edge on any
                    trade with enough columns to not fit the fixed-width side panel. An
                    explicit scroll container is what actually makes those reachable. */}
                <div className="scroll-x" style={{ margin: '0 -20px' }}>
                <table style={{ minWidth: 470 }}>
                  <thead>
                    <tr>
                      {['Date', 'Time', 'Action', 'Qty', 'Price', 'Comm.', ''].map((h, hi) => (
                        <th key={h || hi} className={h === 'Date' || h === 'Time' || h === 'Action' ? undefined : 'num'} style={{ paddingLeft: hi === 0 ? 20 : undefined, paddingRight: hi === 6 ? 20 : undefined }}>
                          {h || <span className="sr-only">Actions</span>}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {parseExecs(trade).map((ex, i) => (
                      <tr key={i}>
                        <td className="mono text-muted" style={{ paddingLeft: 20, fontSize: 13, whiteSpace: 'nowrap' }}>{ex.date ? ex.date.slice(5) : '—'}</td>
                        <td className="mono" style={{ fontSize: 13.5, whiteSpace: 'nowrap' }}>{ex.time ? formatExecutionTimeET(ex.date || trade.date, ex.time) : '—'}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{ex.action}</td>
                        <td className="num mono" style={{ fontSize: 13.5 }}>{ex.qty}</td>
                        <td className="num mono" style={{ fontSize: 13.5 }}>${Number(ex.price ?? 0).toFixed(2)}</td>
                        <td className="num mono text-muted" style={{ fontSize: 13.5 }}>{ex.commission ? `$${Number(ex.commission).toFixed(2)}` : '—'}</td>
                        <td className="num" style={{ whiteSpace: 'nowrap', paddingRight: 20 }}>
                          <button
                            title="Edit"
                            onClick={() => {
                              setEditingExecIdx(i);
                              setEditExecForm({ ...ex, qty: String(ex.qty), price: String(ex.price), commission: String(ex.commission || ''), date: ex.date || trade.date, time: ex.time || '' });
                              setShowAddExec(false);
                            }}
                            type="button"
                            aria-label={`Edit execution ${i + 1}`}
                            className="btn btn-ghost btn-icon"
                          >
                            <Pencil size={14} />
                          </button>
                          <button type="button" onClick={() => handleDeleteExecution(i)} title="Delete" aria-label={`Delete execution ${i + 1}`} className="btn btn-ghost btn-icon" style={{ color: 'var(--result-neg)' }}>
                            <Trash2 size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>

                <div className="text-muted" style={{ fontSize: 13, marginTop: 10 }}>
                  Gross <span className="num">{trade.gross_pnl != null ? fmtSigned$(trade.gross_pnl) : 'n/a'}</span>
                  {' · '}Commissions <span className="num">{trade.commissions ? fmt$(trade.commissions) : '$0.00'}</span>
                </div>

                {/* Edit Execution inline panel */}
                {editingExecIdx !== null && editExecForm && (
                  <div style={editPanelStyle}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--text-primary)' }}>Edit Execution #{editingExecIdx + 1}</div>
                    <div style={editGridStyle}>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Action</div>
                        <select aria-label="Edit execution action" value={editExecForm.action} onChange={e => setEditExecForm(f => ({ ...f, action: e.target.value }))} style={inputStyle}>
                          <option value="BOT">BOT (Buy)</option>
                          <option value="SOLD">SOLD (Sell)</option>
                        </select>
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Qty</div>
                        <input aria-label="Edit execution qty" type="number" min="1" value={editExecForm.qty} onChange={e => setEditExecForm(f => ({ ...f, qty: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Price</div>
                        <input aria-label="Edit execution price" type="number" min="0" step="0.01" value={editExecForm.price} onChange={e => setEditExecForm(f => ({ ...f, price: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Commission</div>
                        <input aria-label="Edit execution commission" type="number" min="0" step="0.01" value={editExecForm.commission} onChange={e => setEditExecForm(f => ({ ...f, commission: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Date</div>
                        <input aria-label="Edit execution date" type="date" value={editExecForm.date} onChange={e => setEditExecForm(f => ({ ...f, date: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Broker time (CT)</div>
                        <input aria-label="Edit execution time" type="time" value={editExecForm.time} onChange={e => setEditExecForm(f => ({ ...f, time: e.target.value }))} style={inputStyle} />
                      </div>
                    </div>
                    {execError && <div className="notice neg" role="alert" style={{ marginBottom: 10 }}>{execError}</div>}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button type="button" onClick={handleSaveEditExec} disabled={savingEditExec} className="btn btn-primary btn-sm">{savingEditExec ? 'Saving…' : 'Save'}</button>
                      <button type="button" onClick={() => { setEditingExecIdx(null); setEditExecForm(null); setExecError(null); }} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  </div>
                )}

                {/* Add Execution */}
                {!showAddExec ? (
                  <button
                    type="button"
                    onClick={() => { setShowAddExec(true); setEditingExecIdx(null); setEditExecForm(null); }}
                    className="btn btn-ghost"
                    style={{ marginTop: 12, color: 'var(--accent-line)', paddingLeft: 6 }}
                  >
                    <PlusCircle size={15} /> Add Execution
                  </button>
                ) : (
                  <div style={editPanelStyle}>
                    <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--text-primary)' }}>Add Execution</div>
                    <div style={editGridStyle}>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Action</div>
                        <select aria-label="New execution action" value={execForm.action} onChange={e => setExecForm(f => ({ ...f, action: e.target.value }))} style={inputStyle}>
                          <option value="BOT">BOT (Buy)</option>
                          <option value="SOLD">SOLD (Sell)</option>
                        </select>
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Qty</div>
                        <input aria-label="New execution qty" type="number" min="1" value={execForm.qty} placeholder="0" onChange={e => setExecForm(f => ({ ...f, qty: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Price</div>
                        <input aria-label="New execution price" type="number" min="0" step="0.01" value={execForm.price} onChange={e => setExecForm(f => ({ ...f, price: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Commission</div>
                        <input aria-label="New execution commission" type="number" min="0" step="0.01" value={execForm.commission} onChange={e => setExecForm(f => ({ ...f, commission: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Date</div>
                        <input aria-label="New execution date" type="date" value={execForm.date || trade.date} onChange={e => setExecForm(f => ({ ...f, date: e.target.value }))} style={inputStyle} />
                      </div>
                      <div>
                        <div className="field-label" style={{ marginBottom: 4 }}>Broker time (CT)</div>
                        <input aria-label="New execution time" type="time" value={execForm.time} onChange={e => setExecForm(f => ({ ...f, time: e.target.value }))} style={inputStyle} />
                      </div>
                    </div>
                    {execError && <div className="notice neg" role="alert" style={{ marginBottom: 10 }}>{execError}</div>}
                    <div style={{ display: 'flex', gap: 8 }}>
                      <button type="button" onClick={handleAddExecution} disabled={addingExec} className="btn btn-primary btn-sm">{addingExec ? 'Saving…' : 'Save'}</button>
                      <button type="button" onClick={() => { setShowAddExec(false); setExecForm(EMPTY_EXEC); setExecError(null); }} className="btn btn-ghost btn-sm">Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* ── Chart Review tab ─────────────────────────────────────── */}
            {tab === 'Chart Review' && (
              <div className="td-chart-review-panel">
                <div className="td-chart-review-callout">
                  <Target size={20} />
                  <div>
                    <strong>Review the setup visually</strong>
                    <p>Use the 10-minute chart and your TradingView screenshot together. Check entry location, key levels, 8 EMA structure, planned risk, trims, and whether the trade followed your original thesis.</p>
                  </div>
                </div>
                <div className="td-chart-review-checks">
                  <span>10m default</span>
                  <span>8 EMA</span>
                  <span>PDH / PDL solid</span>
                  <span>PMH / PML dashed</span>
                </div>
                <label className="btn btn-primary btn-sm td-chart-review-upload">
                  <Upload size={14} /> {analysis?.chart_screenshot_path ? 'Replace screenshot' : 'Paste or upload screenshot'}
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    disabled={chartScreenshotUploading}
                    onChange={e => {
                      const file = e.target.files?.[0];
                      e.target.value = '';
                      handleChartScreenshotUpload(file);
                    }}
                  />
                </label>
              </div>
            )}

          </div>
        </section>

        {/* Beside the tabs: notes, tags, AI analysis, what-if */}
        <div className="stack">
          {/* Strategy & notes */}
          {analysis && (analysis.entry_reason || analysis.exit_reason || analysis.mistakes) && (
            <section className="card">
              <PanelHead title="Strategy Notes" />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {analysis.entry_reason && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Entry Reason</div>
                    <div style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.entry_reason}</div>
                  </div>
                )}
                {analysis.exit_reason && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Exit Reason</div>
                    <div style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.exit_reason}</div>
                  </div>
                )}
                {analysis.mistakes && (
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Mistakes</div>
                    <div className="neg" style={{ fontSize: 14.5, lineHeight: 1.55 }}>{analysis.mistakes}</div>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Tags */}
          {tags.length > 0 && (
            <section className="card">
              <PanelHead title="Tags" />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {tags.map(tag => <TagBadge key={tag.id} tag={tag} />)}
              </div>
            </section>
          )}

          {/* TradingView screenshot — compact until opened */}
          <section className="card td-chart-screenshot-side" aria-label="TradingView screenshot">
            <div className="td-chart-screenshot-head">
              <div>
                <div className="section-title" style={{ fontSize: 15 }}>Chart screenshot</div>
                <div className="text-muted" style={{ fontSize: 11.5, marginTop: 2 }}>
                  Paste with Ctrl+V or upload. Stored images are automatically compressed.
                </div>
              </div>
            </div>

            {chartScreenshotLoading ? (
              <div className="td-chart-preview-loading text-muted">Loading screenshot…</div>
            ) : chartScreenshotUrl ? (
              <>
                <button
                  type="button"
                  className="td-chart-preview"
                  onClick={() => setChartScreenshotExpanded(true)}
                  title="Open screenshot full screen"
                  aria-haspopup="dialog"
                  style={{
                    width: 800,
                    maxWidth: '100%',
                    alignSelf: 'flex-start',
                    flex: '0 0 auto',
                  }}
                >
                  <img
                    src={chartScreenshotUrl}
                    alt={`${trade.ticker} TradingView review screenshot`}
                    style={{
                      width: '100%',
                      height: 'auto',
                      maxHeight: 600,
                      objectFit: 'contain',
                    }}
                  />
                  <span><Maximize2 size={13} /> Open full screen</span>
                </button>
                <div
                  className="td-chart-screenshot-actions td-chart-screenshot-actions-bottom"
                  style={{
                    width: 800,
                    maxWidth: '100%',
                    justifyContent: 'flex-start',
                  }}
                >
                  <label className="btn btn-ghost btn-sm td-chart-review-upload">
                    <Upload size={13} /> Replace
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      disabled={chartScreenshotUploading}
                      onChange={e => {
                        const file = e.target.files?.[0];
                        e.target.value = '';
                        handleChartScreenshotUpload(file);
                      }}
                    />
                  </label>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={handleChartScreenshotDelete}>
                    <Trash2 size={13} /> Remove
                  </button>
                </div>
              </>
            ) : (
              <label
                className="td-chart-dropzone td-chart-dropzone-compact"
                style={{ width: 800, maxWidth: '100%', minHeight: 240, alignSelf: 'flex-start' }}
              >
                <div className="td-chart-dropzone-empty">
                  <Upload size={24} />
                  <strong>{chartScreenshotUploading ? 'Optimizing & uploading…' : 'Paste or upload screenshot'}</strong>
                  <span>Ctrl+V works anywhere on this trade. Or click here to choose an image.</span>
                </div>
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={chartScreenshotUploading}
                  onChange={e => {
                    const file = e.target.files?.[0];
                    e.target.value = '';
                    handleChartScreenshotUpload(file);
                  }}
                />
              </label>
            )}
            {chartScreenshotError && <div className="notice neg" role="alert">{chartScreenshotError}</div>}
          </section>

          {/* AI Feedback — only shown when diary analysis exists */}
          {analysis?.ai_feedback && (
            <section className="card">
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                <h2 className="section-title">AI Analysis</h2>
                {analysis.match_confidence && (
                  <span className="chip">
                    <span className={`confidence-dot confidence-${analysis.match_confidence}`} />
                    {analysis.match_confidence} match
                  </span>
                )}
              </div>
              <div className="notice accent" style={{ lineHeight: 1.6 }}>
                {analysis.ai_feedback}
              </div>
              {analysis.r_multiple != null && (
                <div style={{ marginTop: 12, display: 'flex', gap: 16 }}>
                  <div>
                    <div className="field-label" style={{ marginBottom: 4 }}>Trade Quality (R)</div>
                    <div className={`num ${analysis.r_multiple >= 0 ? 'pos' : 'neg'}`} style={{ fontSize: 20, fontWeight: 600 }}>
                      {Number(analysis.r_multiple).toFixed(2)}R
                    </div>
                  </div>
                  {analysis.risk_reward && (
                    <div>
                      <div className="field-label" style={{ marginBottom: 4 }}>Planned R:R</div>
                      <div className="num" style={{ fontSize: 20, fontWeight: 600 }}>1:{Number(analysis.risk_reward).toFixed(1)}</div>
                    </div>
                  )}
                </div>
              )}
            </section>
          )}


        </div>
          </div>
        </div>
      </div>

      {chartScreenshotExpanded && chartScreenshotUrl && (
        <div
          className="td-image-modal"
          role="dialog"
          aria-modal="true"
          aria-label="TradingView screenshot"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setChartScreenshotExpanded(false);
          }}
        >
          <div className="td-image-modal-hint">Click outside or press Esc to close</div>
          <button
            type="button"
            className="td-image-modal-close"
            aria-label="Close screenshot"
            onClick={() => setChartScreenshotExpanded(false)}
          >
            ×
          </button>
          <img
            src={chartScreenshotUrl}
            alt={`${trade.ticker} TradingView review screenshot full screen`}
          />
        </div>
      )}
    </div>
  );
}
