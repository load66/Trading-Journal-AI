const DEFAULT_EXECUTION_TZ = 'America/Chicago';
const MARKET_TZ = 'America/New_York';

export function parseExecutions(trade) {
  const raw = trade?.executions;
  if (Array.isArray(raw)) return raw;
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function partsInZone(date, timeZone) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(date)
      .filter((p) => p.type !== 'literal')
      .map((p) => [p.type, p.value])
  );
  return parts;
}

export function zonedWallTimeToDate(dateStr, timeStr, timeZone = DEFAULT_EXECUTION_TZ) {
  if (!dateStr || !timeStr) return null;
  const [year, month, day] = String(dateStr).split('-').map(Number);
  const [hour, minute, second = 0] = String(timeStr).split(':').map(Number);
  if (![year, month, day, hour, minute, second].every(Number.isFinite)) return null;

  const wallUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
  let utcMs = wallUtcMs;
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hourCycle: 'h23',
  });

  for (let i = 0; i < 2; i += 1) {
    const parts = Object.fromEntries(
      formatter.formatToParts(new Date(utcMs))
        .filter((p) => p.type !== 'literal')
        .map((p) => [p.type, p.value])
    );
    const renderedAsUtc = Date.UTC(
      Number(parts.year), Number(parts.month) - 1, Number(parts.day),
      Number(parts.hour), Number(parts.minute), Number(parts.second)
    );
    utcMs += wallUtcMs - renderedAsUtc;
  }
  return new Date(utcMs);
}

export function executionInstant(execution, fallbackDate) {
  if (!execution) return null;
  const rawUtc = String(execution.timestamp_utc || '').trim();
  if (rawUtc) {
    const parsed = new Date(rawUtc);
    if (Number.isFinite(parsed.getTime())) return parsed;
  }
  return zonedWallTimeToDate(
    execution.date || fallbackDate,
    execution.time,
    execution.source_timezone || DEFAULT_EXECUTION_TZ
  );
}

export function executionMarketParts(execution, fallbackDate) {
  const instant = executionInstant(execution, fallbackDate);
  if (!instant) return null;
  const p = partsInZone(instant, MARKET_TZ);
  return {
    date: `${p.year}-${p.month}-${p.day}`,
    hour: Number(p.hour),
    minute: Number(p.minute),
    second: Number(p.second),
    hhmm: `${p.hour}:${p.minute}`,
  };
}

export function entryExitFills(trade) {
  const side = String(trade?.side || 'LONG').toUpperCase();
  const entryAction = side === 'SHORT' ? 'SOLD' : 'BOT';
  const exitAction = side === 'SHORT' ? 'BOT' : 'SOLD';
  const execs = parseExecutions(trade);
  const key = (e) => executionInstant(e, trade?.date)?.getTime() ?? Number.MAX_SAFE_INTEGER;
  return {
    entries: execs.filter((e) => String(e.action || '').toUpperCase() === entryAction).sort((a,b)=>key(a)-key(b)),
    exits: execs.filter((e) => String(e.action || '').toUpperCase() === exitAction).sort((a,b)=>key(a)-key(b)),
  };
}

export function weightedPrice(fills) {
  const qty = (fills || []).reduce((s, f) => s + Number(f.qty || 0), 0);
  if (!(qty > 0)) return null;
  return (fills || []).reduce((s, f) => s + Number(f.qty || 0) * Number(f.price || 0), 0) / qty;
}

export function isTradeClosed(trade) {
  const execs = parseExecutions(trade);
  if (!execs.length) return trade?.net_pnl != null;
  const bot = execs.filter((e)=>String(e.action || '').toUpperCase()==='BOT').reduce((s,e)=>s+Number(e.qty||0),0);
  const sold = execs.filter((e)=>String(e.action || '').toUpperCase()==='SOLD').reduce((s,e)=>s+Number(e.qty||0),0);
  return bot > 0 && sold > 0 && Math.abs(bot - sold) < 1e-6;
}

export function tradeHoldSeconds(trade) {
  const { entries, exits } = entryExitFills(trade);
  if (!entries.length || !exits.length) return null;
  const start = executionInstant(entries[0], trade?.date);
  const end = executionInstant(exits[exits.length - 1], trade?.date);
  if (!start || !end) return null;
  const sec = (end.getTime() - start.getTime()) / 1000;
  return sec >= 0 ? sec : null;
}

export function tradeMarketHour(trade, which = 'entry') {
  const { entries, exits } = entryExitFills(trade);
  const fills = which === 'exit' ? exits : entries;
  if (!fills.length) return null;
  const fill = which === 'exit' ? fills[fills.length - 1] : fills[0];
  const p = executionMarketParts(fill, trade?.date);
  return p ? p.hour + p.minute / 60 + p.second / 3600 : null;
}

export function tradeStats(trade) {
  const { entries, exits } = entryExitFills(trade);
  const avgEntry = weightedPrice(entries);
  const avgExit = weightedPrice(exits);
  const totalQty = entries.reduce((s, f) => s + Number(f.qty || 0), 0);
  const instrument = String(trade?.instrument_type || 'STOCK').toUpperCase();
  const multiplier = instrument === 'OPTION' ? 100 : instrument === 'STOCK' ? 1 : null;
  const adjustedCost = multiplier != null && avgEntry > 0 && totalQty > 0
    ? avgEntry * totalQty * multiplier
    : null;
  const plPercent = trade?.pl_pct != null && Number.isFinite(Number(trade.pl_pct))
    ? Number(trade.pl_pct)
    : adjustedCost > 0
      ? Number(trade?.net_pnl || 0) / adjustedCost * 100
      : null;
  const holdSeconds = tradeHoldSeconds(trade);
  return {
    avgEntry,
    avgExit,
    totalQty,
    adjustedCost,
    plPercent,
    holdSeconds,
    holdMinutes: holdSeconds == null ? null : holdSeconds / 60,
    isClosed: isTradeClosed(trade),
    isWin: Number(trade?.net_pnl || 0) > 0,
    entryFills: entries,
    exitFills: exits,
  };
}
