import {
  calculateDefaultPlannedRisk,
  executionTimeETMinutes,
  formatExecutionTimeET,
} from './TradeDetail';

describe('TradeDetail Eastern Time normalization', () => {
  test('converts Schwab Central execution time to Eastern time', () => {
    expect(formatExecutionTimeET('2026-09-25', '09:22:00')).toBe('10:22 ET');
    expect(executionTimeETMinutes('2026-09-25', '09:22:00')).toBe(10 * 60 + 22);
  });


});


describe('TradeDetail option planned-risk baseline', () => {
  test('uses total premium paid across all long-option entry contracts', () => {
    expect(calculateDefaultPlannedRisk({
      instrument_type: 'OPTION',
      side: 'LONG',
      executions: [
        { action: 'BOT', qty: 2, price: 1.00 },
        { action: 'BOT', qty: 3, price: 2.00 },
      ],
    })).toBe(800);
  });

  test('does not guess short-option or stock risk', () => {
    expect(calculateDefaultPlannedRisk({
      instrument_type: 'OPTION',
      side: 'SHORT',
      executions: [{ action: 'SOLD', qty: 1, price: 1.00 }],
    })).toBeNull();
    expect(calculateDefaultPlannedRisk({
      instrument_type: 'STOCK',
      side: 'LONG',
      executions: [{ action: 'BOT', qty: 100, price: 50 }],
    })).toBeNull();
  });
});
