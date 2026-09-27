import { useState, useEffect, useCallback, useRef } from 'react';
import './TradeDetail.mobile.css';
import { ArrowLeft, ChevronLeft, ChevronRight, PlusCircle, Trash2, Pencil, Sparkles, Target, AlertTriangle, CheckCircle2, Upload, BookOpen, ClipboardCheck, FileText, ShieldCheck, Tags as TagsIcon, Library, RefreshCw, Copy } from 'lucide-react';
import { tradesApi, libraryApi } from '../api';
import TradingChart from './TradingChart';
import LEReview from './LEReview';
import { PageHeader, KpiStrip, KpiCell, MoneyValue, PanelHead } from './ui';
import { executionMarketParts, tradeStats as canonicalTradeStats } from '../tradeMetrics';

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

const SCREENSHOT_MAX_DIMENSION = 2200;
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
    // Trading charts contain small labels and thin level lines that need more
    // source pixels than a normal photo thumbnail. Preserve desktop detail first,
    // then step down only when necessary to keep storage bounded.
    const dimensionSteps = [SCREENSHOT_MAX_DIMENSION, 1920, 1600, 1400]
      .map(maxDimension => Math.min(maxDimension, originalLongEdge))
      .filter((value, index, arr) => value > 0 && arr.indexOf(value) === index);
    const qualitySteps = [1.00, 0.96, 0.92, 0.88, 0.84, 0.80, 0.74];

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
  const stats = canonicalTradeStats(trade);
  const openExecution = stats.entryFills[0] || null;
  const closeExecution = stats.exitFills[stats.exitFills.length - 1] || null;
  const openTime = openExecution?.time || null;
  const closeTime = closeExecution?.time || null;

  const fmtHold = (m) => {
    if (m == null || !Number.isFinite(Number(m))) return '—';
    const totalSec = Math.max(0, Math.round(Number(m) * 60));
    if (totalSec < 60) return `${totalSec}s`;
    if (totalSec < 3600) return `${Math.floor(totalSec / 60)}m ${totalSec % 60}s`;
    const h = Math.floor(totalSec / 3600);
    const min = Math.floor((totalSec % 3600) / 60);
    return `${h}h ${min}m`;
  };

  return {
    ...stats,
    openTime,
    closeTime,
    openExecution,
    closeExecution,
    fmtHold,
  };
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

function TradeReviewSummary({ trade, entryReason, exitReason, mistakes, notes }) {
  const completion = reviewCompletion(entryReason, exitReason, mistakes, notes);
  const selectedMistakes = selectedSuggestions(mistakes, MISTAKE_REVIEW_SUGGESTIONS);
  const primary = selectedMistakes[0] || null;
  const pnl = Number(trade?.net_pnl || 0);

  const fallbackFocus = completion < 4
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
          <div className="trade-review-summary-subtitle">{completion}/4 review areas documented</div>
        </div>
        <div className={`trade-review-completion c${completion}`}>
          {completion === 4 ? 'Complete' : 'In progress'}
        </div>
      </div>

      <div className="trade-review-summary-grid">
        <div className="trade-review-summary-cell">
          <span>Primary improvement</span>
          <strong className={primary ? 'neg' : ''}>
            {primary ? primary.label : completion === 4 ? 'No tagged rule violation' : 'Finish the review'}
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

const TAG_TYPES = ['mistake', 'execution', 'setup', 'emotion', 'outcome'];

const TAG_TYPE_META = {
  mistake: {
    label: 'Mistake',
    plural: 'Mistakes',
    help: 'What reduced the quality of the trade or entry.',
  },
  execution: {
    label: 'Execution',
    plural: 'Execution',
    help: 'How the order or management was executed.',
  },
  setup: {
    label: 'Setup context',
    plural: 'Setup context',
    help: 'Extra setup context you want to compare later.',
  },
  emotion: {
    label: 'Emotion',
    plural: 'Emotion',
    help: 'The emotional state that affected the trade.',
  },
  outcome: {
    label: 'Outcome',
    plural: 'Outcome',
    help: 'A repeatable outcome pattern worth tracking.',
  },
};

function tagLibraryItems(library, type) {
  return library?.tags?.[type] || [];
}

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

const REVIEW_TEMPLATES = [
  {
    id: 'le-complete',
    title: 'LE Complete Review',
    badge: 'Full playbook',
    description: 'The complete LE workflow: setup, 13-point pre-trade audit, plan vs. execution, 3-2-1 management, psychology, and the five journal questions.',
    icon: 'book',
  },
  {
    id: 'le-quick',
    title: 'LE Quick Review',
    badge: 'Fast recap',
    description: 'A compact LE recap for routine trades: Flag + Line + Sign, plan, management, rule breaks, and one next-trade lesson.',
    icon: 'check',
  },
];

function journalMoney(value) {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return '—';
  return `$${Number(value).toFixed(2)}`;
}

function journalRr(analysis) {
  const stop = Number(analysis?.stop_loss);
  const target = Number(analysis?.target_price);
  if (!(stop > 0) || !(target > 0)) return '—';
  return `1:${(target / stop).toFixed(2)}`;
}

function buildLeCompleteReviewTemplate(trade, analysis) {
  const s = computeStats(trade);
  const setup = analysis?.strategy || '[L / E / Purple Profits / Other]';
  const risk = analysis?.risk_per_trade ? journalMoney(analysis.risk_per_trade) : '[planned $ risk]';
  const result = trade?.net_pnl == null ? '—' : fmtSigned$(trade.net_pnl);
  return [
    `LE COMPLETE TRADE REVIEW · ${trade?.ticker || 'TRADE'} · ${trade?.date || ''}`,
    '',
    '01 · SETUP + MARKET CONTEXT',
    `Direction: ${trade?.side || '—'}    Strategy: ${setup}`,
    'LE entry type: [L level retest / E EMA retest / Purple Profits / other]',
    'Key level in play: [PDH / PDL / PMH / PML / other]',
    'Flag quality / pullback structure: ',
    'SPY + QQQ confirmation: ',
    'VIX / volatility context: ',
    'Trading window: [9:40–11:30 prime / 11:30–1:30 chop exception / 1:30–3:00 selective]',
    'Trade thesis — what had to happen for this trade to work? ',
    '',
    '02 · 13-POINT LE PRE-TRADE AUDIT — mark [x] or [ ]',
    '[ ] A key level was clearly broken with a close, not only a wick',
    '[ ] Trend was established after the level break',
    '[ ] Price was aligned with the 10-minute 8 EMA',
    '[ ] Price was snug to the 8 EMA, not extended / airgapped',
    '[ ] SPY and QQQ confirmed the same direction',
    '[ ] A flag / consolidation / controlled pullback formed',
    '[ ] Hard stop was entered before the trade',
    '[ ] Position size matched the planned risk',
    '[ ] Realistic reward-to-risk was at least 2:1',
    '[ ] Daily trade count and the two-red rule allowed another trade',
    '[ ] Entry was not a HOD / LOD chase',
    '[ ] Entry was outside chop hour, or I had a specific A+ exception',
    '[ ] VIX was checked and size was reduced when volatility was elevated',
    '',
    '03 · PLAN VS. EXECUTION',
    `Average entry: ${journalMoney(s.avgEntry)}`,
    `Stop distance recorded: ${journalMoney(analysis?.stop_loss)}`,
    `Target distance recorded: ${journalMoney(analysis?.target_price)}`,
    `Planned R:R: ${journalRr(analysis)}    Planned risk: ${risk}`,
    `Result: ${result}    Hold time: ${s.fmtHold(s.holdMinutes)}`,
    'Stop reference used: [previous 10m candle / setup candle / level / other]',
    'Did I enter where the setup called for it, or did I anticipate / chase? ',
    'Did I honor the original hard stop without widening it? ',
    '',
    '04 · MANAGEMENT + EXIT',
    '[ ] Took the first trim at the planned target / next key level',
    '[ ] Scaled out instead of dumping the full position at once',
    '[ ] Moved the runner stop to break-even after the planned trim',
    '[ ] Managed the runner against the 10-minute 8 EMA',
    '[ ] Used 30-minute structure as hold confirmation when relevant',
    '[ ] Never widened the stop to give the trade more room',
    'What specifically triggered my exit? ',
    'What did the 10-minute 8 EMA do after I exited? ',
    'Would the runner have paid if I followed the plan exactly? ',
    '',
    '05 · RULE + PSYCHOLOGY REVIEW',
    'Emotion before entry: ',
    'Emotion while managing: ',
    'Rule broken, if any: ',
    'Was this a system trade or an impulse / revenge / FOMO trade? ',
    'Did I focus on chart structure, or did P&L influence my decisions? ',
    '',
    '06 · LE END-OF-TRADE JOURNAL',
    '1. What setup did I take, and did Flag + Line + Sign truly align? ',
    '2. What were my entry, stop, and target, and did I follow that plan? ',
    '3. Did I exit too early, and what happened around the 8 EMA afterward? ',
    '4. Which rule did I break, if any, and what caused the break? ',
    '5. What specific lesson will I apply to the next trade? ',
    '',
    'NEXT-TRADE RULE',
    'One sentence I can execute next time: ',
  ].join('\n');
}

function buildLeQuickReviewTemplate(trade, analysis) {
  const s = computeStats(trade);
  return [
    `LE QUICK REVIEW · ${trade?.ticker || 'TRADE'} · ${trade?.date || ''}`,
    '',
    `Setup: ${analysis?.strategy || '[L / E / Purple Profits / Other]'}`,
    'Flag: [pass / fail] — ',
    'Line (key level): [pass / fail] — ',
    'Sign (SPY/QQQ + 10m 8 EMA): [pass / fail] — ',
    'Entry quality: [snug / early / chased / ideal] — ',
    `Plan: stop ${journalMoney(analysis?.stop_loss)} · target ${journalMoney(analysis?.target_price)} · R:R ${journalRr(analysis)}`,
    `Result: ${trade?.net_pnl == null ? '—' : fmtSigned$(trade.net_pnl)} · Hold ${s.fmtHold(s.holdMinutes)}`,
    'Management: [trim / break-even / runner / 8 EMA exit] — ',
    'Rule break or emotion: ',
    'Best decision: ',
    'One improvement: ',
    'Next-trade rule: ',
  ].join('\n');
}

function buildReviewTemplate(templateId, trade, analysis) {
  if (templateId === 'le-quick') return buildLeQuickReviewTemplate(trade, analysis);
  return buildLeCompleteReviewTemplate(trade, analysis);
}

function ReviewTemplateShelf({ hasNote, onUseTemplate }) {
  return (
    <section className="trade-review-templates" aria-label="Review templates">
      <div className="trade-review-templates-head">
        <div>
          <span className="trade-review-eyebrow">Templates</span>
          <h3>Start from a repeatable process</h3>
          <p>Built from the complete LE Trading System. Templates write only to the free-form trade note; your structured analytics stay separate.</p>
        </div>
        <span className="trade-review-template-source"><ShieldCheck size={13} /> LE playbook</span>
      </div>
      <div className="trade-review-template-grid">
        {REVIEW_TEMPLATES.map(template => (
          <button
            key={template.id}
            type="button"
            className={`trade-review-template-card ${template.id === 'le-complete' ? 'featured' : ''}`}
            onClick={() => onUseTemplate(template.id)}
          >
            <span className="trade-review-template-icon">
              {template.icon === 'check' ? <ClipboardCheck size={18} /> : <BookOpen size={18} />}
            </span>
            <span className="trade-review-template-copy">
              <span className="trade-review-template-title-row">
                <strong>{template.title}</strong>
                <small>{template.badge}</small>
              </span>
              <span>{template.description}</span>
              <b>{hasNote ? 'Replace journal note with template' : 'Use template'}</b>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function TradeJournalNote({ value, editing, onChange }) {
  const text = String(value || '');
  return (
    <section className="trade-review-journal">
      <div className="trade-review-journal-head">
        <div>
          <span className="trade-review-eyebrow"><FileText size={12} /> Trade note</span>
          <h3>Post-trade journal</h3>
        </div>
        <span className={`trade-review-note-state ${text.trim() ? 'saved' : 'empty'}`}>
          {text.trim() ? 'Documented' : 'Not started'}
        </span>
      </div>
      {editing ? (
        <>
          <textarea
            className="trade-review-journal-editor"
            aria-label="Trade journal note"
            value={text}
            onChange={e => onChange(e.target.value)}
            placeholder="Write what happened, what you saw, how you managed the trade, and what you will repeat or change next time…"
            spellCheck
          />
          <div className="trade-review-journal-meta">
            <span>Plain-text journal · export safe</span>
            <span>{text.length.toLocaleString()} characters</span>
          </div>
        </>
      ) : (
        <div className={`trade-review-journal-readonly ${text.trim() ? '' : 'empty'}`}>
          {text.trim() || 'Choose an LE template above or start a blank review. This note is saved with the trade and does not affect Strategy / Setup / Emotion analytics.'}
        </div>
      )}
    </section>
  );
}

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

function reviewCompletion(entryReason, exitReason, mistakes, notes) {
  return [notes, entryReason, exitReason, mistakes].filter(value => String(value || '').trim()).length;
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
    <span
      className={`chip td-tag-chip td-tag-chip-${tag.tag_type} ${TAG_CLASS[tag.tag_type] || ''}`}
      title={tag.tag_type}
    >
      <span className="td-tag-chip-label">{tag.tag_value}</span>
      {onDelete && (
        <button
          type="button"
          className="td-tag-remove"
          onClick={onDelete}
          aria-label={`Remove tag ${tag.tag_value}`}
          title="Remove tag"
        >
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

function formatExecutionObjectET(execution, fallbackDate) {
  const parts = executionMarketParts(execution, fallbackDate);
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
  const stats = canonicalTradeStats(t);
  const execution = which === 'open'
    ? stats.entryFills[0]
    : stats.exitFills[stats.exitFills.length - 1];
  return execution ? formatExecutionObjectET(execution, t.date) : null;
}

function DaySidebar({ currentTrade, onOpenDetail }) {
  const [dayTrades, setDayTrades] = useState([]);

  useEffect(() => {
    tradesApi.list({ date_from: currentTrade.date, date_to: currentTrade.date, account_id: currentTrade.account_id })
      .then(r => setDayTrades(r.data))
      .catch(() => {});
  }, [currentTrade.date, currentTrade.account_id]);

  const dayPnl = dayTrades.filter(t => !t.is_open).reduce((s, t) => s + (t.net_pnl || 0), 0);

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
  const [chartScreenshotRevision, setChartScreenshotRevision] = useState(0);
  const chartScreenshotDialogRef = useRef(null);
  const chartScreenshotBodyOverflowRef = useRef('');

  const restoreChartScreenshotScroll = useCallback(() => {
    document.body.style.overflow = chartScreenshotBodyOverflowRef.current;
  }, []);

  const openChartScreenshot = useCallback(() => {
    const dialog = chartScreenshotDialogRef.current;
    if (!dialog || dialog.open) return;
    chartScreenshotBodyOverflowRef.current = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
  }, []);

  const closeChartScreenshot = useCallback(() => {
    const dialog = chartScreenshotDialogRef.current;
    if (!dialog?.open && !dialog?.hasAttribute('open')) return;

    if (typeof dialog.close === 'function') dialog.close();
    else {
      dialog.removeAttribute('open');
      restoreChartScreenshotScroll();
    }
  }, [restoreChartScreenshotScroll]);

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

  // Copy reusable journal context from a similar trade
  const [copyJournalOpen, setCopyJournalOpen] = useState(false);
  const [copyCandidates, setCopyCandidates] = useState([]);
  const [copyCandidatesLoading, setCopyCandidatesLoading] = useState(false);
  const [copySourceGroup, setCopySourceGroup] = useState('');
  const [copyMode, setCopyMode] = useState('merge');
  const [copyParts, setCopyParts] = useState({
    review: true,
    tags: true,
    strategy: true,
    setup: true,
  });
  const [copyBusy, setCopyBusy] = useState(false);
  const [copyError, setCopyError] = useState(null);
  const [copyNotice, setCopyNotice] = useState(null);

  // Tags
  const [tagForm, setTagForm]       = useState({ tag_type: 'mistake', tag_value: '' });
  const [tagError, setTagError]     = useState(null);
  const [savingTag, setSavingTag]   = useState(false);
  const [tagLibrary, setTagLibrary] = useState({ tags: {}, tag_types: [] });
  const [tagLibraryLoading, setTagLibraryLoading] = useState(false);
  const [tagLibraryError, setTagLibraryError] = useState(null);

  const loadTagLibrary = useCallback(async () => {
    setTagLibraryLoading(true);
    setTagLibraryError(null);
    try {
      const res = await libraryApi.list();
      setTagLibrary(res.data || { tags: {}, tag_types: [] });
    } catch (e) {
      setTagLibraryError(e.response?.data?.detail || e.message || 'Could not load tag library.');
    } finally {
      setTagLibraryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTagLibrary();
  }, [loadTagLibrary]);

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
        notes:        strategyForm.notes        || null,
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

  const startReview = () => {
    setStrategyForm({
      entry_reason: analysis?.entry_reason || '',
      exit_reason: analysis?.exit_reason || '',
      mistakes: analysis?.mistakes || '',
      notes: analysis?.notes || '',
    });
    setEditingStrategy(true);
  };

  const applyReviewTemplate = (templateId) => {
    const currentNote = editingStrategy ? strategyForm?.notes : analysis?.notes;
    if (String(currentNote || '').trim()) {
      const replace = window.confirm('Replace the current trade note with this template? Entry, exit, and mistake analytics will not be changed.');
      if (!replace) return;
    }
    const base = editingStrategy ? strategyForm : {
      entry_reason: analysis?.entry_reason || '',
      exit_reason: analysis?.exit_reason || '',
      mistakes: analysis?.mistakes || '',
      notes: analysis?.notes || '',
    };
    setStrategyForm({
      ...base,
      notes: buildReviewTemplate(templateId, trade, analysis),
    });
    setEditingStrategy(true);
  };

  const openCopyJournal = async () => {
    setCopyJournalOpen(true);
    setCopyCandidatesLoading(true);
    setCopyError(null);
    setCopyNotice(null);
    setCopySourceGroup('');

    try {
      const response = await tradesApi.list({ limit: 300 });
      const currentStrategy = String(analysis?.strategy || trade.strategy || '').trim().toLowerCase();
      const currentSetup = String(trade.setup || '').trim().toLowerCase();
      const rows = (response.data || [])
        .filter(candidate => candidate.trade_group !== trade.trade_group)
        .map(candidate => {
          const strategy = String(candidate.strategy || '').trim().toLowerCase();
          const setup = String(candidate.setup || '').trim().toLowerCase();
          const strategyMatch = Boolean(currentStrategy && strategy === currentStrategy);
          const setupMatch = Boolean(currentSetup && setup === currentSetup);
          const reusable = Boolean(
            candidate.analysis_notes
            || candidate.entry_reason
            || candidate.exit_reason
            || candidate.mistakes
            || candidate.strategy
            || candidate.setup
          );
          const score = (strategyMatch ? 2 : 0) + (setupMatch ? 1 : 0);
          return { ...candidate, copy_match_score: score, copy_reusable: reusable };
        });

      const matched = rows
        .filter(candidate => candidate.copy_match_score > 0)
        .sort((a, b) => (
          b.copy_match_score - a.copy_match_score
          || String(b.date || '').localeCompare(String(a.date || ''))
          || Number(b.id || 0) - Number(a.id || 0)
        ));

      const fallback = rows
        .filter(candidate => candidate.copy_reusable)
        .sort((a, b) => (
          String(b.date || '').localeCompare(String(a.date || ''))
          || Number(b.id || 0) - Number(a.id || 0)
        ));

      const next = (matched.length ? matched : fallback).slice(0, 40);
      setCopyCandidates(next);
      if (next.length) setCopySourceGroup(next[0].trade_group);
    } catch (e) {
      setCopyError(e.response?.data?.detail || e.message || 'Could not load similar trades.');
    } finally {
      setCopyCandidatesLoading(false);
    }
  };

  const handleCopyJournal = async () => {
    if (!copySourceGroup) return;
    if (!Object.values(copyParts).some(Boolean)) {
      setCopyError('Choose at least one section to copy.');
      return;
    }

    setCopyBusy(true);
    setCopyError(null);
    setCopyNotice(null);
    try {
      const response = await tradesApi.copyJournal(trade.trade_group, {
        source_trade_group: copySourceGroup,
        include_review: copyParts.review,
        include_tags: copyParts.tags,
        include_strategy: copyParts.strategy,
        include_setup: copyParts.setup,
        mode: copyMode,
      });
      const payload = response.data || {};
      setAnalysis(payload.analysis || {});
      setTags(payload.tags || []);

      if (payload.trade) {
        const updatedTrade = {
          ...trade,
          setup: payload.trade.setup,
          setup_notes: payload.trade.setup_notes,
          setup_source: payload.trade.setup_source,
        };
        setTrade(updatedTrade);
        if (onTradeUpdate) onTradeUpdate(updatedTrade);
      }

      const source = copyCandidates.find(candidate => candidate.trade_group === copySourceGroup);
      setCopyNotice(
        `Copied journal context from ${source?.ticker || payload.source?.ticker || 'trade'} ${source?.date || payload.source?.date || ''}.`.trim()
      );
      setCopyJournalOpen(false);
      setEditingStrategy(false);
      loadTagLibrary();
    } catch (e) {
      setCopyError(e.response?.data?.detail || e.response?.data?.error || e.message || 'Could not copy journal context.');
    } finally {
      setCopyBusy(false);
    }
  };

  const selectedCopySource = copyCandidates.find(candidate => candidate.trade_group === copySourceGroup) || null;

  // ── Tag handlers ──────────────────────────────────────────────────────────

  const handleAddTag = async () => {
    const value = tagForm.tag_value.trim();
    if (!value) return;

    if (tags.some(tag => tag.tag_type === tagForm.tag_type && tag.tag_value === value)) {
      setTagError('That tag is already applied to this trade.');
      return;
    }

    setSavingTag(true);
    setTagError(null);
    try {
      const res = await tradesApi.addTag(trade.trade_group, {
        tag_type: tagForm.tag_type,
        tag_value: value,
      });
      setTags(prev => prev.some(tag => tag.id === res.data.id) ? prev : [...prev, res.data]);
      setTagForm(f => ({ ...f, tag_value: '' }));
    } catch (e) {
      setTagError(e.response?.data?.detail || e.response?.data?.error || e.message);
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
      closeChartScreenshot();
    } catch (e) {
      setChartScreenshotError(e.response?.data?.error || e.response?.data?.detail || e.message || 'Could not remove screenshot.');
    }
  };

  const pnl = trade.net_pnl ?? 0;

  const plannedRisk = analysis?.risk_per_trade != null && Number(analysis.risk_per_trade) > 0
    ? Math.abs(Number(analysis.risk_per_trade)) : null;
  const defaultMaxPremiumRisk = calculateDefaultPlannedRisk(trade);
  const usesMaxPremiumRiskBaseline = plannedRisk != null
    && defaultMaxPremiumRisk != null
    && Math.abs(plannedRisk - defaultMaxPremiumRisk) < 0.01
    && analysis?.r_multiple == null;
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
          {stats.openTime && <> · Opened <span className="num">{formatExecutionObjectET(stats.openExecution, trade.date)}</span></>}
          {stats.closeTime && stats.isClosed && <> · Closed <span className="num">{formatExecutionObjectET(stats.closeExecution, trade.date)}</span></>}
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
          foot={usesMaxPremiumRiskBaseline
            ? <>Risk basis: max premium <span className="num">{fmt$(plannedRisk)}</span></>
            : plannedRisk
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
          value={<span className="num">{pnl > 0 && trade.exit_efficiency != null ? `${Number(trade.exit_efficiency).toFixed(1)}%` : 'n/a'}</span>}
          tone={pnl > 0 && trade.exit_efficiency != null ? (Number(trade.exit_efficiency) >= 50 ? 'pos' : 'neg') : undefined}
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
                <StatRow label="Entry Time" value={stats.openTime ? formatExecutionObjectET(stats.openExecution, trade.date) : '—'} />
                <StatRow label="Exit Time" value={(stats.isClosed && stats.closeTime) ? formatExecutionObjectET(stats.closeExecution, trade.date) : '—'} />
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
                      <EditField
                        label={calculateDefaultPlannedRisk(trade) != null
                          && Math.abs(Number(statsForm.risk_per_trade) - calculateDefaultPlannedRisk(trade)) < 0.01
                          ? 'Max Premium Risk ($)'
                          : 'Planned Risk ($)'}
                        type="number"
                        value={String(statsForm.risk_per_trade)}
                        onChange={v => setStatsForm(f => ({ ...f, risk_per_trade: v }))}
                      />
                      <div className="text-muted" style={{ fontSize: 11.5, marginTop: 4, lineHeight: 1.35 }}>
                        {calculateDefaultPlannedRisk(trade) != null
                          ? `Auto-filled from total entry premium: ${stats.totalQty} contract${stats.totalQty === 1 ? '' : 's'} × ${stats.avgEntry.toFixed(2)} × 100 = ${calculateDefaultPlannedRisk(trade).toFixed(2)} maximum premium loss. Enter a smaller dollar amount when your option-premium stop defines the true planned risk.`
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
                    <StatRow
                      label={usesMaxPremiumRiskBaseline ? 'Max Premium Risk' : 'Planned Risk'}
                      value={plannedRisk ? fmt$(-plannedRisk) : 'Not set'}
                      valueColor={plannedRisk ? 'var(--caution)' : 'var(--text-secondary)'}
                    />
                    <StatRow
                      label="Realized R"
                      value={realizedR || 'Set planned risk to calculate'}
                      valueColor={realizedRValue == null ? 'var(--text-secondary)' : realizedRValue >= 0 ? 'var(--green)' : 'var(--red)'}
                    />
                    {/* Excursion: how far the trade went your way and against you,
                        and how much of the favourable move you actually kept. */}
                    <StatRow
                      label="Max Favourable (MFE)"
                      value={trade.excursion_stale || trade.mfe_pct == null ? null : `+${Number(trade.mfe_pct).toFixed(2)}%`}
                      valueColor="var(--result-pos)"
                    />
                    <StatRow
                      label="Max Adverse (MAE)"
                      value={trade.excursion_stale || trade.mae_pct == null ? null : `-${Math.abs(Number(trade.mae_pct)).toFixed(2)}%`}
                      valueColor="var(--result-neg)"
                    />
                    <StatRow
                      label="Exit Efficiency"
                      value={pnl <= 0 || trade.exit_efficiency == null ? null : `${Number(trade.exit_efficiency).toFixed(1)}%`}
                      valueColor={pnl <= 0 || trade.exit_efficiency == null ? undefined
                        : trade.exit_efficiency >= 50 ? 'var(--result-pos)' : 'var(--caution)'}
                    />
                    <StatRow label="Emotional State" value={analysis.emotional_state} />
                  </>
                )}
              </div>
            )}

            {/* ── Professional trade review workspace ─────────────────── */}
            {tab === 'Review' && (
              <div className="trade-review-shell">
                <div className="trade-review-toolbar">
                  <div>
                    <div className="trade-review-kicker"><Sparkles size={14} /> Professional review workspace</div>
                    <div className="trade-review-toolbar-copy">Journal the trade in detail, then keep Entry / Exit / Mistake fields clean for analytics and pattern detection.</div>
                  </div>
                  <div className="trade-review-toolbar-actions">
                    <button type="button" className="btn btn-ghost btn-sm" onClick={openCopyJournal}>
                      <Copy size={13} /> Copy similar
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => setTab('LE Review')}>
                      <ShieldCheck size={13} /> LE evidence
                    </button>
                    {!editingStrategy ? (
                      <button onClick={startReview} className="btn btn-primary btn-sm" type="button">
                        <Pencil size={13} /> {(analysis?.notes || analysis?.entry_reason || analysis?.exit_reason || analysis?.mistakes) ? 'Edit review' : 'Start blank review'}
                      </button>
                    ) : (
                      <>
                        <button type="button" onClick={handleSaveStrategy} disabled={savingStrategy} className="btn btn-primary btn-sm">
                          {savingStrategy ? 'Saving…' : 'Save review'}
                        </button>
                        <button type="button" onClick={() => setEditingStrategy(false)} className="btn btn-ghost btn-sm">Cancel</button>
                      </>
                    )}
                  </div>
                </div>

                {copyNotice && (
                  <div className="notice pos trade-copy-notice" role="status">{copyNotice}</div>
                )}

                {copyJournalOpen && (
                  <section className="trade-copy-panel" aria-label="Copy journal from a similar trade">
                    <div className="trade-copy-head">
                      <div>
                        <span className="trade-review-eyebrow">Journal accelerator</span>
                        <h3>Copy from a similar trade</h3>
                        <p>Matching Strategy and Setup trades are shown first. Trade-specific risk, P&amp;L, executions, chart screenshots, and LE evidence are never copied.</p>
                      </div>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => {
                          setCopyJournalOpen(false);
                          setCopyError(null);
                        }}
                      >
                        Cancel
                      </button>
                    </div>

                    {copyCandidatesLoading ? (
                      <div className="trade-copy-loading">Finding similar reviewed trades…</div>
                    ) : copyCandidates.length ? (
                      <>
                        <label className="trade-copy-source">
                          <span className="field-label">Copy from</span>
                          <select value={copySourceGroup} onChange={event => setCopySourceGroup(event.target.value)}>
                            {copyCandidates.map(candidate => {
                              const exact = candidate.copy_match_score >= 3 ? 'Strategy + setup match' : candidate.copy_match_score === 2 ? 'Strategy match' : candidate.copy_match_score === 1 ? 'Setup match' : 'Recent reviewed trade';
                              const pnl = candidate.net_pnl == null ? '' : ` · ${candidate.net_pnl >= 0 ? '+' : '-'}${Math.abs(Number(candidate.net_pnl)).toFixed(0)}`;
                              return (
                                <option value={candidate.trade_group} key={candidate.trade_group}>
                                  {candidate.date} · {candidate.ticker} · {exact}{pnl}
                                </option>
                              );
                            })}
                          </select>
                        </label>

                        {selectedCopySource && (
                          <div className="trade-copy-source-card">
                            <div>
                              <strong>{selectedCopySource.ticker}</strong>
                              <span>{selectedCopySource.date}</span>
                            </div>
                            <div>
                              <span>Strategy</span>
                              <strong>{selectedCopySource.strategy || '—'}</strong>
                            </div>
                            <div>
                              <span>Setup</span>
                              <strong>{selectedCopySource.setup || '—'}</strong>
                            </div>
                          </div>
                        )}

                        <div className="trade-copy-parts" role="group" aria-label="Journal sections to copy">
                          {[
                            ['review', 'Review', 'Journal note + Entry / Exit / Mistake'],
                            ['tags', 'Tags', 'Setup, execution, mistake, emotion, outcome tags'],
                            ['strategy', 'Strategy', 'Reusable strategy label only'],
                            ['setup', 'Playbook setup', 'The trade setup name, not setup evidence'],
                          ].map(([key, label, detail]) => (
                            <label className={`trade-copy-part ${copyParts[key] ? 'active' : ''}`} key={key}>
                              <input
                                type="checkbox"
                                checked={copyParts[key]}
                                onChange={event => setCopyParts(parts => ({ ...parts, [key]: event.target.checked }))}
                              />
                              <span>
                                <strong>{label}</strong>
                                <small>{detail}</small>
                              </span>
                            </label>
                          ))}
                        </div>

                        <div className="trade-copy-mode">
                          <button
                            type="button"
                            className={`trade-copy-mode-btn ${copyMode === 'merge' ? 'active' : ''}`}
                            aria-pressed={copyMode === 'merge'}
                            onClick={() => setCopyMode('merge')}
                          >
                            <strong>Fill blanks + add missing</strong>
                            <span>Safest default. Keeps anything you already journaled.</span>
                          </button>
                          <button
                            type="button"
                            className={`trade-copy-mode-btn danger ${copyMode === 'replace' ? 'active' : ''}`}
                            aria-pressed={copyMode === 'replace'}
                            onClick={() => setCopyMode('replace')}
                          >
                            <strong>Replace selected content</strong>
                            <span>Use when this trade should mirror the source review.</span>
                          </button>
                        </div>

                        {copyMode === 'replace' && (
                          <div className="notice caution trade-copy-warning">
                            Replace mode overwrites the selected review fields and structured tags on this trade.
                          </div>
                        )}

                        {copyError && <div className="notice neg trade-copy-error" role="alert">{copyError}</div>}

                        <div className="trade-copy-actions">
                          <button
                            type="button"
                            className="btn btn-primary"
                            onClick={handleCopyJournal}
                            disabled={copyBusy || !copySourceGroup || !Object.values(copyParts).some(Boolean)}
                          >
                            <Copy size={15} /> {copyBusy ? 'Copying…' : 'Copy journal'}
                          </button>
                        </div>
                      </>
                    ) : (
                      <div className="trade-copy-empty">
                        <strong>No similar reviewed trades found.</strong>
                        <span>Set this trade’s Strategy or Playbook Setup first, or journal another trade with reusable context.</span>
                      </div>
                    )}

                    {!copyCandidatesLoading && copyCandidates.length === 0 && copyError && (
                      <div className="notice neg trade-copy-error" role="alert">{copyError}</div>
                    )}
                  </section>
                )}

                <ReviewTemplateShelf
                  hasNote={Boolean((editingStrategy ? strategyForm?.notes : analysis?.notes)?.trim?.())}
                  onUseTemplate={applyReviewTemplate}
                />

                <div className="trade-review-workspace-grid">
                  <TradeJournalNote
                    value={editingStrategy ? strategyForm?.notes : analysis?.notes}
                    editing={editingStrategy}
                    onChange={value => setStrategyForm(prev => ({ ...prev, notes: value }))}
                  />
                  <TradeReviewSummary
                    trade={trade}
                    notes={editingStrategy ? strategyForm?.notes : analysis?.notes}
                    entryReason={editingStrategy ? strategyForm.entry_reason : analysis?.entry_reason}
                    exitReason={editingStrategy ? strategyForm.exit_reason : analysis?.exit_reason}
                    mistakes={editingStrategy ? strategyForm.mistakes : analysis?.mistakes}
                  />
                </div>

                <section className="trade-review-structured">
                  <div className="trade-review-structured-head">
                    <div>
                      <span className="trade-review-eyebrow">Structured analytics</span>
                      <h3>Keep the fields that power your reports concise</h3>
                      <p>These three fields feed your repeatable pattern analysis. Use the journal above for the full story.</p>
                    </div>
                    <span className="trade-review-analytics-badge">Report inputs</span>
                  </div>

                  {editingStrategy ? (
                    <div className="trade-review-editor">
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
                </section>
              </div>
            )}
            {/* ── Tags tab ──────────────────────────────────────────────── */}
            {tab === 'Tags' && (() => {
              const libraryItems = tagLibraryItems(tagLibrary, tagForm.tag_type);
              const appliedValues = new Set(
                tags.filter(tag => tag.tag_type === tagForm.tag_type).map(tag => tag.tag_value)
              );
              const availableItems = libraryItems.filter(item => !appliedValues.has(item.name));
              const selectedItem = libraryItems.find(item => item.name === tagForm.tag_value);
              const groupedTags = TAG_TYPES
                .map(type => ({
                  type,
                  meta: TAG_TYPE_META[type],
                  items: tags.filter(tag => tag.tag_type === type),
                }))
                .filter(group => group.items.length > 0);

              return (
                <div className="td-tags-workspace">
                  <div className="td-tags-toolbar">
                    <div>
                      <div className="td-tags-kicker"><TagsIcon size={15} /> Trade Tags</div>
                      <div className="td-tags-subtitle">Use the same saved labels on every trade so your pattern analysis stays consistent.</div>
                    </div>
                    <div className="td-tags-library-state">
                      <Library size={13} />
                      Settings library
                    </div>
                  </div>

                  <div className="td-tags-grid">
                    <section className="td-tags-card" aria-label="Add trade tag">
                      <div className="td-tags-card-head">
                        <div>
                          <h3>Add a tag</h3>
                          <p>Choose from the labels saved in Settings.</p>
                        </div>
                      </div>

                      <div className="td-tags-form">
                        <div className="td-tags-field">
                          <div className="td-tags-field-label">Category</div>
                          <div className="td-tags-category-strip" role="group" aria-label="Tag category">
                            {TAG_TYPES.map(type => {
                              const count = tagLibraryItems(tagLibrary, type).length;
                              const active = tagForm.tag_type === type;
                              return (
                                <button
                                  key={type}
                                  type="button"
                                  className={`td-tags-category-btn type-${type} ${active ? 'active' : ''}`}
                                  aria-label={TAG_TYPE_META[type].label}
                                  aria-pressed={active}
                                  disabled={tagLibraryLoading}
                                  onClick={() => {
                                    setTagForm({ tag_type: type, tag_value: '' });
                                    setTagError(null);
                                  }}
                                >
                                  <span>{TAG_TYPE_META[type].label}</span>
                                  <small>{count}</small>
                                </button>
                              );
                            })}
                          </div>
                          <div className="td-tags-category-help">
                            {TAG_TYPE_META[tagForm.tag_type].help}
                          </div>
                        </div>

                        <div className="td-tags-field">
                          <div className="td-tags-field-label">Saved tag</div>
                          <div className="td-tags-picker-row">
                            <select
                              aria-label="Saved tag"
                              value={tagForm.tag_value}
                              onChange={e => {
                                setTagForm(f => ({ ...f, tag_value: e.target.value }));
                                setTagError(null);
                              }}
                              disabled={tagLibraryLoading || availableItems.length === 0}
                            >
                              <option value="">
                                {tagLibraryLoading
                                  ? 'Loading saved tags…'
                                  : availableItems.length
                                    ? `Choose a ${TAG_TYPE_META[tagForm.tag_type].label.toLowerCase()}…`
                                    : 'No unused saved tags'}
                              </option>
                              {availableItems.map(item => (
                                <option key={item.name} value={item.name}>{item.name}</option>
                              ))}
                            </select>

                            <button
                              type="button"
                              onClick={handleAddTag}
                              disabled={savingTag || !tagForm.tag_value}
                              className="btn btn-primary td-tags-add"
                            >
                              <PlusCircle size={15} /> {savingTag ? 'Adding…' : 'Add tag'}
                            </button>
                          </div>
                        </div>

                        {selectedItem?.description && (
                          <div className="td-tags-description">
                            <strong>{selectedItem.name}</strong>
                            <span>{selectedItem.description}</span>
                          </div>
                        )}

                        {tagLibraryError && (
                          <div className="notice neg td-tags-error" role="alert">
                            <span>{tagLibraryError}</span>
                            <button type="button" className="btn btn-ghost btn-sm" onClick={loadTagLibrary}>
                              <RefreshCw size={13} /> Retry
                            </button>
                          </div>
                        )}

                        {!tagLibraryLoading && !tagLibraryError && libraryItems.length === 0 && (
                          <div className="td-tags-empty-library">
                            No {TAG_TYPE_META[tagForm.tag_type].plural.toLowerCase()} are saved yet.
                            Add them in <strong>Settings → Tags</strong>, then return here.
                          </div>
                        )}

                        {!tagLibraryLoading && !tagLibraryError && libraryItems.length > 0 && availableItems.length === 0 && (
                          <div className="td-tags-empty-library">
                            Every saved {TAG_TYPE_META[tagForm.tag_type].label.toLowerCase()} tag is already applied to this trade.
                          </div>
                        )}

                        {tagError && (
                          <div className="notice neg td-tags-error" role="alert">{tagError}</div>
                        )}

                      </div>

                      <div className="td-tags-settings-note">
                        Tag names are managed in <strong>Settings → Tags</strong>. Keeping one canonical list prevents duplicate wording.
                      </div>
                    </section>

                    <section className="td-tags-card td-tags-applied" aria-label="Tags applied to this trade">
                      <div className="td-tags-card-head">
                        <div>
                          <h3>Applied to this trade</h3>
                          <p>{tags.length ? `${tags.length} structured ${tags.length === 1 ? 'tag' : 'tags'}` : 'No tags applied yet'}</p>
                        </div>
                        <span className="td-tags-count">{tags.length}</span>
                      </div>

                      {groupedTags.length ? (
                        <div className="td-tags-groups">
                          {groupedTags.map(group => (
                            <div className={`td-tags-group type-${group.type}`} key={group.type}>
                              <div className="td-tags-group-label">
                                <span>{group.meta.label}</span>
                                <small>{group.items.length}</small>
                              </div>
                              <div className="td-tags-chip-row">
                                {group.items.map(tag => (
                                  <TagBadge key={tag.id} tag={tag} onDelete={() => handleDeleteTag(tag.id)} />
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="td-tags-empty-state">
                          <TagsIcon size={22} />
                          <strong>No structured tags yet</strong>
                          <span>Start with the mistake, execution, or emotion that best explains this trade.</span>
                        </div>
                      )}
                    </section>
                  </div>
                </div>
              );
            })()}

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
              <div className="td-chart-review-workspace">
                <div className="td-chart-review-toolbar">
                  <div className="td-chart-review-title">TradingView Screenshot</div>
                  {chartScreenshotUrl && (
                    <div className="td-chart-review-actions">
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
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm td-chart-review-remove"
                        onClick={handleChartScreenshotDelete}
                        disabled={chartScreenshotUploading}
                      >
                        <Trash2 size={13} /> Remove
                      </button>
                    </div>
                  )}
                </div>

                <div className="td-chart-review-stage">
                  {chartScreenshotLoading ? (
                    <div className="td-chart-preview-loading text-muted">Loading screenshot…</div>
                  ) : chartScreenshotUrl ? (
                    <div
                      className="td-chart-preview td-chart-review-preview"
                      role="button"
                      tabIndex={0}
                      title="Open screenshot full screen"
                      aria-label="Open chart screenshot full screen"
                      aria-haspopup="dialog"
                      onClick={openChartScreenshot}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          openChartScreenshot();
                        }
                      }}
                    >
                      <img
                        src={chartScreenshotUrl}
                        alt={`${trade.ticker} TradingView review screenshot`}
                        onClick={(event) => {
                          event.stopPropagation();
                          openChartScreenshot();
                        }}
                      />
                    </div>
                  ) : (
                    <label className="td-chart-review-empty">
                      <Upload size={26} />
                      <strong>{chartScreenshotUploading ? 'Uploading…' : 'Add chart screenshot'}</strong>
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
                </div>

                {chartScreenshotError && <div className="notice neg" role="alert">{chartScreenshotError}</div>}
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

      <dialog
        ref={chartScreenshotDialogRef}
        className="td-image-modal"
        aria-label="TradingView screenshot"
        data-testid="chart-screenshot-lightbox"
        onClose={restoreChartScreenshotScroll}
        onCancel={(event) => {
          event.preventDefault();
          closeChartScreenshot();
        }}
        style={{
          position: 'fixed',
          inset: 0,
          width: '100vw',
          height: '100vh',
          maxWidth: 'none',
          maxHeight: 'none',
          margin: 0,
          padding: 0,
          border: 0,
          background: 'transparent',
          overflow: 'hidden',
          boxSizing: 'border-box',
        }}
      >
        <div
          className="td-image-modal-stage"
          data-testid="chart-screenshot-stage"
          onClick={(event) => {
            if (event.target === event.currentTarget) closeChartScreenshot();
          }}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxSizing: 'border-box',
          }}
        >
          {chartScreenshotUrl && (
            <img
              src={chartScreenshotUrl}
              alt={`${trade.ticker} TradingView review screenshot full screen`}
            />
          )}
        </div>
        <div className="td-image-modal-hint">
          Click outside the chart or press Esc to close
        </div>
        <button
          type="button"
          className="td-image-modal-close"
          aria-label="Close screenshot"
          onClick={closeChartScreenshot}
        >
          ×
        </button>
      </dialog>
    </div>
  );
}
