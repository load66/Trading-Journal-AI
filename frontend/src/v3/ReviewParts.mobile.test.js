import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

import { DayTrades } from './ReviewParts';


const TRADES = [
  {
    id: 1,
    trade_group: 'g1',
    ticker: 'WMT',
    side: 'LONG',
    instrument_type: 'OPTION',
    net_pnl: -96.06,
    r_multiple: -0.72,
    executions: [
      { side: 'BUY', quantity: 1, price: 2.0, time: '10:00:00' },
      { side: 'SELL', quantity: 1, price: 1.04, time: '10:05:00' },
    ],
  },
];


test('DayTrades provides a mobile card representation without removing the desktop table', () => {
  const onOpen = jest.fn();
  render(
    <DayTrades
      trades={TRADES}
      gradeMap={{
        g1: {
          grade: 'D',
          one_line: 'Averaging down weakened the trade management.',
        },
      }}
      loading={false}
      onOpen={onOpen}
    />,
  );

  expect(screen.getByRole('table')).toBeInTheDocument();
  expect(screen.getByRole('list', { name: 'Trades for this day' })).toBeInTheDocument();

  const card = screen.getByRole('listitem', { name: /Open WMT trade/i });
  expect(card).toHaveClass('v3-trade-card');
  expect(screen.getByText('Averaging down weakened the trade management.')).toBeVisible();

  fireEvent.click(card);
  expect(onOpen).toHaveBeenCalledWith(TRADES[0], TRADES);
});
