import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

import { DayCurve } from './ReviewParts';

const TRADES = [
  {
    id: 1,
    trade_group: 'g1',
    ticker: 'SPY',
    side: 'LONG',
    instrument_type: 'OPTION',
    strategy: 'LE level retest',
    net_pnl: 180,
    date: '2026-09-25',
    executions: [
      { action: 'BOT', qty: 2, price: 2.0, timestamp_utc: '2026-09-25T13:45:00Z' },
      { action: 'SOLD', qty: 2, price: 2.9, timestamp_utc: '2026-09-25T14:15:00Z' },
    ],
  },
  {
    id: 2,
    trade_group: 'g2',
    ticker: 'QQQ',
    side: 'LONG',
    instrument_type: 'OPTION',
    net_pnl: -60,
    date: '2026-09-25',
    executions: [
      { action: 'BOT', qty: 1, price: 3.0, timestamp_utc: '2026-09-25T15:30:00Z' },
      { action: 'SOLD', qty: 1, price: 2.4, timestamp_utc: '2026-09-25T16:00:00Z' },
    ],
  },
];

test('DayCurve shows professional session landmarks, LE windows, and exact trade interaction', () => {
  const onPick = jest.fn();

  render(<DayCurve trades={TRADES} onPick={onPick} />);

  expect(screen.getByText('Peak')).toBeVisible();
  expect(screen.getByText('Giveback')).toBeVisible();
  expect(screen.getByText('Close')).toBeVisible();

  expect(screen.getByText('PRIME')).toBeVisible();
  expect(screen.getByText('CHOP')).toBeVisible();
  expect(screen.getByText('AFTERNOON')).toBeVisible();
  expect(screen.getByText('HARD CLOSE')).toBeVisible();

  const spyMark = screen.getByRole('button', { name: /SPY Long closed/i });
  expect(spyMark).toBeVisible();

  fireEvent.pointerEnter(spyMark);
  expect(screen.getByText(/Running \+\$180\.00/)).toBeVisible();
  expect(screen.getByText('LE level retest')).toBeVisible();

  fireEvent.click(spyMark);
  expect(onPick).toHaveBeenCalledWith(TRADES[0]);
});
