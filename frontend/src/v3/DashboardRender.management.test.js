import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';

import { TradeManagement } from './DashboardRender';


const KPIS = {
  total_trades: 47,
  exit_efficiency: 65,
  capture_n: 27,
  capture_winner_total: 27,
  capture_coverage_pct: 100,
  capture_confidence: 'RELIABLE',
  avg_mfe: 24.29,
  avg_mae: 12.56,
  median_mfe: 15,
  median_mae: 8,
  winner_median_mfe: 20,
  loser_median_mfe: 4,
  winner_median_mae: 6.8,
  loser_median_mae: 15.7,
  loser_mfe_le_5_pct: 55,
  loser_mfe_le_10_pct: 70,
  loser_mae_ge_25_pct: 20,
  winner_mae_le_20_pct: 90,
  excursion_n: 47,
  management_coverage_pct: 100,
  excursion_confidence: 'RELIABLE',
};

const EDGE = {
  total_trades: 47,
  hold_time: {
    winners_avg_min: 22.5,
    losers_avg_min: 8.8,
    winners_median_min: 14,
    losers_median_min: 8.5,
    winner_count: 27,
    loser_count: 19,
    sample_count: 46,
    coverage_pct: 97.9,
    overnight_excluded_count: 1,
    overnight_excluded: [
      { ticker: 'U', hold_minutes: 1220, net_pnl: -96.06 },
    ],
  },
};


test('Trade Management replaces Analysis window copy with Generate AI Analysis', () => {
  const onAiGenerate = jest.fn();

  render(
    <TradeManagement
      kpis={KPIS}
      edge={EDGE}
      range="7D"
      onRangeChange={() => {}}
      goals={{ exit_efficiency: 50 }}
      error=""
      ai={null}
      aiLoading={false}
      aiError=""
      onAiGenerate={onAiGenerate}
    />,
  );

  expect(screen.queryByText(/Analysis window/i)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Generate AI Analysis/i })).toBeVisible();
  expect(screen.getByText(/last 7 days · 47 closed trades/i)).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: /Generate AI Analysis/i }));
  expect(onAiGenerate).toHaveBeenCalledWith(false);
});


test('Trade Management shows evidence-locked AI diagnosis and re-run control', () => {
  const onAiGenerate = jest.fn();
  const ai = {
    headline: 'Early invalidation is the clearest improvement candidate.',
    diagnosis: 'Same-session losers are held for less time than winners, while adverse excursion separates losing trades.',
    next_focus: 'Review failed trades that cannot make +5% favorable progress.',
    focus_area: 'entry_quality_early_invalidation',
    evidence_locked: true,
    ai_provider: 'groq',
  };

  render(
    <TradeManagement
      kpis={KPIS}
      edge={EDGE}
      range="7D"
      onRangeChange={() => {}}
      goals={{ exit_efficiency: 50 }}
      error=""
      ai={ai}
      aiLoading={false}
      aiError=""
      onAiGenerate={onAiGenerate}
    />,
  );

  expect(screen.getByText('Early invalidation is the clearest improvement candidate.')).toBeVisible();
  expect(screen.getByText(ai.diagnosis)).toBeVisible();
  expect(screen.getByText(/Next focus · Review failed trades/i)).toBeVisible();
  expect(screen.getByText(/AI management review · 7D/i)).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: /Re-run/i }));
  expect(onAiGenerate).toHaveBeenCalledWith(true);
});


test('Trade Management preserves deterministic bottom line when AI is unavailable', () => {
  render(
    <TradeManagement
      kpis={KPIS}
      edge={EDGE}
      range="7D"
      onRangeChange={() => {}}
      goals={{ exit_efficiency: 50 }}
      error=""
      ai={{ unavailable: true, diagnosis: 'AI provider unavailable.' }}
      aiLoading={false}
      aiError="AI provider unavailable."
      onAiGenerate={() => {}}
    />,
  );

  expect(screen.getByText(/AI provider unavailable/i)).toBeVisible();
  expect(screen.getByRole('button', { name: /Generate AI Analysis/i })).toBeVisible();
  expect(screen.getByText(/currently covered excursion data|clearest improvement candidate|Winners average/i)).toBeVisible();
});
