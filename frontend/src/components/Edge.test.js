import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';
import { HoldTime } from './Edge';


test('Holding Behavior shows the excluded overnight contract without changing its averages', () => {
  render(
    <HoldTime data={{
      winners_avg_min: 22.5,
      losers_avg_min: 8.8,
      overnight_excluded_count: 1,
      overnight_excluded: [{
        trade_group: 'u-overnight',
        ticker: 'U',
        option_expiry: '2026-10-16',
        option_strike: 45,
        option_type: 'CALL',
        hold_minutes: 1220,
        net_pnl: 500,
      }],
    }} />,
  );

  expect(screen.getByText('22.5 min')).toBeVisible();
  expect(screen.getByText('8.8 min')).toBeVisible();
  expect(screen.getByText('1 overnight trade excluded')).toBeVisible();
  expect(screen.getByText(/U Oct 16 \$45 Call/)).toHaveTextContent('U Oct 16 $45 Call · 1,220 min · +$500');
});


test('Holding Behavior omits the indicator when no overnight trades were excluded', () => {
  render(<HoldTime data={{ winners_avg_min: 262, losers_avg_min: 8.8, overnight_excluded: [] }} />);

  expect(screen.queryByText(/overnight.*excluded/i)).not.toBeInTheDocument();
});
