import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import AuthGate from './AuthGate';
import * as auth from './auth';

jest.mock('./auth', () => ({
  AUTH_ENABLED: true,
  getAuthConfigurationError: jest.fn(() => null),
  getStoredSession: jest.fn(() => null),
  getAccessToken: jest.fn(() => Promise.resolve(null)),
  signIn: jest.fn(),
  signOut: jest.fn(() => Promise.resolve()),
  subscribeAuth: jest.fn(() => () => {}),
}));

beforeEach(() => {
  jest.clearAllMocks();
  auth.getAuthConfigurationError.mockReturnValue(null);
  auth.getStoredSession.mockReturnValue(null);
  auth.subscribeAuth.mockImplementation(() => () => {});
});

test('auth-disabled mode renders the journal directly', () => {
  render(
    <AuthGate authEnabled={false}>
      <div>Journal</div>
    </AuthGate>,
  );
  expect(screen.getByText('Journal')).toBeInTheDocument();
  expect(screen.queryByRole('form')).not.toBeInTheDocument();
});

test('auth-enabled mode shows login and no registration control', () => {
  render(
    <AuthGate authEnabled>
      <div>Journal</div>
    </AuthGate>,
  );
  expect(screen.getByRole('heading', { name: /Private Trading Journal/i })).toBeInTheDocument();
  expect(screen.getByLabelText('Email')).toBeInTheDocument();
  expect(screen.getByLabelText('Password')).toBeInTheDocument();
  expect(screen.queryByText(/sign up|register/i)).not.toBeInTheDocument();
  expect(screen.queryByText('Journal')).not.toBeInTheDocument();
});

test('successful login unlocks the journal', async () => {
  auth.signIn.mockResolvedValue({
    access_token: 'access',
    refresh_token: 'refresh',
    expires_at: Date.now() + 3_600_000,
  });

  render(
    <AuthGate authEnabled>
      <div>Journal</div>
    </AuthGate>,
  );

  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: 'owner@example.com' },
  });
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'secret' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

  expect(await screen.findByText('Journal')).toBeInTheDocument();
  expect(auth.signIn).toHaveBeenCalledWith('owner@example.com', 'secret');
});

test('logout clears the authenticated view', async () => {
  auth.getStoredSession.mockReturnValue({
    access_token: 'access',
    refresh_token: 'refresh',
    expires_at: Date.now() + 3_600_000,
  });

  render(
    <AuthGate authEnabled>
      <div>Journal</div>
    </AuthGate>,
  );

  expect(screen.getByText('Journal')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));

  await waitFor(() => expect(auth.signOut).toHaveBeenCalled());
  expect(await screen.findByRole('heading', { name: /Private Trading Journal/i })).toBeInTheDocument();
});
