import { buildMonthShareSvg, buildYearShareSvg } from './Calendar';

describe('Calendar share image documents', () => {
  test('month image includes beginner-friendly metrics and every weekday trade cell', () => {
    const spec = buildMonthShareSvg({
      year: 2026,
      month: 9,
      weeks: [
        [null, 1, 2, 3, 4],
        [7, 8, 9, 10, 11],
      ],
      dayData: {
        '2026-09-01': { net_pnl: 446, trade_count: 3 },
        '2026-09-09': { net_pnl: -1600, trade_count: 7 },
      },
      monthPnl: 2750,
      winRate: '51.4',
      profitFactor: 1.33,
      avgWinLoss: '1.26',
      tradingDays: 18,
    });

    expect(spec.filename).toBe('trading-calendar-2026-09.png');
    expect(spec.svg).toContain('TRADING PERFORMANCE SNAPSHOT');
    expect(spec.svg).toContain('NET P&amp;L');
    expect(spec.svg).toContain('WIN RATE');
    expect(spec.svg).toContain('PROFIT FACTOR');
    expect(spec.svg).toContain('AVG WIN / LOSS');
    expect(spec.svg).toContain('TRADING DAYS');
    expect(spec.svg).toContain('+$446');
    expect(spec.svg).toContain('-$1.6K');
    expect(spec.svg).toContain('3 trades');
    expect(spec.width).toBe(1200);
    expect(spec.height).toBeGreaterThan(500);
  });

  test('year image contains all twelve months and plain-English yearly metrics', () => {
    const months = Array.from({ length: 12 }, (_, i) => ({
      has_data: i < 3,
      net_pnl: i === 0 ? 500 : i === 1 ? -200 : i === 2 ? 300 : 0,
      total_trades: i < 3 ? 10 : 0,
      winning_trades: i < 3 ? 6 : 0,
      trading_days: i < 3 ? 5 : 0,
      win_rate: i < 3 ? 60 : 0,
      profit_factor: i < 3 ? 1.5 : 0,
      avg_win: 120,
      avg_loss: -80,
    }));

    const spec = buildYearShareSvg({
      year: 2026,
      yearData: months,
      yearPnl: 600,
      yearWinRate: '60.0',
      totalTrades: 30,
      tradingDays: 15,
      profitableMonths: 2,
    });

    expect(spec.filename).toBe('trading-calendar-2026.png');
    expect(spec.svg).toContain('YEARLY TRADING PERFORMANCE');
    expect(spec.svg).toContain('YTD NET P&amp;L');
    expect(spec.svg).toContain('PROFITABLE MONTHS');
    expect(spec.svg).toContain('JAN');
    expect(spec.svg).toContain('DEC');
    expect(spec.svg).toContain('2/12');
    expect(spec.width).toBe(1200);
    expect(spec.height).toBe(1460);
  });
});
