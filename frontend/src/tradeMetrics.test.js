import {
  executionMarketParts,
  isTradeClosed,
  tradeHoldSeconds,
  tradeMarketHour,
  tradeStats,
} from './tradeMetrics';

const WMT = {
  date: '2026-09-25',
  ticker: 'WMT',
  instrument_type: 'OPTION',
  side: 'LONG',
  net_pnl: -172.10,
  pl_pct: -17.26,
  executions: [
    { date: '2026-09-25', time: '14:43:00', source_timezone: 'America/Chicago', action: 'BOT', qty: 2, price: 3.35 },
    { date: '2026-09-25', time: '14:51:00', source_timezone: 'America/Chicago', action: 'BOT', qty: 1, price: 3.27 },
    { date: '2026-09-25', time: '14:52:00', source_timezone: 'America/Chicago', action: 'SOLD', qty: 3, price: 2.76 },
  ],
};

test('frontend WMT metrics use weighted option entry and backend P/L percent', () => {
  const stats = tradeStats(WMT);
  expect(stats.avgEntry).toBeCloseTo((2 * 3.35 + 3.27) / 3, 8);
  expect(stats.avgExit).toBeCloseTo(2.76, 8);
  expect(stats.adjustedCost).toBeCloseTo(997, 8);
  expect(stats.plPercent).toBeCloseTo(-17.26, 8);
  expect(stats.isClosed).toBe(true);
});

test('partial exits are not treated as completed trades', () => {
  const trade = {
    date: '2026-09-25',
    side: 'LONG',
    executions: [
      { action: 'BOT', qty: 2, price: 1, time: '09:30:00', source_timezone: 'America/Chicago' },
      { action: 'SOLD', qty: 1, price: 1.5, time: '09:31:00', source_timezone: 'America/Chicago' },
    ],
  };
  expect(isTradeClosed(trade)).toBe(false);
  expect(tradeStats(trade).isClosed).toBe(false);
});

test('Schwab Central execution clock maps to market Eastern time', () => {
  const fill = {
    date: '2026-09-25',
    time: '08:30:00',
    source_timezone: 'America/Chicago',
  };
  const market = executionMarketParts(fill, '2026-09-25');
  expect(market.hhmm).toBe('09:30');
});

test('canonical UTC timestamp takes precedence over legacy wall clock', () => {
  const fill = {
    date: '2026-09-25',
    time: '00:00:00',
    source_timezone: 'America/Chicago',
    timestamp_utc: '2026-09-25T13:30:00Z',
  };
  const market = executionMarketParts(fill, '2026-09-25');
  expect(market.hhmm).toBe('09:30');
});

test('hold duration preserves seconds and market-time placement', () => {
  const trade = {
    date: '2026-09-25',
    side: 'LONG',
    executions: [
      {
        action: 'BOT', qty: 1, price: 1,
        timestamp_utc: '2026-09-25T13:30:10Z',
      },
      {
        action: 'SOLD', qty: 1, price: 1.2,
        timestamp_utc: '2026-09-25T13:32:20Z',
      },
    ],
  };
  expect(tradeHoldSeconds(trade)).toBe(130);
  expect(tradeMarketHour(trade, 'entry')).toBeCloseTo(9.5027777778, 7);
  expect(tradeMarketHour(trade, 'exit')).toBeCloseTo(9.5388888889, 7);
});
