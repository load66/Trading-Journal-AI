import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

import { SavedTagCombobox, filterTagLibraryItems } from './TradeDetail';

const ITEMS = [
  { name: 'Outside Day', description: 'Both directional levels broke.' },
  { name: 'PMH Break', description: 'Premarket High was the breakout level.' },
  { name: 'PMH RECLAIM', description: '' },
  { name: 'PDH Break', description: 'Previous Day High was the breakout level.' },
];

test('saved tag filtering finds stored PMH tags case-insensitively', () => {
  expect(filterTagLibraryItems(ITEMS, 'pmh').map(item => item.name)).toEqual([
    'PMH Break',
    'PMH RECLAIM',
  ]);
});

test('searchable saved tag picker shows stored matches and selects an exact saved tag', () => {
  const onSelect = jest.fn();

  render(
    <SavedTagCombobox
      items={ITEMS}
      value=""
      onSelect={onSelect}
      placeholder="Search setup context tags…"
      ariaLabel="Search saved tag"
    />,
  );

  const input = screen.getByRole('combobox', { name: 'Search saved tag' });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'pmh' } });

  expect(screen.getByRole('option', { name: /PMH Break/i })).toBeVisible();
  expect(screen.getByRole('option', { name: /PMH RECLAIM/i })).toBeVisible();
  expect(screen.queryByRole('option', { name: /Outside Day/i })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('option', { name: /PMH RECLAIM/i }));
  expect(onSelect).toHaveBeenCalledWith('PMH RECLAIM');
});

test('typing arbitrary text does not select or create a new saved tag', () => {
  const onSelect = jest.fn();

  render(
    <SavedTagCombobox
      items={ITEMS}
      value=""
      onSelect={onSelect}
      ariaLabel="Search saved tag"
    />,
  );

  const input = screen.getByRole('combobox', { name: 'Search saved tag' });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'brand new wording' } });

  expect(screen.getByText(/No saved tags match/)).toBeVisible();
  expect(onSelect).not.toHaveBeenCalledWith('brand new wording');
});
