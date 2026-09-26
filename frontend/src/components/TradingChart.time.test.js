import { buildExecutionMarkerGroups, execToTs, executionTimeLabelET, toTs } from './TradingChart';

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

  test('execution labels preserve the broker-reported minute in ET', () => {
    expect(executionTimeLabelET('2026-09-25', '09:47:00')).toBe('10:47 ET');
    expect(executionTimeLabelET('2026-09-25', '10:11:00')).toBe('11:11 ET');
  });

  test('5-minute marker grouping keeps exact trim minutes and all fill prices', () => {
    const fills = [
      { date: '2026-09-25', time: '09:47:00', action: 'BOT', qty: 5, price: 0.67 },
      { date: '2026-09-25', time: '09:47:00', action: 'BOT', qty: 5, price: 0.67 },
      { date: '2026-09-25', time: '09:48:00', action: 'SOLD', qty: 2, price: 0.85 },
      { date: '2026-09-25', time: '09:48:00', action: 'SOLD', qty: 2, price: 0.75 },
      { date: '2026-09-25', time: '09:49:00', action: 'SOLD', qty: 1, price: 0.95 },
    ];
    const markers = buildExecutionMarkerGroups(fills, '2026-09-25', 5);

    expect(markers).toHaveLength(2);
    expect(markers[0].time).toBe(utcWallTs('2026-09-25', '10:45'));
    expect(markers[0].text).toBe('10:47 ET · 5@0.67 ×2');
    expect(markers[1].time).toBe(utcWallTs('2026-09-25', '10:45'));
    expect(markers[1].text).toBe('10:48 ET · 2@0.85 + 2@0.75 | 10:49 ET · 1@0.95');
  });

  test('1-minute markers use the exact broker-supported execution minute', () => {
    const fills = [
      { date: '2026-09-25', time: '09:54:00', action: 'SOLD', qty: 1, price: 0.95 },
      { date: '2026-09-25', time: '10:03:00', action: 'SOLD', qty: 1, price: 1.23 },
    ];
    const markers = buildExecutionMarkerGroups(fills, '2026-09-25', 1);

    expect(markers.map(m => m.time)).toEqual([
      utcWallTs('2026-09-25', '10:54'),
      utcWallTs('2026-09-25', '11:03'),
    ]);
  });

  test('canonical timestamp wins over legacy local clock fields', () => {
    const fills = [{
      date: '2026-09-25',
      time: '08:00:00',
      timestamp_utc: '2026-09-25T14:48:00Z',
      source_timezone: 'America/Chicago',
      timestamp_precision: 'minute',
      action: 'SOLD',
      qty: 2,
      price: 0.85,
    }];
    const markers = buildExecutionMarkerGroups(fills, '2026-09-25', 1);
    expect(markers).toHaveLength(1);
    expect(markers[0].time).toBe(utcWallTs('2026-09-25', '10:48'));
    expect(markers[0].text).toBe('10:48 ET · 2@0.85');
  });

  test('second-precision canonical fills keep seconds in the label', () => {
    const fills = [{
      date: '2026-09-25',
      time: '10:11:22',
      timestamp_utc: '2026-09-25T15:11:22Z',
      source_timezone: 'America/Chicago',
      timestamp_precision: 'second',
      action: 'SOLD',
      qty: 1,
      price: 2.11,
    }];
    const markers = buildExecutionMarkerGroups(fills, '2026-09-25', 1);
    expect(markers[0].text).toBe('11:11:22 ET · 1@2.11');
  });

});
