import {
  executionTimeETMinutes,
  formatExecutionTimeET,
} from './TradeDetail';

describe('TradeDetail Eastern Time normalization', () => {
  test('converts Schwab Central execution time to Eastern time', () => {
    expect(formatExecutionTimeET('2026-09-25', '09:22:00')).toBe('10:22 ET');
    expect(executionTimeETMinutes('2026-09-25', '09:22:00')).toBe(10 * 60 + 22);
  });


});
