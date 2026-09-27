import { calculateLESize, getThreeTradeGuard } from './LERiskPlanner';

test('LE sizing reproduces the $10k worked example from the strategy guide', () => {
  const result = calculateLESize({
    capital: 10000,
    exposure_pct: 30,
    direction: 'call',
    option_price: 4.20,
    delta: 0.62,
    underlying_entry: 150,
    stop_price: 148.50,
    target_price: '',
  });

  expect(result.exposureHigh).toBeCloseTo(3000, 6);
  expect(result.maxLoss).toBeCloseTo(500, 6);
  expect(result.contractsByExposure).toBe(7);
  expect(result.contractsByRisk).toBe(5);
  expect(result.contracts).toBe(5);
  expect(result.premiumUsed).toBeCloseTo(2100, 6);
  expect(result.actualRisk).toBeCloseTo(465, 6);
  expect(result.actualRiskPct).toBeCloseTo(4.65, 6);
  expect(result.requiredSpotMove).toBeCloseTo(1000 / (0.62 * 100 * 5), 6);
});

test('LE sizing always uses the stricter of premium exposure and actual-loss cap', () => {
  const result = calculateLESize({
    capital: 5000,
    exposure_pct: 25,
    direction: 'call',
    option_price: 2.00,
    delta: 0.50,
    underlying_entry: 100,
    stop_price: 99,
  });

  expect(result.contractsByExposure).toBe(6);
  expect(result.contractsByRisk).toBe(5);
  expect(result.contracts).toBe(5);
  expect(result.actualRisk).toBeLessThanOrEqual(result.maxLoss);
  expect(result.premiumUsed).toBeLessThanOrEqual(result.exposureBudget);
});

test.each([
  ['red', 'red', false, 'STOP · TWO REDS', false],
  ['red', 'green', false, 'STOP · RECOVERED', false],
  ['green', 'green', false, 'A+ SETUP ONLY', false],
  ['green', 'green', true, 'FINAL TRADE ALLOWED', true],
  ['green', 'red', false, 'CAUTION · A+ ONLY', false],
  ['green', 'red', true, 'FINAL TRADE · CAUTION', true],
])('Three Trade Rule maps %s/%s correctly', (trade1, trade2, aPlus, label, thirdEligible) => {
  const result = getThreeTradeGuard({
    rule_committed: true,
    trade1,
    trade2,
    third_trade_a_plus: aPlus,
    trade3_done: false,
  });

  expect(result.label).toBe(label);
  expect(result.thirdEligible).toBe(thirdEligible);
});

test('Three Trade Rule closes the session after trade three', () => {
  const result = getThreeTradeGuard({
    rule_committed: true,
    trade1: 'green',
    trade2: 'green',
    third_trade_a_plus: true,
    trade3_done: true,
  });

  expect(result.label).toBe('STOP · THREE TRADES');
  expect(result.thirdEligible).toBe(false);
});
