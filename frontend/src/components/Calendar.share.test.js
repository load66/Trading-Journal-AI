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
        '2026-09-02': { net_pnl: 100, trade_count: 2 },
        '2026-09-09': { net_pnl: -1600, trade_count: 7 },
        '2026-09-10': { net_pnl: 200, trade_count: 3 },
      },
      monthPnl: 2750,
      winRate: '51.4',
      profitFactor: 1.33,
      avgWinLoss: '1.26',
      tradingDays: 18,
      asOfDate: new Date('2026-09-27T12:00:00'),
    });

    expect(spec.filename).toBe('trading-calendar-2026-09.png');
    expect(spec.svg).toContain('AI JOURNAL · MONTHLY PERFORMANCE');
    expect(spec.svg).toContain('NET P&amp;L');
    expect(spec.svg).toContain('WIN RATE');
    expect(spec.svg).toContain('PROFIT FACTOR');
    expect(spec.svg).toContain('AVG WIN / LOSS');
    expect(spec.svg).toContain('TRADING DAYS');
    expect(spec.svg).toContain('+$446');
    expect(spec.svg).toContain('-$1.6K');
    expect(spec.svg).toContain('3 trades');
    expect(spec.svg).toContain('WEEK TOTAL');
    expect(spec.svg).toContain('WEEK 1 · SEP 1–4');
    expect(spec.svg).toContain('WEEK 2 · SEP 7–11');
    expect(spec.svg).toContain('+$546');
    expect(spec.svg).toContain('-$1.4K');
    expect(spec.svg).toContain('2 sessions');
    expect(spec.svg).toContain('BEST DAY');
    expect(spec.svg).toContain('AVG / TRADING DAY');
    expect(spec.svg).not.toContain('LARGEST LOSS');
    expect(spec.svg).toContain('GREEN DAYS');
    expect(spec.svg).toContain('TOTAL TRADES');
    expect(spec.svg).toContain('Goal ≥ 50% · Goal met');
    expect(spec.svg).toContain('Goal ≥ 1.30 · Goal met');
    expect(spec.svg).toContain('Goal ≥ 1.20 · Goal met');
    expect(spec.svg).toContain('system-ui');
    expect(spec.width).toBe(1400);
    expect(spec.height).toBeGreaterThan(500);
    expect(spec.height).toBeLessThan(1110);
  });

  test('month image marks a partially completed current week as to date', () => {
    const spec = buildMonthShareSvg({
      year: 2026,
      month: 9,
      weeks: [[28, 29, 30, null, null]],
      dayData: {
        '2026-09-28': { net_pnl: 449.91, trade_count: 6 },
      },
      monthPnl: 449.91,
      winRate: '50.0',
      profitFactor: 1.2,
      avgWinLoss: '1.10',
      tradingDays: 1,
      asOfDate: new Date('2026-09-28T12:00:00'),
    });

    expect(spec.svg).toContain('WEEK 1 · SEP 28–30');
    expect(spec.svg).toContain('+$450');
    expect(spec.svg).toContain('1 session · to date');
  });

  test('month image labels an entirely future week without fabricating a weekly P&L', () => {
    const spec = buildMonthShareSvg({
      year: 2026,
      month: 10,
      weeks: [[null, null, null, 1, 2]],
      dayData: {},
      monthPnl: 0,
      winRate: '--',
      profitFactor: undefined,
      avgWinLoss: '--',
      tradingDays: 0,
      asOfDate: new Date('2026-09-28T12:00:00'),
    });

    expect(spec.svg).toContain('WEEK 1 · OCT 1–2');
    expect(spec.svg).toContain('UPCOMING');
    expect(spec.svg).toContain('Future week');
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
      yearProfitFactor: 1.5,
      yearAvgWinLoss: '1.50',
      totalTrades: 30,
      tradingDays: 15,
      profitableMonths: 2,
      asOfDate: new Date('2026-09-27T12:00:00'),
    });

    expect(spec.filename).toBe('trading-calendar-2026.png');
    expect(spec.svg).toContain('AI JOURNAL · YEARLY PERFORMANCE');
    expect(spec.svg).toContain('YTD NET P&amp;L');
    expect(spec.svg).toContain('PROFIT FACTOR');
    expect(spec.svg).toContain('AVG WIN / LOSS');
    expect(spec.svg).toContain('JAN');
    expect(spec.svg).toContain('DEC');
    expect(spec.svg).toContain('67%');
    expect(spec.svg).toContain('2 green · 1 red');
    expect(spec.svg).toContain('BEST MONTH');
    expect(spec.svg).toContain('AVG / ACTIVE MONTH');
    expect(spec.svg).not.toContain('LARGEST LOSS');
    expect(spec.svg).toContain('PROFITABLE RATE');
    expect(spec.svg).toContain('TOTAL TRADES');
    expect(spec.svg).toContain('UPCOMING');
    expect(spec.svg).toContain('Goal ≥ 50% · Goal met');
    expect(spec.svg).toContain('Goal ≥ 1.30 · Goal met');
    expect(spec.svg).toContain('Goal ≥ 1.20 · Goal met');
    expect(spec.svg).toContain('system-ui');
    expect(spec.width).toBe(1200);
    expect(spec.height).toBeLessThan(1460);
    expect(spec.height).toBeGreaterThan(1100);
  });

  test('share-card goals clearly show when period metrics are below baseline', () => {
    const spec = buildMonthShareSvg({
      year: 2026,
      month: 9,
      weeks: [[1, 2, 3, 4, 5]],
      dayData: {},
      monthPnl: -100,
      winRate: '42.0',
      profitFactor: 0.9,
      avgWinLoss: '0.80',
      tradingDays: 5,
      asOfDate: new Date('2026-09-27T12:00:00'),
    });

    expect(spec.svg).toContain('Goal ≥ 50% · Below goal');
    expect(spec.svg).toContain('Goal ≥ 1.30 · Below goal');
    expect(spec.svg).toContain('Goal ≥ 1.20 · Below goal');
  });
});
