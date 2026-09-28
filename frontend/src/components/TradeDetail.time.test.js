import {
  calculateDefaultPlannedRisk,
  executionTimeETMinutes,
  groupDayTradesByTicker,
  formatExecutionTimeET,
  optimizeChartScreenshot,
} from './TradeDetail';

describe('TradeDetail session grouping', () => {
  test('orders ticker groups by their newest trade and trades newest-first inside each group', () => {
    const makeTrade = (id, ticker, timestampUtc, pnl) => ({
      id,
      ticker,
      date: '2026-09-25',
      side: 'LONG',
      instrument_type: 'STOCK',
      net_pnl: pnl,
      is_open: false,
      executions: [
        { action: 'BOT', qty: 1, price: 1, timestamp_utc: timestampUtc },
        { action: 'SOLD', qty: 1, price: 1, timestamp_utc: timestampUtc },
      ],
    });

    const groups = groupDayTradesByTicker([
      makeTrade(1, 'QCOM', '2026-09-25T14:22:00Z', 206),
      makeTrade(2, 'TSM', '2026-09-25T16:06:00Z', 66),
      makeTrade(3, 'QCOM', '2026-09-25T14:47:00Z', 443),
      makeTrade(4, 'WMT', '2026-09-25T19:43:00Z', -172),
    ]);

    expect(groups.map(group => group.ticker)).toEqual(['WMT', 'TSM', 'QCOM']);
    expect(groups.find(group => group.ticker === 'QCOM').trades.map(trade => trade.id)).toEqual([3, 1]);
    expect(groups.find(group => group.ticker === 'QCOM').netPnl).toBe(649);
  });
});

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


describe('TradeDetail chart screenshot optimization', () => {
  test('preserves a high-resolution desktop chart before stepping down quality', async () => {
    const originalCreateImageBitmap = global.createImageBitmap;
    const originalCreateElement = document.createElement.bind(document);

    const close = jest.fn();
    global.createImageBitmap = jest.fn().mockResolvedValue({
      width: 2560,
      height: 1440,
      close,
    });

    const drawImage = jest.fn();
    const canvas = {
      width: 0,
      height: 0,
      getContext: jest.fn(() => ({
        drawImage,
        imageSmoothingEnabled: false,
        imageSmoothingQuality: 'low',
      })),
      toBlob: jest.fn((callback, type, quality) => {
        callback(new Blob([new Uint8Array(1024)], { type }));
      }),
    };

    const createElementSpy = jest.spyOn(document, 'createElement').mockImplementation((tagName, options) => {
      if (tagName === 'canvas') return canvas;
      return originalCreateElement(tagName, options);
    });

    try {
      const source = new File([new Uint8Array(2 * 1024 * 1024)], 'chart.png', { type: 'image/png' });
      const optimized = await optimizeChartScreenshot(source);

      expect(canvas.width).toBe(2200);
      expect(canvas.height).toBe(1238);
      expect(canvas.toBlob).toHaveBeenCalledWith(expect.any(Function), 'image/webp', 1);
      expect(drawImage).toHaveBeenCalled();
      expect(optimized.name).toBe('trade-review.webp');
      expect(optimized.type).toBe('image/webp');
      expect(close).toHaveBeenCalled();
    } finally {
      createElementSpy.mockRestore();
      global.createImageBitmap = originalCreateImageBitmap;
    }
  });

  test('tries lower WebP quality before reducing chart dimensions', async () => {
    const originalCreateImageBitmap = global.createImageBitmap;
    const originalCreateElement = document.createElement.bind(document);

    global.createImageBitmap = jest.fn().mockResolvedValue({
      width: 2560,
      height: 1440,
      close: jest.fn(),
    });

    const calls = [];
    const canvas = {
      width: 0,
      height: 0,
      getContext: jest.fn(() => ({
        drawImage: jest.fn(),
        imageSmoothingEnabled: false,
        imageSmoothingQuality: 'low',
      })),
      toBlob: jest.fn((callback, type, quality) => {
        calls.push({ width: canvas.width, height: canvas.height, quality });
        const size = quality === 1 ? 600 * 1024 : 450 * 1024;
        callback(new Blob([new Uint8Array(size)], { type }));
      }),
    };

    const createElementSpy = jest.spyOn(document, 'createElement').mockImplementation((tagName, options) => {
      if (tagName === 'canvas') return canvas;
      return originalCreateElement(tagName, options);
    });

    try {
      const source = new File([new Uint8Array(2 * 1024 * 1024)], 'chart.png', { type: 'image/png' });
      const optimized = await optimizeChartScreenshot(source);

      expect(calls[0]).toMatchObject({ width: 2200, height: 1238, quality: 1 });
      expect(calls[1]).toMatchObject({ width: 2200, height: 1238, quality: 0.96 });
      expect(calls.some(call => call.width < 2200)).toBe(false);
      expect(optimized.size).toBeLessThanOrEqual(500 * 1024);
    } finally {
      createElementSpy.mockRestore();
      global.createImageBitmap = originalCreateImageBitmap;
    }
  });
});
