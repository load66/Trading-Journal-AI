import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

import LEReview from './LEReview';
import { tradesApi } from '../api';

jest.mock('../api', () => ({
  tradesApi: {
    refreshLeCompliance: jest.fn(),
    addTag: jest.fn(),
    updateAnalysis: jest.fn(),
  },
}));

jest.mock('./TradingChart', () => () => <div data-testid="trading-chart">chart</div>);

const CHECKS = [
  ['level_broken', 'Level Broken?', 'pass'],
  ['trend_established', 'Trend Established?', 'pass'],
  ['ema_aligned', 'EMA Aligned?', 'pass'],
  ['ema_snug', 'EMA Snug?', 'pass'],
  ['market_sign', 'Market Sign?', 'pass'],
  ['flag_forming', 'Flag Forming?', 'unknown'],
  ['hard_stop_set', 'Hard Stop Set?', 'unknown'],
  ['size_correct', 'Size Correct?', 'unknown'],
  ['risk_reward', 'R:R >= 2:1?', 'unknown'],
  ['trade_count_ok', 'Trade Count OK?', 'pass'],
  ['not_chasing', 'Not Chasing?', 'unknown'],
  ['not_chop_hour', 'Not in Chop Hour?', 'pass'],
  ['vix_checked', 'VIX Checked?', 'unknown'],
].map(([id, label, status]) => ({
  id,
  label,
  status,
  detail: status === 'pass' ? 'Verified.' : 'Evidence not recorded.',
}));

const RESPONSE = {
  available: true,
  ruleset_version: 'LE_2026_09_v7_MARKET_SIGN',
  compliance_cached: true,
  compliance: {
    classification: 'INCOMPLETE_EVIDENCE',
    classification_label: 'Incomplete evidence',
    score: {
      passed: 7,
      failed: 0,
      unknown: 6,
      evaluated: 7,
      total: 13,
      evaluated_pass_pct: 100,
      coverage_pct: 53.8,
    },
    checks: CHECKS,
    extra_findings: [],
  },
  evidence: {
    levels: {},
    level_meta: {},
    level_breaks_before_entry: {},
    entry_checks: {},
    management_10m8ema: {},
    market_sign: {},
    evidence_quality: { level: 'High', completeness_pct: 100 },
  },
  auto_tags: [],
  data_warnings: [],
  ai: {
    available: false,
    suggested_tags: [],
    insufficient_evidence: [],
  },
};

test('LE Review renders the deterministic 13-point compliance audit and caches it', async () => {
  tradesApi.refreshLeCompliance.mockResolvedValue({ data: RESPONSE });

  render(
    <LEReview
      trade={{ trade_group: 'g1', ticker: 'SPY', date: '2026-09-25', side: 'LONG', executions: [] }}
      analysis={{}}
      tags={[]}
    />
  );

  await waitFor(() => expect(tradesApi.refreshLeCompliance).toHaveBeenCalledWith('g1'));

  expect(await screen.findByText('13-Point LE Compliance')).toBeVisible();
  expect(screen.getByText('Incomplete evidence')).toBeVisible();
  expect(screen.getByText('53.8%')).toBeVisible();
  expect(screen.getByText('Level Broken?')).toBeVisible();
  expect(screen.getByText('Hard Stop Set?')).toBeVisible();
  expect(screen.getByText('VIX Checked?')).toBeVisible();
  expect(screen.getAllByText('Unverified').length).toBeGreaterThan(0);
});
