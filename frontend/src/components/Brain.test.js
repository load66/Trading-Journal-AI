import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';

import Brain from './Brain';
import { brainApi } from '../api';

jest.mock('../api', () => ({
  brainApi: {
    chat: jest.fn(),
  },
}));

test('Brain suggested journal question calls the API and renders the answer', async () => {
  brainApi.chat.mockResolvedValue({
    data: { response: '**SPY** is your strongest ticker by total P&L.' },
  });

  render(<Brain accountId={4} open={true} onOpenChange={() => {}} />);

  fireEvent.click(screen.getByRole('button', { name: 'What is my best performing strategy?' }));

  await waitFor(() => expect(brainApi.chat).toHaveBeenCalledTimes(1));
  expect(brainApi.chat.mock.calls[0][1]).toBe(4);
  expect(brainApi.chat.mock.calls[0][0][0].content).toBe('What is my best performing strategy?');
  expect(await screen.findByText('SPY')).toBeVisible();
});

test('Brain exposes a compact journal-first welcome and composer', () => {
  render(<Brain accountId={4} open={true} onOpenChange={() => {}} />);

  expect(screen.getByText('Ask your journal, not a generic chatbot.')).toBeVisible();
  expect(screen.getByLabelText('Message Brain')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Audit my recent trades against the LE system' })).toBeVisible();
});
