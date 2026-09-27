export const ACCOUNT_TYPES = [
  {
    value: 'day_trading',
    label: 'Day Trading',
    description: 'Intraday-focused. Overnight trades are excluded from Holding Behavior analysis.',
  },
  {
    value: 'swing_trading',
    label: 'Swing Trading',
    description: 'Trades intentionally held overnight or across multiple sessions. Overnight trades stay in Holding Behavior.',
  },
  {
    value: 'mixed_trading',
    label: 'Mixed Trading',
    description: 'One brokerage account for both day and swing trades. Overnight trades stay in Holding Behavior for now.',
  },
  {
    value: 'investment',
    label: 'Investment',
    description: 'Long-term holdings and portfolio investing. Overnight trades stay in Holding Behavior.',
  },
];

export const accountTypeLabel = (type) =>
  ACCOUNT_TYPES.find(option => option.value === type)?.label || type || 'Unclassified';

export const accountTypeDescription = (type) =>
  ACCOUNT_TYPES.find(option => option.value === type)?.description || 'No account behavior description is available.';

export function accountTypeImpact(fromType, toType) {
  if (fromType === toType) {
    return {
      affected: [],
      unchanged: ['Trade history', 'Entry and exit prices', 'P&L', 'Commissions and fees', 'Journal notes'],
      note: 'No analytics behavior changes because the account type is unchanged.',
    };
  }

  if (toType === 'day_trading') {
    return {
      affected: [
        'Holding Behavior will use Day Trading rules.',
        'Overnight trades will be excluded from Holding Behavior analysis.',
      ],
      unchanged: ['Trade history', 'Entry and exit prices', 'P&L', 'Commissions and fees', 'Journal notes'],
      note: 'This changes analytics interpretation only. It does not edit or delete trades.',
    };
  }

  if (fromType === 'day_trading') {
    return {
      affected: [
        'Holding Behavior will stop using the Day Trading overnight exclusion.',
        'Overnight trades will be included in Holding Behavior analysis.',
      ],
      unchanged: ['Trade history', 'Entry and exit prices', 'P&L', 'Commissions and fees', 'Journal notes'],
      note: 'This changes analytics interpretation only. It does not edit or delete trades.',
    };
  }

  return {
    affected: [
      'Account-type-dependent analytics will use the new classification.',
      'Current Holding Behavior overnight inclusion stays the same.',
    ],
    unchanged: ['Trade history', 'Entry and exit prices', 'P&L', 'Commissions and fees', 'Journal notes'],
    note: 'Swing Trading, Mixed Trading, and Investment currently include overnight trades in Holding Behavior.',
  };
}
