import {
  calculateDefaultPlannedRisk,
  executionTimeETMinutes,
  formatExecutionTimeET,
  optimizeChartScreenshot,
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
      expect(canvas.toBlob).toHaveBeenCalledWith(expect.any(Function), 'image/webp', 0.92);
      expect(drawImage).toHaveBeenCalled();
      expect(optimized.name).toBe('trade-review.webp');
      expect(optimized.type).toBe('image/webp');
      expect(close).toHaveBeenCalled();
    } finally {
      createElementSpy.mockRestore();
      global.createImageBitmap = originalCreateImageBitmap;
    }
  });
});
