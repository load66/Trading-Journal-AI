import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import PasswordReset from './PasswordReset';

test('requires matching passwords before updating', async () => {
  const onUpdatePassword = jest.fn();
  render(<PasswordReset onUpdatePassword={onUpdatePassword} onCancel={jest.fn()} />);

  fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: 'abcdefgh' } });
  fireEvent.change(screen.getByLabelText(/confirm new password/i), { target: { value: 'abcdefghX' } });
  fireEvent.click(screen.getByRole('button', { name: /update password/i }));

  expect(await screen.findByRole('alert')).toHaveTextContent(/passwords do not match/i);
  expect(onUpdatePassword).not.toHaveBeenCalled();
});

test('submits a valid new password', async () => {
  const onUpdatePassword = jest.fn().mockResolvedValue(undefined);
  render(<PasswordReset onUpdatePassword={onUpdatePassword} onCancel={jest.fn()} />);

  fireEvent.change(screen.getByLabelText(/^new password$/i), { target: { value: 'abcdefgh1234' } });
  fireEvent.change(screen.getByLabelText(/confirm new password/i), { target: { value: 'abcdefgh1234' } });
  fireEvent.click(screen.getByRole('button', { name: /update password/i }));

  await waitFor(() => expect(onUpdatePassword).toHaveBeenCalledWith('abcdefgh1234'));
});
