import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

import LEDiagnosisReport from './LEDiagnosisReport';
import { leApi } from '../api';

jest.mock('../api', () => ({
  leApi: {
    getDiagnosis: jest.fn(),
    generateDiagnosis: jest.fn(),
  },
}));

const REPORT = {
  compliance_version: 'LE_PLAYBOOK_2026_09_v3',
  total_trades: 191,
  audited_trades: 191,
  missing_trades: 0,
  stale_snapshots: 0,
  coverage_pct: 100,
  overall: {
    trades: 191,
    wins: 101,
    losses: 90,
    win_rate: 52.9,
    net_pnl: 4340.34,
    avg_pnl: 22.72,
    gross_profit: 16239.24,
    gross_loss: 11898.9,
    profit_factor: 1.36,
  },
  most_profitable_cohort: {
    id: 'outside_day',
    label: 'Outside Day — both directional levels broken',
    trades: 43,
    wins: 30,
    losses: 13,
    win_rate: 69.8,
    net_pnl: 3927.29,
    avg_pnl: 91.33,
    profit_factor: 4.65,
    stable_sample: true,
  },
  biggest_verified_leak: {
    id: 'not_chop_hour',
    label: 'Not in Chop Hour?',
    trades: 19,
    net_pnl: -351.33,
    win_rate: 47.4,
    profit_factor: 0.63,
  },
  findings: [
    {
      kind: 'edge',
      title: 'Most profitable proven LE cohort',
      text: 'Outside Day produced strong results.',
    },
    {
      kind: 'manual',
      title: 'User-confirmed LE evidence',
      text: '2 audited trades contain authoritative manual LE tags.',
    },
  ],
  cohorts: [
    {
      id: 'outside_day',
      label: 'Outside Day — both directional levels broken',
      trades: 43,
      win_rate: 69.8,
      net_pnl: 3927.29,
      avg_pnl: 91.33,
      profit_factor: 4.65,
      stable_sample: true,
    },
  ],
  manual_evidence: {
    trades: 2,
    override_count: 4,
    conflict_count: 1,
    authoritative_source: 'USER_MANUAL',
  },
  learning_core: {
    learning_version: 'LE_LEARNING_2026_09_v1',
    headline: 'Manual setup evidence is being learned, but no setup has enough stable out-of-sample evidence for promotion yet.',
    validated_setup_count: 0,
    high_priority_detector_count: 1,
    setup_edges: [
      {
        id: 'outside_day',
        label: 'Outside Day',
        stage: 'DISCOVERY',
        reason: 'Need at least 8 trades before the setup leaves discovery.',
        promotion_ready: false,
        overall: {
          trades: 2,
          trading_days: 2,
          net_pnl: 648.63,
          avg_pnl: 324.32,
          profit_factor: null,
        },
        early_sample: { net_pnl: 300 },
        recent_sample: { net_pnl: 348.63 },
      },
    ],
    detector_calibration: [
      {
        id: 'market_sign',
        label: 'Market Sign?',
        priority: 'HIGH',
        labeled: 5,
        system_agreements: 0,
        system_conflicts: 5,
        system_unknown_resolved: 0,
        agreement_rate_pct: 0,
        priority_reason: 'Repeated user-vs-system disagreements indicate a detector gap.',
      },
    ],
    note: 'Manual LE tags are treated as ground truth labels.',
  },
  user_confirmed_setups: [
    {
      id: 'outside_day',
      label: 'Outside Day',
      trades: 2,
      win_rate: 100,
      net_pnl: 648.63,
      avg_pnl: 324.32,
      profit_factor: null,
      stable_sample: false,
      evidence_source: 'USER_MANUAL',
    },
  ],
  rules: [
    {
      id: 'not_chop_hour',
      label: 'Not in Chop Hour?',
      coverage_pct: 100,
      pass: { trades: 172, net_pnl: 4691.67 },
      fail: { trades: 19, net_pnl: -351.33, profit_factor: 0.63 },
      unknown: { trades: 0, net_pnl: 0 },
      user_backed_trades: 1,
      user_system_conflicts: 1,
    },
  ],
  evidence_gaps: [
    { id: 'vix_checked', label: 'VIX Checked?', unknown_trades: 191, unknown_pct: 100 },
  ],
  recent_trades: [
    {
      trade_group: 'g1',
      date: '2026-09-25',
      ticker: 'SPY',
      side: 'LONG',
      net_pnl: 125.5,
      classification: 'LE_VIOLATION',
      classification_label: 'LE violation found',
      score: { passed: 7, failed: 1, unknown: 5 },
      failed_rule_ids: ['not_chop_hour'],
      manual_le_evidence: {
        override_count: 1,
        conflict_count: 1,
        setup_tags: ['Outside Day'],
        recognized_tags: [
          { id: 7, tag_type: 'setup', tag_value: 'Outside Day', source: 'manual' },
        ],
      },
    },
  ],
  note: 'LE diagnosis is deterministic.',
};

test('renders journal-wide LE diagnosis and automation status', async () => {
  leApi.getDiagnosis.mockResolvedValue({ data: REPORT });

  render(<LEDiagnosisReport accountId={4} dateFrom="" dateTo="" />);

  expect(await screen.findByText('Journal-wide LE Smart Diagnosis')).toBeVisible();
  expect(screen.getByText('191 / 191')).toBeVisible();
  expect(screen.getByText('Outside Day — both directional levels broken')).toBeVisible();
  expect(screen.getByText('Rule Performance Matrix')).toBeVisible();
  expect(screen.getByText('LE Learning Core')).toBeVisible();
  expect(screen.getByText('LE Detector Calibration')).toBeVisible();
  expect(screen.getByText('DISCOVERY')).toBeVisible();
  expect(screen.getByText('HIGH')).toBeVisible();
  expect(screen.getByText('User-Confirmed LE Setups')).toBeVisible();
  expect(screen.getByText('User-backed LE evidence')).toBeVisible();
  expect(screen.getAllByText('Outside Day').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('VIX Checked?')).toBeVisible();
});

test('Generate / Refresh calls diagnosis generator and replaces report', async () => {
  leApi.getDiagnosis.mockResolvedValue({ data: REPORT });
  leApi.generateDiagnosis.mockResolvedValue({
    data: {
      processed: 2,
      attempted: 2,
      errors: [],
      complete: true,
      report: REPORT,
    },
  });

  render(<LEDiagnosisReport accountId={4} dateFrom="2026-09-01" dateTo="2026-09-25" />);

  const button = await screen.findByRole('button', { name: 'Generate / Refresh Diagnosis' });
  fireEvent.click(button);

  await waitFor(() => expect(leApi.generateDiagnosis).toHaveBeenCalledWith({
    account_id: 4,
    date_from: '2026-09-01',
    date_to: '2026-09-25',
    force: false,
  }));

  expect(await screen.findByText(/processed 2 of 2 stale\/missing trade/)).toBeVisible();
});
