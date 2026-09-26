import { execToTs, toTs } from './TradingChart';

const utcWallTs = (dateStr, hhmm) => {
  const [h, m] = hhmm.split(':').map(Number);
  const [y, mo, d] = dateStr.split('-').map(Number);
  return Math.floor(Date.UTC(y, mo - 1, d, h, m, 0) / 1000);
};

describe('TradingChart timezone integrity', () => {
  test('market bars project to 10:00 ET in summer and winter', () => {
    expect(toTs('2026-09-25T14:00:00Z')).toBe(utcWallTs('2026-09-25', '10:00'));
    expect(toTs('2026-01-15T15:00:00Z')).toBe(utcWallTs('2026-01-15', '10:00'));
  });

  test('Schwab Central execution markers align to Eastern chart time across DST', () => {
    expect(execToTs('2026-09-25', '09:00:00', 5)).toBe(utcWallTs('2026-09-25', '10:00'));
    expect(execToTs('2026-01-15', '09:00:00', 5)).toBe(utcWallTs('2026-01-15', '10:00'));
  });
});
