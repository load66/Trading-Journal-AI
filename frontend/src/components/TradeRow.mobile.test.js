import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

import TradeRow from './TradeRow';


const TRADE = {
  id: 25,
  date: '2026-09-25',
  ticker: 'WMT',
  side: 'LONG',
  instrument_type: 'OPTION',
  strategy: 'LE E-Entry — 10m 8 EMA Retest + VWAP Level bounce',
  setup: null,
  net_pnl: -172.10,
  pl_pct: -17.3,
  realized_r: -0.66,
  stop_loss: 1,
  target_price: 2.18,
  mfe_pct: 9.7,
  mae_pct: 24.2,
  exit_efficiency: null,
  executions: [],
};


test('TradeRow renders a compact mobile card with key review metrics', () => {
  const onOpenDetail = jest.fn();

  render(
    <TradeRow
      trade={TRADE}
      openTime="14:43:00"
      onOpenDetail={onOpenDetail}
      mobile
    />,
  );

  const card = screen.getByRole('button', { name: /Open WMT trade from 2026-09-25/i });
  expect(card).toHaveClass('trade-mobile-card');

  expect(screen.getByText('WMT')).toBeInTheDocument();
  expect(screen.getByText('2026-09-25 · 14:43')).toBeInTheDocument();
  expect(screen.getByText('-$172.10')).toBeInTheDocument();
  expect(screen.getByText('-17.3%')).toBeInTheDocument();
  expect(screen.getByText('1:2.18')).toBeInTheDocument();
  expect(screen.getByText('-0.66R')).toBeInTheDocument();
  expect(screen.getByText('+9.7')).toBeInTheDocument();
  expect(screen.getByText('-24.2')).toBeInTheDocument();
  expect(screen.getByText('LE E-Entry — 10m 8 EMA Retest + VWAP Level bounce')).toBeInTheDocument();

  fireEvent.click(card);
  expect(onOpenDetail).toHaveBeenCalledWith(TRADE);
});


test('TradeRow mobile card supports keyboard activation', () => {
  const onOpenDetail = jest.fn();

  render(
    <TradeRow
      trade={{ ...TRADE, id: 26, ticker: 'QCOM', net_pnl: 442.74, pl_pct: 66.1 }}
      openTime="09:47:00"
      onOpenDetail={onOpenDetail}
      mobile
    />,
  );

  const card = screen.getByRole('button', { name: /Open QCOM trade/i });
  fireEvent.keyDown(card, { key: 'Enter' });
  expect(onOpenDetail).toHaveBeenCalledTimes(1);
});
