// Integration checks: every feature stays reachable from the app shell, and the
// Settings library behaves. The api module is mocked, so no test reaches a backend.
import { render, screen, within, fireEvent, waitFor, act } from '@testing-library/react';
import App from './App';
import { accountsApi, tradesApi, libraryApi, kpisApi, goalsApi, smokingGunLibraryApi, excursionApi, __restoreMocks } from './api';

jest.mock('./api', () => {
  const ok = (data) => Promise.resolve({ data });
  // CRA's jest preset resets mocks before each test, so every mock keeps its
  // implementation and __restoreMocks puts it back in beforeEach.
  const all = [];
  const fn = (impl) => { const f = jest.fn(impl); f.impl = impl; all.push(f); return f; };
  const TRADE = {
    id: 101, account_id: 1, trade_group: '9/10/26_TSLA_STOCK_1', date: '2026-09-10', ticker: 'TSLA',
    instrument_type: 'STOCK', side: 'LONG', gross_pnl: 195, net_pnl: 193.45, commissions: 1.55,
    executions: [
      { date: '2026-09-10', time: '09:54:10', action: 'BOT', qty: 200, price: 366.09, commission: 0 },
      { date: '2026-09-10', time: '10:08:24', action: 'SOLD', qty: 200, price: 367.07, commission: 1.55 },
    ],
    setup: 'VWAP Reclaim', setup_grade: 'B', setup_notes: null, setup_source: 'manual',
    mfe_pct: 0.85, mae_pct: -0.27, exit_efficiency: 31.25, strategy: 'Test Strategy', r_multiple: 0.41,
  };
  const TRADE_2 = { ...TRADE, id: 102, trade_group: '9/10/26_META_STOCK_1', ticker: 'META', net_pnl: -50 };
  // 300 bought, 100 sold: 200 still open.
  const OPEN_TRADE = {
    id: 103, account_id: 1, trade_group: '9/09/26_GOOG_STOCK_1', date: '2026-09-09', ticker: 'GOOG',
    instrument_type: 'STOCK', side: 'LONG', net_pnl: null, gross_pnl: null, commissions: 0,
    executions: [
      { date: '2026-09-09', time: '09:40:00', action: 'BOT', qty: 300, price: 100, commission: 0 },
      { date: '2026-09-09', time: '11:00:00', action: 'SOLD', qty: 100, price: 105, commission: 0 },
    ],
  };
  const ACCOUNTS = [
    { id: 1, name: 'Day Trading', type: 'day_trading', color: '#6366f1', broker: 'Schwab' },
    { id: 2, name: 'Swing', type: 'swing', color: '#6366f1', broker: 'Schwab' },
  ];
  const SMOKING_GUN_REPORTS = [
    {
      id: 22, account_id: 1, title: 'September Smoking Gun',
      date_from: '2026-09-01', date_to: '2026-09-30',
      generated_at: '2026-09-26 12:00:00', trade_count: 191,
      net_pnl: 4340.34, primary_edge: 'Patience over speed',
      primary_leak: 'Averaging down', report_version: '1',
      filters: { tickers: ['SPY', 'QQQ'] }, is_stale: true,
      stale_reason: 'source-data-changed', status: 'complete',
    },
    {
      id: 21, account_id: 1, title: 'August Smoking Gun',
      date_from: '2026-08-01', date_to: '2026-08-31',
      generated_at: '2026-09-01 08:00:00', trade_count: 75,
      net_pnl: 1586.28, primary_edge: 'Patient holds',
      primary_leak: 'Micro-scalping', report_version: '1',
      filters: {}, is_stale: false, stale_reason: null, status: 'complete',
    },
  ];
  const SMOKING_GUN_DETAIL = {
    ...SMOKING_GUN_REPORTS[0],
    analytics_engine_version: '2026.09.26.1',
    behavior_version: '2026.09.26.1',
    data_fingerprint: 'a'.repeat(64),
    source_metrics: {
      meta: { timestamp_coverage: 100, behavior_counterfactual_note: 'Impacts overlap.' },
      scoreboard: {
        net_pnl: 4340.34, gross_pnl: 5485, fees: 1144.66, win_rate: 52.9,
        profit_factor: 1.36, avg_winner: 160.78, avg_loser: 132.21,
        reward_risk: 1.22, max_drawdown: -1626.32, active_days: 28,
        best_day: { date: '2026-09-03', pnl: 746.06 },
        worst_day: { date: '2026-09-09', pnl: -1626.32 },
      },
      two_traders: {
        disciplined: { trade_count: 136, total_pnl: 5108.85, win_rate: 56.6, avg_pnl: 37.57 },
        destructive: { trade_count: 55, total_pnl: -768.51, win_rate: 43.6, avg_pnl: -13.97 },
      },
      hold_time: [
        { bucket: 'Under 30 sec', trade_count: 1, total_pnl: -10.03, win_rate: 0, avg_pnl: -10.03 },
        { bucket: '20-30min', trade_count: 15, total_pnl: 1136.68, win_rate: 73.3, avg_pnl: 75.78 },
      ],
      position_size: {
        options: [
          { bucket: '1-3', trade_count: 89, total_pnl: 1516.50, win_rate: 48.3, avg_pnl: 17.04 },
          { bucket: '11-15', trade_count: 4, total_pnl: -25.41, win_rate: 50, avg_pnl: -6.35 },
        ],
        shares_by_notional: [],
        cross_reference: {
          small_size_long_hold: { trade_count: 29, total_pnl: 2179.93, win_rate: 65.5, avg_pnl: 75.17 },
          big_size_short_hold: { trade_count: 1, total_pnl: -203.61, win_rate: 0, avg_pnl: -203.61 },
        },
      },
      daily_pnl: [
        { date: '2026-09-09', options_pnl: -1626.32, shares_pnl: 0, futures_pnl: 0, total_pnl: -1626.32, running_total: 2847.13, trade_count: 7, blow_up: true },
      ],
      daily_stop_model: {
        avg_loss: 132.21,
        levels: [{ stop: 500, adjusted_pnl: 5241.40, actual_pnl: 4340.34, saved: 901.06, breach_count: 3, breaches: [] }],
      },
      ticker_ranking: [
        { ticker: 'META', trade_count: 14, total_pnl: 1774.61, win_rate: 71.4, dollars_per_trade: 126.76, label: 'EDGE', sample_quality: 'established' },
        { ticker: 'GLD', trade_count: 4, total_pnl: 588.37, win_rate: 100, dollars_per_trade: 147.09, label: 'MARGINAL', sample_quality: 'thin' },
      ],
      behavior: {
        ranked_flaws: [
          { name: 'Averaging down / adding to losers', trade_count: 12, pnl: -1335.79, dollar_impact: 1335.79, pnl_if_eliminated: 5676.13, evidence: 'Confirmed leak' },
        ],
        evidence: [
          { name: 'Revenge re-entry', trade_count: 23, pnl: 546.27, evidence_status: 'Not supported as a leak' },
        ],
        averaging_down: {
          averaged_down: { trade_count: 12, total_pnl: -1335.79, win_rate: 33.3, avg_pnl: -111.32 },
          clean_entries: { trade_count: 124, total_pnl: 2926.90, win_rate: 52.4, avg_pnl: 23.60 },
        },
      },
      time_analysis: {
        first_10_minutes: { trade_count: 7, total_pnl: -480.28, win_rate: 28.6, avg_pnl: -68.61 },
        rest_of_day: { trade_count: 184, total_pnl: 4820.62, win_rate: 53.8, avg_pnl: 26.20 },
        half_hour_blocks: [{ bucket: '08:30', trade_count: 28, total_pnl: 623.49, win_rate: 46.4, avg_pnl: 22.27 }],
        day_of_week: [{ bucket: 'Thu', trade_count: 47, total_pnl: 2118, win_rate: 60, avg_pnl: 45.06 }],
      },
      projections: {
        proven_daily_edge: 182.46,
        rates: [{ rate: 1, daily_edge: 182.46, remaining_weekdays: 67, gross_earnings: 12224.75, current_drawdown: -133.11, net_after_current_drawdown: 12091.64, months_to_recover: 0.04 }],
      },
      trade_ledger: [],
    },
    diagnosis: {
      headline: 'Patience is the edge; averaging down is the clearest leak.',
      edge: {
        where_it_lives: ['20+ minute holds with controlled size.'],
        where_it_dies: ['Averaging down and micro-scalping.'],
      },
      limitations: ['Post-exit opportunity cost is not available without market data.'],
    },
    action_plan: [
      { priority: 1, rule: 'No averaging down on long options.', why: 'Observed cohort lost $1,335.79.' },
      { priority: 2, rule: 'Test a $500 daily stop prospectively.', why: 'Best in-sample modeled candidate.' },
    ],
  };

  const LIBRARY = {
    strategies: [
      { name: 'VWAP Cross', description: 'Reclaim of VWAP', trades: 10, aliases: [], },
      { name: 'Continuation RS', description: null, trades: 2, aliases: [], },
    ],
    sources: [
      { name: 'Scanner', description: null, trades: 8, aliases: [] },
      { name: 'OneOption', description: null, trades: 1, aliases: [] },
    ],
    tags: {
      mistake: [{ name: 'Sized too big', description: null, trades: 6, aliases: [] }],
      execution: [{ name: 'Scaled out', description: null, trades: 20, aliases: [] }],
      setup: [], emotion: [], outcome: [],
    },
    tag_types: ['setup', 'execution', 'mistake', 'emotion', 'outcome'],
  };
  // Anything not listed resolves with an empty object, which every page treats as "no data".
  const withDefault = (methods) => new Proxy(methods, {
    get: (target, key) => (key in target ? target[key] : fn(() => ok({}))),
  });
  return {
    __restoreMocks: () => all.forEach(f => f.mockImplementation(f.impl)),
    API_BASE: 'http://mocked.invalid',
    accountsApi: withDefault({
      list: fn(() => ok(ACCOUNTS)),
      create: fn(() => ok({ id: 3 })),
      update: fn(() => ok({})),
    }),
    tradesApi: withDefault({
      list: fn((params = {}) => ok(params.open_only ? [OPEN_TRADE] : [TRADE, TRADE_2])),
      addExecution: fn(() => ok({})),
      getAnalysis: fn(() => ok(null)),
      updateAnalysis: fn((tradeGroup, payload) => ok(payload)),
      uploadChartScreenshot: fn(() => ok({ chart_screenshot_path: 'trade-review/test/chart.png' })),
      getChartScreenshot: fn(() => ok(new Blob(['image'], { type: 'image/png' }))),
      deleteChartScreenshot: fn(() => ok({ deleted: true })),
      getLeReview: fn(() => ok({ available: false, reason: 'No LE review in tests', data_warnings: [] })),
      getLeLevels: fn(() => ok({ available: true, levels: {}, feed: 'sip', warnings: [] })),
      getAnalysisOptions: fn(() => ok({ strategies: [], idea_sources: [] })),
      listCustomSetups: fn(() => ok([])),
    }),
    kpisApi: withDefault({ get: fn(() => ok({ total_net_pnl: 100, daily_pnl: [], by_strategy: [] })) }),
    importApi: withDefault({}),
    diaryApi: withDefault({ list: fn(() => ok([])) }),
    chartApi: withDefault({ get: fn(() => ok({ bars: [], warning: 'No chart data in tests' })) }),
    insightsApi: withDefault({}),
    calendarApi: withDefault({ get: fn(() => ok({ days: [] })) }),
    brainApi: withDefault({}),
    dailySummaryApi: withDefault({ get: fn(() => ok({ trades: [] })) }),
    syncApi: withDefault({}),
    goalsApi: withDefault({ get: fn(() => ok({ win_rate: 65 })), put: fn(() => ok({ win_rate: 65 })) }),
    reportsApi: withDefault({ get: fn(() => Promise.reject(new Error('no reports in tests'))) }),
    smokingGunApi: withDefault({ get: fn(() => ok({ has_data: false })), diagnose: fn(() => ok({ diagnosis: null })) }),
    smokingGunLibraryApi: withDefault({
      list: fn(() => ok(SMOKING_GUN_REPORTS)),
      get: fn(() => ok(SMOKING_GUN_DETAIL)),
      remove: fn(() => ok({})),
      downloadHtml: fn(() => ok(new Blob(['html'], { type: 'text/html' }))),
      downloadLedger: fn(() => ok(new Blob(['csv'], { type: 'text/csv' }))),
    }),
    edgeReportApi: withDefault({}),
    excursionApi: withDefault({
      calculate: fn(() => ok({ computed: 0 })),
      calculateRange: fn(() => ok({ computed: 0, skipped: 0 })),
    }),
    weeklySummaryApi: withDefault({}),
    yearlyKpisApi: withDefault({ get: fn(() => ok({ months: [] })) }),
    libraryApi: withDefault({
      list: fn(() => ok(LIBRARY)),
      merge: fn(() => ok({ moved: 2, target: 'VWAP Cross', trades: 12 })),
      remove: fn(() => ok({ affected: 1, reassigned_to: null })),
      update: fn(() => ok({ name: 'VWAP Cross', trades: 10 })),
      create: fn(() => ok({ name: 'New' })),
    }),
  };
});

beforeAll(() => {
  // jsdom gaps that charts and menus touch.
  global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  Element.prototype.scrollIntoView = jest.fn();
  window.alert = jest.fn();
  URL.createObjectURL = jest.fn(() => 'blob:trade-chart-review');
  URL.revokeObjectURL = jest.fn();
});

beforeEach(() => {
  __restoreMocks();
  sessionStorage.clear();
});

async function renderApp() {
  const view = render(<App />);
  // Accounts load on mount; waiting for them lets the first render settle.
  await waitFor(() => expect(accountsApi.list).toHaveBeenCalled());
  await act(async () => {});
  return view;
}

const nav = () => screen.getByRole('navigation', { name: 'Main' });

test('header keeps every page, Settings, Import, Add Trade and a labeled Brain entry visible', async () => {
  await renderApp();
  for (const label of ['Dashboard', 'Trade View', 'Calendar', 'Day Review', 'Reports', 'Diary', 'Help', 'Settings']) {
    expect(within(nav()).getByRole('button', { name: label })).toBeVisible();
  }
  const banner = screen.getByRole('banner');
  expect(within(banner).getByRole('button', { name: /^Import$/ })).toBeVisible();
  expect(within(banner).getByRole('button', { name: /Add Trade/ })).toBeVisible();
  expect(within(banner).getByRole('button', { name: /Brain/ })).toBeVisible();
});

test('refresh restores the current journal location instead of returning to Dashboard', async () => {
  const first = await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  expect(within(nav()).getByRole('button', { name: 'Trade View' })).toHaveAttribute('aria-current', 'page');

  await waitFor(() => {
    const saved = JSON.parse(sessionStorage.getItem('trading-journal:navigation-state-v1'));
    expect(saved.page).toBe('trades');
  });

  first.unmount();
  accountsApi.list.mockClear();
  const second = render(<App />);
  await waitFor(() => expect(accountsApi.list).toHaveBeenCalled());
  expect(within(nav()).getByRole('button', { name: 'Trade View' })).toHaveAttribute('aria-current', 'page');
  expect(screen.getByRole('heading', { name: 'Trade View' })).toBeVisible();
  second.unmount();
});

test('Brain opens from the header as a dialog and closes on Escape', async () => {
  await renderApp();
  const trigger = within(screen.getByRole('banner')).getByRole('button', { name: /Brain/ });
  fireEvent.click(trigger);
  const dialog = await screen.findByRole('dialog', { name: /Brain/i });
  expect(dialog).toBeVisible();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog', { name: /Brain/i })).not.toBeInTheDocument());
});

test('account menu keeps selection, rename and new-account controls', async () => {
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  const menu = screen.getByRole('menu', { name: 'Accounts' });
  expect(within(menu).getByRole('menuitemradio', { name: 'All Accounts' })).toBeInTheDocument();
  expect(within(menu).getByRole('menuitemradio', { name: /Day Trading/ })).toBeInTheDocument();
  expect(within(menu).getByRole('button', { name: /New Account/ })).toBeInTheDocument();

  fireEvent.click(within(menu).getByRole('button', { name: 'Rename Day Trading' }));
  const input = within(menu).getByRole('textbox', { name: 'New name for Day Trading' });
  fireEvent.change(input, { target: { value: 'Day Trading Main' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  await waitFor(() => expect(accountsApi.update).toHaveBeenCalledWith(1, { name: 'Day Trading Main' }));
});

test('choosing an account updates the shared header state', async () => {
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Account: All Accounts' }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: /Swing/ }));
  expect(await screen.findByRole('button', { name: 'Account: Swing' })).toBeInTheDocument();
});

test('Add Trade opens a modal dialog that closes on Escape', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /Add Trade/ }));
  const dialog = await screen.findByRole('dialog', { name: /Add Trade/ });
  expect(within(dialog).getByRole('button', { name: /Save Trade/ })).toBeInTheDocument();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  await waitFor(() => expect(screen.queryByRole('dialog', { name: /Add Trade/ })).not.toBeInTheDocument());
});

test('Reports keeps its tabs, adds Sources & Tags, and supports arrow-key navigation', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Reports' }));
  const tablist = await screen.findByRole('tablist');
  const names = within(tablist).getAllByRole('tab').map(t => t.textContent.trim());
  expect(names).toEqual(['Smoking Gun', 'Overview', 'Setups & Strategy', 'Sources & Tags', 'Timing', 'Execution', 'Symbols', 'Psychology']);
  const overview = within(tablist).getByRole('tab', { name: 'Overview' });
  expect(overview).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(overview, { key: 'ArrowRight' });
  expect(within(tablist).getByRole('tab', { name: 'Setups & Strategy' })).toHaveAttribute('aria-selected', 'true');
});


async function openSmokingGun() {
  fireEvent.click(within(nav()).getByRole('button', { name: 'Reports' }));
  const reportTabs = await screen.findByRole('tablist');
  fireEvent.click(within(reportTabs).getByRole('tab', { name: 'Smoking Gun' }));
  return screen.findByRole('tablist', { name: 'Smoking Gun views' });
}

test('Smoking Gun has Live Analytics and Report Library submodes with live as default', async () => {
  await renderApp();
  const subnav = await openSmokingGun();
  expect(within(subnav).getAllByRole('tab').map(t => t.textContent.trim()))
    .toEqual(['Live Analytics', 'Report Library']);
  expect(within(subnav).getByRole('tab', { name: 'Live Analytics' }))
    .toHaveAttribute('aria-selected', 'true');
});

test('Smoking Gun report library preserves API order and marks stale snapshots', async () => {
  await renderApp();
  const subnav = await openSmokingGun();
  fireEvent.click(within(subnav).getByRole('tab', { name: 'Report Library' }));

  await screen.findByText('September Smoking Gun');
  const cards = screen.getAllByRole('article', { name: /Smoking Gun report:/ });
  expect(within(cards[0]).getByText('September Smoking Gun')).toBeInTheDocument();
  expect(within(cards[1]).getByText('August Smoking Gun')).toBeInTheDocument();
  expect(within(cards[0]).getByText('SOURCE CHANGED')).toBeInTheDocument();
  expect(within(cards[1]).getByText('CURRENT')).toBeInTheDocument();
  expect(within(cards[0]).getByText(/SPY, QQQ/)).toBeInTheDocument();
});

test('Smoking Gun library has a stable empty and error state', async () => {
  smokingGunLibraryApi.list.mockResolvedValue({ data: [] });
  await renderApp();
  let subnav = await openSmokingGun();
  fireEvent.click(within(subnav).getByRole('tab', { name: 'Report Library' }));
  expect(await screen.findByText('No saved Smoking Gun reports yet. Generate one from ChatGPT to build your audit history.')).toBeInTheDocument();

  smokingGunLibraryApi.list.mockRejectedValue(new Error('library offline'));
  fireEvent.click(within(subnav).getByRole('tab', { name: 'Live Analytics' }));
  fireEvent.click(within(subnav).getByRole('tab', { name: 'Report Library' }));
  expect(await screen.findByText(/library offline/i)).toBeInTheDocument();
  expect(screen.getByRole('tablist', { name: 'Smoking Gun views' })).toBeInTheDocument();
});

test('Smoking Gun library requires explicit delete confirmation', async () => {
  await renderApp();
  const subnav = await openSmokingGun();
  fireEvent.click(within(subnav).getByRole('tab', { name: 'Report Library' }));
  await screen.findByText('September Smoking Gun');

  const card = screen.getByRole('article', { name: 'Smoking Gun report: September Smoking Gun' });
  fireEvent.click(within(card).getByRole('button', { name: 'Delete Report' }));
  expect(within(card).getByText(/Delete this saved snapshot/i)).toBeInTheDocument();
  expect(within(card).getByRole('button', { name: 'Confirm delete' })).toBeInTheDocument();
  expect(smokingGunLibraryApi.remove).not.toHaveBeenCalled();
});


test('Smoking Gun saved report renders professional evidence hierarchy and accessibility', async () => {
  await renderApp();
  const subnav = await openSmokingGun();
  fireEvent.click(within(subnav).getByRole('tab', { name: 'Report Library' }));
  const card = await screen.findByRole('article', { name: 'Smoking Gun report: September Smoking Gun' });
  fireEvent.click(within(card).getByRole('button', { name: 'Open Report' }));

  const terminal = await screen.findByRole('region', { name: 'Saved Smoking Gun report' });
  const topHeadings = within(terminal).getAllByRole('heading', { level: 1 });
  expect(topHeadings).toHaveLength(1);
  expect(topHeadings[0]).toHaveTextContent('September Smoking Gun');

  const sections = within(terminal).getAllByRole('heading', { level: 2 }).map((h) => h.textContent.trim());
  expect(sections).toEqual([
    'Command Header',
    'Executive Scoreboard',
    'Behavioral Cohort Split',
    'Edge Map / Hold Time',
    'Position Size',
    'Daily P&L',
    'Daily Stop Lab',
    'Ticker Ranking',
    'Evidence Standards',
    'Behavioral Forensics',
    'Time Analysis',
    'Scenario Model',
    'Diagnosis',
    'Mechanical Action Plan',
  ]);

  expect(within(terminal).getByText('SOURCE CHANGED')).toBeVisible();
  expect(within(terminal).getByText('Not supported as a leak')).toBeVisible();
  expect(within(terminal).getByText('thin')).toBeVisible();
  expect(within(terminal).getByText('Rule-aligned cohort')).toBeVisible();
  expect(within(terminal).getByText('Comparison cohort')).toBeVisible();
  expect(within(terminal).getByText(/Association, not causation/i)).toBeVisible();
  expect(within(terminal).getAllByText(/Thin sample/i).length).toBeGreaterThan(0);
  expect(within(terminal).getByRole('button', { name: 'Back to Report Library' })).toBeVisible();
  expect(within(terminal).getByRole('button', { name: 'Download HTML' })).toBeVisible();
  expect(within(terminal).getByRole('button', { name: 'Download Trade Ledger' })).toBeVisible();
});

test('Smoking Gun saved report back navigation preserves the loaded library state', async () => {
  await renderApp();
  const subnav = await openSmokingGun();
  fireEvent.click(within(subnav).getByRole('tab', { name: 'Report Library' }));
  const card = await screen.findByRole('article', { name: 'Smoking Gun report: September Smoking Gun' });
  const callsBeforeOpen = smokingGunLibraryApi.list.mock.calls.length;
  fireEvent.click(within(card).getByRole('button', { name: 'Open Report' }));
  const terminal = await screen.findByRole('region', { name: 'Saved Smoking Gun report' });
  fireEvent.click(within(terminal).getByRole('button', { name: 'Back to Report Library' }));

  expect(await screen.findByRole('article', { name: 'Smoking Gun report: September Smoking Gun' })).toBeVisible();
  expect(screen.getByRole('article', { name: 'Smoking Gun report: August Smoking Gun' })).toBeVisible();
  expect(smokingGunLibraryApi.list).toHaveBeenCalledTimes(callsBeforeOpen);
});

test('Trade View surfaces option strategy, review status, excursion and planned R:R status', async () => {
  tradesApi.list.mockResolvedValue({
    data: [{
      id: 565,
      account_id: 1,
      trade_group: '2026-09-25_QCOM_OPTION_0922',
      date: '2026-09-25',
      ticker: 'QCOM',
      instrument_type: 'OPTION',
      side: 'LONG',
      gross_pnl: 211,
      net_pnl: 205.89,
      commissions: 5.11,
      executions: [
        { date: '2026-09-25', time: '09:22:00', action: 'BOT', qty: 5, price: 1.08 },
        { date: '2026-09-25', time: '09:41:00', action: 'SOLD', qty: 5, price: 1.50 },
      ],
      pl_pct: 38.13,
      strategy: 'LE E-Entry — 10m 8 EMA Retest + VWAP Reclaim',
      setup_grade: 'A',
      emotional_state: 'Focused',
      entry_reason: 'Confirmed reclaim entry.',
      exit_reason: null,
      mistakes: 'Held first trim too long.',
      mfe_pct: 103.7,
      mae_pct: 72.22,
      exit_efficiency: 71.8,
      stop_loss: 1.17,
      target_price: 4.14,
      risk_per_trade: null,
      realized_r: null,
    }],
  });

  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());

  expect(await screen.findByText('QCOM')).toBeVisible();
  expect(screen.getByText(/LE E-Entry — 10m 8 EMA Retest/)).toBeVisible();
  expect(screen.getByText('A')).toBeVisible();
  expect(screen.getByText('Followed')).toBeVisible();
  expect(screen.getByRole('img', { name: /2\/3 review fields documented/ })).toBeVisible();
  expect(screen.queryByText('2/3 documented')).not.toBeInTheDocument();
  expect(screen.queryByText('Held first trim too long.')).not.toBeInTheDocument();
  expect(screen.getByText('+103.7')).toBeVisible();
  expect(screen.getByText('-72.2')).toBeVisible();
  expect(screen.getByText('71.8%')).toBeVisible();
  expect(screen.getByText('1:3.54')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Set R:R' })).not.toBeInTheDocument();
  expect(screen.queryByText('Not tagged')).not.toBeInTheDocument();

  tradesApi.getAnalysis.mockResolvedValueOnce({
    data: {
      analysis: {
        strategy: 'LE E-Entry — 10m 8 EMA Retest + VWAP Reclaim',
        stop_loss: 1.17,
        target_price: 4.14,
        risk_per_trade: null,
      },
      tags: [],
    },
  });
  fireEvent.click(screen.getByText('QCOM'));
  const panel = await screen.findByRole('tabpanel');
  fireEvent.click(within(panel).getByRole('button', { name: /^Edit$/ }));

  const stopDistanceInput = await screen.findByLabelText('Stop Distance ($)');
  expect(stopDistanceInput).toBeVisible();
  expect(screen.getByLabelText('Target Distance ($)')).toBeVisible();
  expect(screen.getByText(/Planned R:R:/)).toBeVisible();
  expect(screen.getByLabelText('Max Premium Risk ($)')).toHaveValue(540);
  expect(screen.getByText(/Auto-filled from total entry premium:/)).toBeVisible();
  expect(stopDistanceInput).toHaveValue(1.17);
});

test('Stats planned risk is explicit and is sent with the saved trade analysis', async () => {
  tradesApi.getAnalysis.mockResolvedValue({
    data: {
      analysis: {
        strategy: 'Test Strategy',
        stop_loss: 360,
        target_price: 380,
        risk_per_trade: null,
        emotional_state: 'Focused',
      },
      tags: [],
    },
  });

  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  const row = (await screen.findAllByText('TSLA'))[0].closest('tr');
  fireEvent.click(row);

  const panel = await screen.findByRole('tabpanel');
  fireEvent.click(within(panel).getByRole('button', { name: /^Edit$/ }));
  expect(within(panel).getByLabelText('Stop Distance ($)')).toBeVisible();
  expect(within(panel).getByLabelText('Target Distance ($)')).toBeVisible();
  const riskInput = within(panel).getByLabelText('Planned Risk ($)');
  fireEvent.change(riskInput, { target: { value: '150' } });
  fireEvent.click(within(panel).getByRole('button', { name: /^Save$/ }));

  await waitFor(() => expect(tradesApi.updateAnalysis).toHaveBeenCalled());
  const [, payload] = tradesApi.updateAnalysis.mock.calls.at(-1);
  expect(payload.risk_per_trade).toBe(150);
});

test('Trade View opens Trade Details with all six tabs, back and previous/next', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  // A row opens the trade. The in-place expand was removed in V3.
  const row = (await screen.findAllByText('TSLA'))[0].closest('tr');
  fireEvent.click(row);

  const tablist = await screen.findByRole('tablist', { name: 'Trade review sections' });
  const names = within(tablist).getAllByRole('tab').map(t => t.textContent.trim());
  expect(names).toEqual(['Stats', 'Review', 'Tags', 'LE Review', 'Executions', 'Chart Review']);
  expect(screen.getByRole('button', { name: /Back to trades/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Previous trade/ })).toBeDisabled();
  expect(screen.getByRole('button', { name: /Next trade/ })).toBeEnabled();
  // Trade View stays highlighted while a trade is open.
  expect(within(nav()).getByRole('button', { name: 'Trade View' })).toHaveAttribute('aria-current', 'page');

  fireEvent.click(within(tablist).getByRole('tab', { name: 'LE Review' }));
  await waitFor(() => expect(tradesApi.getLeReview).toHaveBeenCalled());
  expect(screen.getByText(/LE review unavailable/)).toBeInTheDocument();

  fireEvent.click(within(tablist).getByRole('tab', { name: 'Executions' }));
  expect(screen.getByRole('button', { name: /Add Execution/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit execution 1' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Delete execution 1' })).toBeInTheDocument();
});

test('Chart Review replaces What If and uploads a TradingView screenshot', async () => {
  tradesApi.getAnalysis.mockResolvedValue({ data: { analysis: {}, tags: [] } });

  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  const row = (await screen.findAllByText('TSLA'))[0].closest('tr');
  fireEvent.click(row);

  const tablist = await screen.findByRole('tablist', { name: 'Trade review sections' });
  expect(within(tablist).queryByRole('tab', { name: 'What If' })).not.toBeInTheDocument();
  fireEvent.click(within(tablist).getByRole('tab', { name: 'Chart Review' }));

  expect(await screen.findByText(/Review the setup visually/i)).toBeVisible();
  expect(screen.getByText('10m default')).toBeVisible();
  expect(screen.getByText('8 EMA')).toBeVisible();
  expect(screen.getByText('PDH / PDL solid')).toBeVisible();
  expect(screen.getByText('PMH / PML dashed')).toBeVisible();

  const input = document.querySelector('.td-chart-review-upload input[type="file"]');
  const file = new File(['chart'], 'qqq-review.webp', { type: 'image/webp' });
  fireEvent.change(input, { target: { files: [file] } });

  await waitFor(() => expect(tradesApi.uploadChartScreenshot).toHaveBeenCalled());
  const [group, formData] = tradesApi.uploadChartScreenshot.mock.calls.at(-1);
  expect(group).toBeTruthy();
  expect(formData.get('file').name).toBe('qqq-review.webp');
  expect(screen.getByText(/Paste with Ctrl\+V or upload/i)).toBeVisible();
});

test('trade screenshot can be pasted from clipboard without saving a local file', async () => {
  tradesApi.getAnalysis.mockResolvedValue({ data: { analysis: {}, tags: [] } });

  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  const row = (await screen.findAllByText('TSLA'))[0].closest('tr');
  fireEvent.click(row);

  const pasted = new File(['clipboard-chart'], 'clipboard.webp', { type: 'image/webp' });
  fireEvent.paste(document.body, {
    clipboardData: {
      files: [pasted],
      items: [],
    },
  });

  await waitFor(() => expect(tradesApi.uploadChartScreenshot).toHaveBeenCalled());
  const [, formData] = tradesApi.uploadChartScreenshot.mock.calls.at(-1);
  expect(formData.get('file').type).toBe('image/webp');
});

test('chart image opens a native fullscreen dialog from the image surface', async () => {
  URL.createObjectURL.mockImplementation(() => 'blob:trade-chart-review');
  tradesApi.getAnalysis.mockResolvedValue({
    data: {
      analysis: { chart_screenshot_path: 'trade-review/test/chart.webp' },
      tags: [{ id: 1, tag_type: 'setup', tag_value: 'PDH Break' }],
    },
  });
  tradesApi.getChartScreenshot.mockResolvedValue({
    data: new Blob(['image'], { type: 'image/webp' }),
  });

  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  const row = (await screen.findAllByText('TSLA'))[0].closest('tr');
  fireEvent.click(row);

  await waitFor(() => expect(tradesApi.getChartScreenshot).toHaveBeenCalled());
  expect(await screen.findByText('Chart screenshot')).toBeVisible();
  const preview = await screen.findByTitle('Open screenshot full screen');
  expect(preview).toBeVisible();
  expect(preview).toHaveStyle({ width: '800px', maxWidth: '100%' });
  expect(within(preview).getByRole('img')).toHaveStyle({ height: 'auto', maxHeight: '600px' });

  const previewImage = within(preview).getByRole('img');
  fireEvent.click(previewImage);
  const lightbox = await screen.findByTestId('chart-screenshot-lightbox');
  expect(lightbox).toHaveAttribute('open');
  expect(document.body.style.overflow).toBe('hidden');
  expect(screen.getByRole('button', { name: 'Close screenshot' })).toBeVisible();
  expect(screen.getByText(/Click outside the chart or press Esc to close/i)).toBeVisible();

  fireEvent(lightbox, new Event('cancel', { bubbles: false, cancelable: true }));
  await waitFor(() => expect(lightbox).not.toHaveAttribute('open'));
  expect(document.body.style.overflow).toBe('');

  fireEvent.click(previewImage);
  await waitFor(() => expect(lightbox).toHaveAttribute('open'));
  fireEvent.click(lightbox);
  await waitFor(() => expect(lightbox).not.toHaveAttribute('open'));
});

test('professional Review quick picks stay separate from the full journal note', async () => {
  tradesApi.getAnalysis.mockResolvedValue({ data: { analysis: {}, tags: [] } });

  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  const row = (await screen.findAllByText('TSLA'))[0].closest('tr');
  fireEvent.click(row);

  const tablist = await screen.findByRole('tablist', { name: 'Trade review sections' });
  fireEvent.click(within(tablist).getByRole('tab', { name: 'Review' }));

  expect(await screen.findByText(/Professional review workspace/i)).toBeVisible();
  expect(screen.getByText(/0\/4 review areas documented/i)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: /Start blank review/i }));

  fireEvent.click(screen.getByRole('button', { name: 'Break + retest' }));
  fireEvent.click(screen.getByRole('button', { name: 'HOD trim + 8 EMA trail' }));
  fireEvent.click(screen.getByRole('button', { name: 'Held loser too long' }));

  expect(screen.getByText(/3\/4 review areas documented/i)).toBeVisible();
  expect(screen.getAllByText('Held loser too long').length).toBeGreaterThanOrEqual(1);
  expect(screen.getByText(/Exit sooner when favorable progress fails and technical invalidation begins/i)).toBeVisible();

  const entryNotes = screen.getByRole('textbox', { name: 'Entry notes' });
  const exitNotes = screen.getByRole('textbox', { name: 'Exit notes' });
  const mistakeNotes = screen.getByRole('textbox', { name: 'Mistake \/ improvement notes' });

  expect(entryNotes.value).toContain('Entered after the breakout level held on a retest.');
  expect(exitNotes.value).toContain('Trimmed into the high of day, then trailed the remainder using the 8 EMA on the 10-minute timeframe.');
  expect(mistakeNotes.value).toContain('Held a losing trade too long after the setup stopped working.');

  fireEvent.click(screen.getByRole('button', { name: /Save review/i }));
  await waitFor(() => expect(tradesApi.updateAnalysis).toHaveBeenCalled());

  const [, payload] = tradesApi.updateAnalysis.mock.calls.at(-1);
  expect(payload.entry_reason).toContain('Entered after the breakout level held on a retest.');
  expect(payload.exit_reason).toContain('8 EMA on the 10-minute timeframe');
  expect(payload.mistakes).toContain('Held a losing trade too long');
  expect(payload.notes).toBeNull();
});

test('LE Complete Review template prebuilds the journal without overwriting analytics fields', async () => {
  tradesApi.getAnalysis.mockResolvedValue({
    data: {
      analysis: {
        strategy: 'LE Model',
        stop_loss: 0.50,
        target_price: 1.25,
        risk_per_trade: 100,
        entry_reason: 'Existing structured entry',
        exit_reason: 'Existing structured exit',
        mistakes: 'Existing structured improvement',
      },
      tags: [],
    },
  });

  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Trade View' }));
  await waitFor(() => expect(tradesApi.list).toHaveBeenCalled());
  fireEvent.click((await screen.findAllByText('TSLA'))[0].closest('tr'));

  const tablist = await screen.findByRole('tablist', { name: 'Trade review sections' });
  fireEvent.click(within(tablist).getByRole('tab', { name: 'Review' }));

  const completeTemplate = await screen.findByRole('button', { name: /LE Complete Review/i });
  fireEvent.click(completeTemplate);

  const journal = screen.getByRole('textbox', { name: 'Trade journal note' });
  expect(journal.value).toContain('LE COMPLETE TRADE REVIEW');
  expect(journal.value).toContain('13-POINT LE PRE-TRADE AUDIT');
  expect(journal.value).toContain('LE END-OF-TRADE JOURNAL');
  expect(journal.value).toContain('Planned R:R: 1:2.50');
  expect(screen.getByText(/4\/4 review areas documented/i)).toBeVisible();

  fireEvent.click(screen.getByRole('button', { name: /Save review/i }));
  await waitFor(() => expect(tradesApi.updateAnalysis).toHaveBeenCalled());

  const [, payload] = tradesApi.updateAnalysis.mock.calls.at(-1);
  expect(payload.notes).toContain('NEXT-TRADE RULE');
  expect(payload.entry_reason).toBe('Existing structured entry');
  expect(payload.exit_reason).toBe('Existing structured exit');
  expect(payload.mistakes).toBe('Existing structured improvement');
});

test('Import keeps broker CSV import and diary analysis, with keyboard dropzones', async () => {
  await renderApp();
  fireEvent.click(within(screen.getByRole('banner')).getByRole('button', { name: /^Import$/ }));
  expect(await screen.findByRole('heading', { name: /Import Broker CSV/ })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: /Analyze Trading Diary/ })).toBeInTheDocument();
  // Both supported brokers stay selectable, with auto-detect as the default.
  const broker = screen.getByLabelText('Broker');
  expect(broker).toHaveValue('auto');
  expect(within(broker).getByRole('option', { name: /Interactive Brokers/ })).toBeInTheDocument();
  expect(within(broker).getByRole('option', { name: /Thinkorswim/ })).toBeInTheDocument();
  const dropzones = screen.getAllByRole('button', { name: /Press Enter to browse/ });
  expect(dropzones).toHaveLength(2);
  dropzones.forEach(z => expect(z).toHaveAttribute('tabindex', '0'));
});

test('Help lists the metric reference and the feature guide', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Help' }));
  expect(await screen.findByRole('heading', { name: 'Help and Reference' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Dashboard KPIs' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Features' })).toBeInTheDocument();
});

test('Calendar keeps the Month and Year views', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Calendar' }));
  expect(await screen.findByRole('button', { name: 'Month' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Year' })).toBeInTheDocument();
});

test('Day Review keeps the loss-streak alert and its Dismiss control', async () => {
  const loss = (id, time, pnl) => ({
    id, account_id: 1, trade_group: `g${id}`, date: '2026-09-10', ticker: `L${id}`, instrument_type: 'STOCK',
    side: 'LONG', net_pnl: pnl, gross_pnl: pnl, commissions: 0,
    executions: [{ date: '2026-09-10', time, action: 'BOT', qty: 1, price: 10, commission: 0 }],
  });
  tradesApi.list.mockImplementation(() => Promise.resolve({ data: [loss(1, '09:40:00', -10), loss(2, '10:10:00', -20), loss(3, '11:00:00', -30)] }));
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Day Review' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(/3 losses in a row/);
  fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss loss-streak alert' }));
  await waitFor(() => expect(screen.queryByText(/3 losses in a row/)).not.toBeInTheDocument());
  // Previous, Next and Regenerate AI stay in the page header.
  expect(screen.getByRole('button', { name: /Previous/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Next/ })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /Regenerate AI/ })).toBeInTheDocument();
});

test('Settings has Strategies, Sources and Tags sections, and Tags leaves out strategy and source types', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Settings' }));
  const tablist = await screen.findByRole('tablist', { name: 'Settings sections' });
  expect(within(tablist).getAllByRole('tab').map(t => t.textContent.replace(/\d+/g, '').trim()))
    .toEqual(['Strategies', 'Sources', 'Tags']);
  expect(await screen.findByText('VWAP Cross')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Edit VWAP Cross' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Delete VWAP Cross' })).toBeInTheDocument();

  fireEvent.click(within(tablist).getByRole('tab', { name: /Tags/ }));
  expect(await screen.findByRole('region', { name: 'Mistakes tags' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Execution tags' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: /Strategy tags/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: /Source tags/ })).not.toBeInTheDocument();
});

test('Settings merges one strategy into another', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Settings' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Merge Continuation RS into another strategy' }));
  fireEvent.change(screen.getByRole('combobox', { name: /Merge "Continuation RS" into/ }), { target: { value: 'VWAP Cross' } });
  fireEvent.click(screen.getByRole('button', { name: 'Merge' }));
  await waitFor(() => expect(libraryApi.merge).toHaveBeenCalledWith({
    kind: 'strategy', tag_type: '', source_name: 'Continuation RS', target_name: 'VWAP Cross',
  }));
  expect(await screen.findByRole('status')).toHaveTextContent(/2 trades moved/);
});

test('Settings delete asks to reassign and can leave trades blank', async () => {
  await renderApp();
  fireEvent.click(within(nav()).getByRole('button', { name: 'Settings' }));
  fireEvent.click(await screen.findByRole('tab', { name: /Sources/ }));
  fireEvent.click(await screen.findByRole('button', { name: 'Delete OneOption' }));
  expect(screen.getByRole('combobox', { name: /1 trade use "OneOption". Reassign them to/ })).toHaveValue('');
  fireEvent.click(screen.getAllByRole('button', { name: 'Delete' }).find(b => b.className.includes('btn-danger')));
  await waitFor(() => expect(libraryApi.remove).toHaveBeenCalledWith({
    kind: 'source', tag_type: '', name: 'OneOption', reassign_to: null,
  }));
});

test('A failed dashboard load keeps the page and offers Retry', async () => {
  kpisApi.get.mockImplementation(() => Promise.reject(new Error('Network Error')));
  await renderApp();
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(/Could not load the dashboard: Network Error/);
  // The shell stays: title and the date filter are still there.
  expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument();
  expect(within(screen.getByRole('main')).getByRole('button', { name: /All time/ })).toBeInTheDocument();

  kpisApi.get.mockImplementation(() => Promise.resolve({ data: { total_net_pnl: 100, daily_pnl: [], by_strategy: [] } }));
  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});

test('Goals save failure is shown and keeps the panel open', async () => {
  goalsApi.put.mockImplementation(() => Promise.reject(new Error('Server error')));
  await renderApp();
  fireEvent.click(screen.getByRole('button', { name: 'Edit goals' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(/Could not save goals: Server error/);
  expect(screen.getByRole('heading', { name: 'Goals' })).toBeInTheDocument();
});


test('Dashboard primary KPI strip uses goal-based trader metrics from the reference design', async () => {
  kpisApi.get.mockResolvedValue({
    data: {
      total_net_pnl: 4340.34,
      total_trades: 191,
      winning_trades: 101,
      losing_trades: 90,
      win_rate: 52.9,
      avg_win: 338,
      avg_loss: -517,
      profit_factor: 1.35,
      trading_days: 173,
      positive_days: 143,
      negative_days: 30,
      day_win_rate: 82.7,
      expectancy: 56.14,
      exit_efficiency: 38,
      capture_confidence: 'RELIABLE',
      daily_pnl: [],
    },
  });
  goalsApi.get.mockResolvedValue({
    data: {
      win_rate: 65,
      day_win_rate: 75,
      profit_factor: 1.5,
      avg_win_loss_ratio: 1.5,
      exit_efficiency: 50,
      expectancy: 50,
    },
  });

  await renderApp();

  for (const label of [
    'Trade win rate',
    'Day win rate',
    'Profit factor',
    'Win / loss size',
    'Exit efficiency',
    'Expectancy',
  ]) {
    expect(await screen.findByText(label)).toBeVisible();
  }

  expect(screen.getByText('52.9%')).toBeVisible();
  expect(screen.getByText('82.7%')).toBeVisible();
  expect(screen.getByText('1.35')).toBeVisible();
  expect(screen.getByText('0.65')).toBeVisible();
  expect(screen.getByText('38.0%')).toBeVisible();
  expect(screen.getByText('+$56.14')).toBeVisible();
  expect(screen.getByText(/101 won, 90 lost/i)).toBeVisible();
  expect(screen.getByText(/143 green days, 30 red/i)).toBeVisible();
  expect(screen.getByText(/average win is/i)).toBeVisible();
  expect(screen.getByText(/losses are larger than wins/i)).toBeVisible();
  expect(screen.getByText(/capture 38% of the favorable move/i)).toBeVisible();
  expect(screen.queryByText('Avg R / trade')).not.toBeInTheDocument();
  expect(screen.queryByText('Max drawdown')).not.toBeInTheDocument();
  const goalMarkers = Array.from(document.querySelectorAll('.v3-measures-dashboard .v3-track-goal'))
    .map(node => node.getAttribute('data-g'));
  expect(goalMarkers).toEqual(expect.arrayContaining([
    'goal 65%',
    'goal 75%',
    'goal 1.50',
    'goal 50%',
    'goal +$50.00',
  ]));
});

test('Dashboard prioritizes trade management and the latest saved Smoking Gun report', async () => {
  kpisApi.get.mockResolvedValue({
    data: {
      total_net_pnl: 4340.34,
      total_trades: 191,
      winning_trades: 111,
      losing_trades: 80,
      win_rate: 58.1,
      avg_win: 222.72,
      avg_loss: -162.63,
      profit_factor: 1.42,
      expectancy: 22.14,
      avg_r: 0.28,
      r_sample_count: 83,
      max_drawdown: -1626.32,
      exit_efficiency: 64,
      capture_n: 83,
      capture_winner_total: 111,
      capture_coverage_pct: 74.8,
      capture_confidence: 'RELIABLE',
      capture_days: 20,
      avg_mfe: 2.4,
      avg_mae: 0.8,
      excursion_n: 150,
      excursion_total_trades: 191,
      management_coverage_pct: 78.5,
      excursion_confidence: 'RELIABLE',
      excursion_days: 22,
      management_primary_source: 'broker_csv',
      daily_pnl: [{ date: '2026-09-25', net_pnl: 528, cumulative: 4340.34 }],
      trading_days: 28,
    },
  });

  await renderApp();

  expect(await screen.findByRole('heading', { name: /Trade management/i })).toBeVisible();
  expect(screen.getByText('Total net P&L')).toBeVisible();
  expect(screen.getByText(/Evaluate how efficiently you manage entries, risk, and exits/i)).toBeVisible();
  expect(screen.queryByText(/Broker CSV/i)).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: /Profit capture/i })).toBeVisible();
  expect(screen.getByRole('heading', { name: /Holding behavior/i })).toBeVisible();
  expect(screen.getByRole('heading', { name: /Profit vs\. left on table/i })).toBeVisible();
  expect(screen.getByRole('heading', { name: /Risk during trade/i })).toBeVisible();
  expect(screen.getByRole('heading', { name: /Bottom line/i })).toBeVisible();
  expect(screen.getByText('ABOVE GOAL')).toBeVisible();
  expect(screen.getByText('64% Captured')).toBeVisible();
  expect(screen.getByText('36% Left')).toBeVisible();
  expect(screen.getAllByText('+$4340.34')).toHaveLength(1);
  expect(screen.getAllByText('64%').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText('36%')).toBeVisible();
  const captureMeter = screen.getByRole('meter', { name: 'Profit capture' });
  expect(captureMeter).toHaveAttribute('aria-valuenow', '64');
  expect(captureMeter).toHaveAttribute('aria-valuetext', '64% exit efficiency; goal 60%');
  expect(screen.getByText(/retain a solid portion of the favorable move/i)).toBeVisible();
  expect(screen.getByText('Cumulative net P&L')).toBeVisible();
  expect(screen.getByRole('heading', { name: /Latest Smoking Gun report summary/i })).toBeVisible();
  expect(screen.getByText('Diagnosis')).toBeVisible();
  expect(screen.getByText('Mechanical action plan')).toBeVisible();
  expect(screen.getByText('Averaging down')).toBeVisible();
  expect(screen.queryByText('Report period')).not.toBeInTheDocument();
  expect(screen.queryByText('September Smoking Gun')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /Recent trades/i })).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: /Open positions/i })).not.toBeInTheDocument();
  expect(screen.queryByText('Overall performance')).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'What works' })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /View full report/i }));
  const smokingTab = await screen.findByRole('tab', { name: 'Smoking Gun' });
  expect(smokingTab).toHaveAttribute('aria-selected', 'true');
});


test('Risk During Trade turns winner-vs-loser excursion into actionable coaching', async () => {
  kpisApi.get.mockResolvedValue({
    data: {
      total_net_pnl: 1200,
      total_trades: 47,
      trading_days: 5,
      exit_efficiency: 65.58,
      exit_efficiency_median: 74.52,
      capture_n: 22,
      capture_winner_total: 27,
      capture_coverage_pct: 81.5,
      capture_confidence: 'RELIABLE',
      capture_days: 5,
      avg_mfe: 53.51,
      avg_mae: 30.87,
      median_mfe: 12.82,
      median_mae: 26.14,
      winner_median_mfe: 22.86,
      loser_median_mfe: 0.85,
      winner_median_mae: 14.57,
      loser_median_mae: 33.76,
      winner_mae_le_20_pct: 59.1,
      loser_mfe_le_5_pct: 66.7,
      loser_mfe_le_10_pct: 72.2,
      loser_mae_ge_25_pct: 66.7,
      excursion_n: 40,
      excursion_total_trades: 47,
      management_coverage_pct: 85.1,
      excursion_confidence: 'RELIABLE',
      excursion_days: 5,
      daily_pnl: [{ date: '2026-09-25', net_pnl: 100, cumulative: 1200 }],
    },
  });

  await renderApp();

  expect(await screen.findByText(/Main improvement: tighten entry quality and invalidate failed trades sooner/i)).toBeVisible();
  expect(screen.getByText(/Typical winner: \+22\.9% MFE \/ -14\.6% MAE\. Typical loser: \+0\.9% MFE \/ -33\.8% MAE/i)).toBeVisible();
  expect(screen.getByText(/67% of losers never reach \+5% MFE, 72% never reach \+10%, and 67% reach at least -25% MAE/i)).toBeVisible();
  expect(screen.getByText(/41% of winners exceeded -20% MAE, so avoid a blanket -20% stop/i)).toBeVisible();
  expect(screen.queryByText(/Mean and median excursion disagree/i)).not.toBeInTheDocument();
});

test('Dashboard withholds extreme capture when evidence coverage is low', async () => {
  kpisApi.get.mockResolvedValue({
    data: {
      total_net_pnl: 500,
      total_trades: 47,
      trading_days: 5,
      exit_efficiency: -3036,
      capture_n: 4,
      capture_winner_total: 27,
      capture_coverage_pct: 14.8,
      capture_confidence: 'LOW',
      avg_mfe: 0.55,
      avg_mae: 0.36,
      excursion_n: 10,
      excursion_total_trades: 47,
      management_coverage_pct: 21.3,
      excursion_confidence: 'LOW',
      excursion_days: 1,
      daily_pnl: [{ date: '2026-09-25', net_pnl: 500, cumulative: 500 }],
    },
  });

  await renderApp();

  expect(await screen.findByText('LOW COVERAGE')).toBeVisible();
  expect(screen.queryByText(/-3036%/)).not.toBeInTheDocument();
  expect(screen.queryByText(/3136%/)).not.toBeInTheDocument();
  expect(screen.getByRole('status', { name: 'Profit capture unavailable' })).toBeVisible();
  expect(screen.getByText(/No excursion-based diagnosis is issued/i)).toBeVisible();
  expect(screen.getByText(/10\/47 trades · 21% coverage · LOW/i)).toBeVisible();
});

test('management backfill refresh survives same-range dashboard rerenders', async () => {
  let resolveBackfill;
  let resolveRecent;

  const backfillPromise = new Promise(resolve => { resolveBackfill = resolve; });
  const recentPromise = new Promise(resolve => { resolveRecent = resolve; });

  excursionApi.calculateRange.mockImplementation(() =>
    backfillPromise.then(() => ({ data: { computed: 105, skipped: 42 } }))
  );

  tradesApi.list.mockImplementation((params = {}) => {
    if (params.open_only) return Promise.resolve({ data: [] });
    if (params.closed_only) return recentPromise;
    return Promise.resolve({ data: [] });
  });

  let rangedCalls = 0;
  kpisApi.get.mockImplementation((params = {}) => {
    if (!params.date_from) {
      return Promise.resolve({
        data: {
          total_net_pnl: 1000,
          total_trades: 159,
          daily_pnl: [{ date: '2026-09-25', net_pnl: 100, cumulative: 1000 }],
        },
      });
    }

    rangedCalls += 1;
    const refreshed = rangedCalls >= 3;
    return Promise.resolve({
      data: refreshed ? {
        total_net_pnl: 1000,
        total_trades: 159,
        exit_efficiency: 66.74,
        capture_n: 62,
        capture_winner_total: 81,
        capture_coverage_pct: 76.5,
        capture_confidence: 'RELIABLE',
        avg_mfe: 18.2,
        avg_mae: 7.1,
        excursion_n: 117,
        management_coverage_pct: 73.6,
        excursion_confidence: 'RELIABLE',
        excursion_days: 21,
        daily_pnl: [{ date: '2026-09-25', net_pnl: 100, cumulative: 1000 }],
      } : {
        total_net_pnl: 1000,
        total_trades: 159,
        exit_efficiency: null,
        capture_n: 7,
        capture_winner_total: 81,
        capture_coverage_pct: 8.6,
        capture_confidence: 'LOW',
        avg_mfe: null,
        avg_mae: null,
        excursion_n: 12,
        management_coverage_pct: 7.5,
        excursion_confidence: 'LOW',
        excursion_days: 1,
        daily_pnl: [{ date: '2026-09-25', net_pnl: 100, cumulative: 1000 }],
      },
    });
  });

  await renderApp();
  await waitFor(() => expect(excursionApi.calculateRange).toHaveBeenCalled());

  await act(async () => {
    resolveRecent({ data: [{ date: '2026-09-25', ticker: 'QQQ', net_pnl: 10 }] });
    await Promise.resolve();
  });

  await act(async () => {
    resolveBackfill();
    await backfillPromise;
  });

  expect(await screen.findByText(/117\/159 trades · 74% coverage · RELIABLE/i)).toBeVisible();
  expect(screen.getAllByText(/62\/81 winning trades · 77% coverage · RELIABLE/i).length).toBeGreaterThanOrEqual(2);
  expect(screen.queryByText(/12\/159 trades · 8% coverage · LOW/i)).not.toBeInTheDocument();
});

test('Dashboard reports unavailable profit capture without invalid meter semantics', async () => {
  kpisApi.get.mockResolvedValue({
    data: {
      total_net_pnl: 0,
      total_trades: 0,
      trading_days: 0,
      exit_efficiency: null,
      daily_pnl: [],
    },
  });

  await renderApp();

  const unavailableCapture = await screen.findByRole('status', { name: 'Profit capture unavailable' });
  expect(unavailableCapture).toBeVisible();
  expect(unavailableCapture).not.toHaveAttribute('aria-valuetext');
  expect(screen.queryByRole('meter', { name: 'Profit capture' })).not.toBeInTheDocument();
});


test('Dashboard suppresses absurd capture values when coverage is low', async () => {
  kpisApi.get.mockResolvedValue({
    data: {
      total_net_pnl: 100,
      total_trades: 47,
      winning_trades: 27,
      losing_trades: 20,
      trading_days: 5,
      exit_efficiency: -3036,
      capture_n: 2,
      capture_winner_total: 27,
      capture_coverage_pct: 7.4,
      capture_confidence: 'LOW',
      capture_days: 1,
      avg_mfe: 0.55,
      avg_mae: 0.36,
      excursion_n: 10,
      excursion_total_trades: 47,
      management_coverage_pct: 21.3,
      excursion_confidence: 'LOW',
      excursion_days: 1,
      daily_pnl: [{ date: '2026-09-25', net_pnl: 528, cumulative: 100 }],
    },
  });

  await renderApp();

  expect(await screen.findByText('LOW COVERAGE')).toBeVisible();
  expect(screen.queryByText('-3036%')).not.toBeInTheDocument();
  expect(screen.queryByText('3136%')).not.toBeInTheDocument();
  expect(screen.getAllByText('N/A').length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText(/No excursion-based diagnosis is issued/i)).toBeVisible();
  expect(excursionApi.calculateRange).toHaveBeenCalled();
});
