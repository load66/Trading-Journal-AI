import { calculateLESize, getThreeTradeGuard } from './LERiskPlanner';

test('LE capital calculator produces the strategy limits instantly from account size', () => {
  const result = calculateLESize({ capital: 10000 });

  expect(result.hasCapital).toBe(true);
  expect(result.exposureLow).toBeCloseTo(2000, 6);
  expect(result.exposureHigh).toBeCloseTo(3000, 6);
  expect(result.maxLoss).toBeCloseTo(500, 6);
  expect(result.minTarget).toBeCloseTo(1000, 6);
});

test('LE capital calculator stays empty until capital is entered', () => {
  const result = calculateLESize({ capital: '' });

  expect(result.hasCapital).toBe(false);
  expect(result.exposureLow).toBe(0);
  expect(result.exposureHigh).toBe(0);
  expect(result.maxLoss).toBe(0);
  expect(result.minTarget).toBe(0);
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
