export function parseTradeExecutions(trade) {
  const raw = trade?.executions;
  if (!raw) return [];
  if (Array.isArray(raw)) return raw;
  try { return JSON.parse(raw); } catch { return []; }
}

export function buildExecutionLedger(trade) {
  const executions = parseTradeExecutions(trade);
  const side = String(trade?.side || 'LONG').toUpperCase();
  const entryAction = side === 'SHORT' ? 'SOLD' : 'BOT';
  const exitAction = side === 'SHORT' ? 'BOT' : 'SOLD';
  const direction = side === 'SHORT' ? -1 : 1;
  const instrument = String(trade?.instrument_type || 'STOCK').toUpperCase();
  const multiplier = instrument === 'OPTION' ? 100 : 1;

  let openQty = 0;
  let avgEntry = 0;
  let openEntryFees = 0;
  let realizedNet = 0;
  let exitNumber = 0;

  const rows = executions.map((ex, index) => {
    const action = String(ex.action || '').toUpperCase();
    const qty = Number(ex.qty || 0);
    const price = Number(ex.price || 0);
    const commission = Math.abs(Number(ex.commission || 0));

    if (action === entryAction && qty > 0) {
      const newQty = openQty + qty;
      avgEntry = newQty > 0 ? ((avgEntry * openQty) + (price * qty)) / newQty : price;
      openQty = newQty;
      openEntryFees += commission;
      return {
        ...ex,
        index,
        role: 'entry',
        basisPrice: price,
        trimReturnPct: null,
        realizedPnl: null,
        remainingQty: openQty,
      };
    }

    if (action === exitAction && qty > 0 && openQty > 0) {
      const closingQty = Math.min(qty, openQty);
      const basisPrice = avgEntry;
      const entryFeeAllocation = openQty > 0 ? openEntryFees * (closingQty / openQty) : 0;
      const exitFeeAllocation = qty > 0 ? commission * (closingQty / qty) : 0;
      const grossPnl = direction * (price - basisPrice) * closingQty * multiplier;
      const realizedPnl = grossPnl - entryFeeAllocation - exitFeeAllocation;
      const trimReturnPct = basisPrice > 0
        ? direction * (price - basisPrice) / basisPrice * 100
        : null;

      realizedNet += realizedPnl;
      openQty -= closingQty;
      openEntryFees = Math.max(0, openEntryFees - entryFeeAllocation);
      exitNumber += 1;

      return {
        ...ex,
        index,
        role: 'exit',
        exitNumber,
        basisPrice,
        grossPnl,
        entryFeeAllocation,
        exitFeeAllocation,
        trimReturnPct,
        realizedPnl,
        remainingQty: openQty,
      };
    }

    return {
      ...ex,
      index,
      role: 'other',
      basisPrice: null,
      trimReturnPct: null,
      realizedPnl: null,
      remainingQty: openQty,
    };
  });

  const exitRows = rows.filter((r) => r.role === 'exit');
  if (exitRows.length) {
    exitRows[exitRows.length - 1].isFinalExit = true;
  }

  const bestTrimReturnPct = exitRows.reduce((best, row) => (
    row.trimReturnPct == null ? best : (best == null || row.trimReturnPct > best ? row.trimReturnPct : best)
  ), null);
  const lastTrimReturnPct = exitRows.length ? exitRows[exitRows.length - 1].trimReturnPct : null;

  return {
    rows,
    realizedNet,
    bestTrimReturnPct,
    lastTrimReturnPct,
  };
}
