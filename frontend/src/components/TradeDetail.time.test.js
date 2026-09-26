import {
  computeWhatIf,
  executionTimeETMinutes,
  formatExecutionTimeET,
} from './TradeDetail';
import { executionToChartTs } from '../tradeTime';

describe('TradeDetail Eastern Time normalization', () => {
  test('converts Schwab Central execution time to Eastern time', () => {
    expect(formatExecutionTimeET('2026-09-25', '09:22:00')).toBe('10:22 ET');
    expect(executionTimeETMinutes('2026-09-25', '09:22:00')).toBe(10 * 60 + 22);
  });

  test('plots broker fills on the same ET wall-clock timeline as market bars', () => {
    const expected = Math.floor(Date.UTC(2026, 8, 25, 10, 20, 0) / 1000);
    expect(executionToChartTs('2026-09-25', '09:22:33', 5)).toBe(expected);
  });

  test('keeps Central-to-Eastern conversion DST-aware in winter', () => {
    expect(formatExecutionTimeET('2026-12-15', '09:22:00')).toBe('10:22 ET');
  });

  test('What If offsets are anchored to ET exit time', () => {
    const bars = [
      { t: '2026-09-25T14:27:00Z', c: 101 }, // 10:27 ET = exit +5
      { t: '2026-09-25T14:32:00Z', c: 102 }, // 10:32 ET = exit +10
      { t: '2026-09-25T14:52:00Z', c: 103 }, // 10:52 ET = exit +30
      { t: '2026-09-25T15:22:00Z', c: 104 }, // 11:22 ET = exit +60
      { t: '2026-09-25T19:59:00Z', c: 105 }, // 15:59 ET
    ];
    const stats = {
      isClosed: true,
      avgExit: 100,
      closeTime: '09:22:00', // raw broker Central
      totalQty: 1,
    };
    const trade = {
      date: '2026-09-25',
      instrument_type: 'STOCK',
      side: 'LONG',
      net_pnl: 0,
    };

    const scenarios = computeWhatIf(bars, stats, trade);
    expect(scenarios.map(s => s.scenarioHHMM)).toEqual([
      '10:27', '10:32', '10:52', '11:22', '16:00',
    ]);
    expect(scenarios.map(s => s.price)).toEqual([101, 102, 103, 104, 105]);
  });
});
