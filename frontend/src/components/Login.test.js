import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import Login from './Login';

test('submits email and password to the supplied sign-in action', async () => {
  const onSignIn = jest.fn().mockResolvedValue(undefined);
  render(<Login onSignIn={onSignIn} />);

  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'owner@example.com' } });
  fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'secret' } });
  fireEvent.click(screen.getByRole('button', { name: /sign in/i }));

  await waitFor(() => expect(onSignIn).toHaveBeenCalledWith('owner@example.com', 'secret'));
});

test('shows authentication errors without exposing the application', async () => {
  const onSignIn = jest.fn().mockRejectedValue(new Error('Invalid login credentials'));
  render(<Login onSignIn={onSignIn} />);

  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: 'owner@example.com' } });
  fireEvent.change(screen.getByLabelText(/password/i), { target: { value: 'wrong' } });
  fireEvent.click(screen.getByRole('button', { name: /sign in/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid login credentials');
});
