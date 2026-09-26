import { buildExecutionLedger } from './tradeMath';

describe('buildExecutionLedger', () => {
  test('reconciles the 2026-09-25 QCOM 202.5C scale-out exactly', () => {
    const trade = {
      side: 'LONG',
      instrument_type: 'OPTION',
      net_pnl: 442.74,
      executions: [
        { date: '2026-09-25', time: '09:47:04', action: 'BOT', qty: 5, price: 0.67, commission: 2.56 },
        { date: '2026-09-25', time: '09:47:04', action: 'BOT', qty: 5, price: 0.67, commission: 2.56 },
        { date: '2026-09-25', time: '09:48:12', action: 'SOLD', qty: 2, price: 0.85, commission: 1.03 },
        { date: '2026-09-25', time: '09:48:22', action: 'SOLD', qty: 2, price: 0.75, commission: 1.03 },
        { date: '2026-09-25', time: '09:54:02', action: 'SOLD', qty: 1, price: 0.95, commission: 0.51 },
        { date: '2026-09-25', time: '09:54:09', action: 'SOLD', qty: 1, price: 0.94, commission: 0.51 },
        { date: '2026-09-25', time: '10:03:07', action: 'SOLD', qty: 1, price: 1.23, commission: 0.51 },
        { date: '2026-09-25', time: '10:03:07', action: 'SOLD', qty: 1, price: 1.24, commission: 0.53 },
        { date: '2026-09-25', time: '10:07:07', action: 'SOLD', qty: 1, price: 1.56, commission: 0.51 },
        { date: '2026-09-25', time: '10:11:06', action: 'SOLD', qty: 1, price: 2.11, commission: 0.51 },
      ],
    };

    const ledger = buildExecutionLedger(trade);
    const exits = ledger.rows.filter((r) => r.role === 'exit');

    expect(exits.map((r) => Number(r.trimReturnPct.toFixed(2)))).toEqual([
      26.87, 11.94, 41.79, 40.30, 83.58, 85.07, 132.84, 214.93,
    ]);
    expect(Number(ledger.lastTrimReturnPct.toFixed(2))).toBe(214.93);
    expect(Number(ledger.bestTrimReturnPct.toFixed(2))).toBe(214.93);
    expect(Number(ledger.realizedNet.toFixed(2))).toBe(442.74);
    expect(Number(exits[exits.length - 1].realizedPnl.toFixed(2))).toBe(142.98);
    expect(exits[exits.length - 1].isFinalExit).toBe(true);
  });
});
