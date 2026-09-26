import { authConfigured, signInWithPassword, getAccessToken, clearSession, requestPasswordReset, consumePasswordRecoveryCallback, updatePassword, restoreSession } from './auth';

describe('web authentication client', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    process.env = { ...originalEnv };
    localStorage.clear();
    global.fetch = jest.fn();
  });

  afterEach(() => {
    process.env = originalEnv;
    jest.restoreAllMocks();
  });

  test('is disabled for local development without Supabase build variables', () => {
    delete process.env.REACT_APP_SUPABASE_URL;
    delete process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY;
    expect(authConfigured()).toBe(false);
  });

  test('requires both public Supabase build variables', () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    delete process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY;
    expect(authConfigured()).toBe(false);

    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-anon-key';
    expect(authConfigured()).toBe(true);
  });

  test('sign in persists the returned session and exposes its access token', async () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-anon-key';
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: 'access-123',
        refresh_token: 'refresh-123',
        expires_in: 3600,
        user: { id: 'owner' },
      }),
    });

    await signInWithPassword('owner@example.com', 'secret');

    expect(fetch).toHaveBeenCalledWith(
      'https://project.supabase.co/auth/v1/token?grant_type=password',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ apikey: 'public-anon-key' }),
      }),
    );
    expect(await getAccessToken()).toBe('access-123');
  });


  test('password reset email explicitly redirects back to the deployed app', async () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-anon-key';
    process.env.PUBLIC_URL = '/Trading-Journal-AI';
    fetch.mockResolvedValue({ ok: true, json: async () => ({}) });

    await requestPasswordReset('owner@example.com');

    expect(fetch).toHaveBeenCalledWith(
      'https://project.supabase.co/auth/v1/recover?redirect_to=' +
        encodeURIComponent('http://localhost/Trading-Journal-AI/'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ email: 'owner@example.com' }),
      }),
    );
  });

  test('consumes a Supabase recovery callback and removes tokens from the URL', () => {
    window.history.replaceState({}, '', '/Trading-Journal-AI/#access_token=recovery-access&refresh_token=recovery-refresh&expires_in=3600&token_type=bearer&type=recovery');

    const session = consumePasswordRecoveryCallback();

    expect(session.access_token).toBe('recovery-access');
    expect(JSON.parse(localStorage.getItem('trading-journal-auth')).refresh_token).toBe('recovery-refresh');
    expect(window.location.hash).toBe('');
  });

  test('updates password with the recovery access token', async () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-anon-key';
    localStorage.setItem('trading-journal-auth', JSON.stringify({
      access_token: 'recovery-access',
      refresh_token: 'recovery-refresh',
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    }));
    fetch.mockResolvedValue({ ok: true, json: async () => ({ id: 'owner' }) });

    await updatePassword('new-password-123');

    expect(fetch).toHaveBeenCalledWith(
      'https://project.supabase.co/auth/v1/user',
      expect.objectContaining({
        method: 'PUT',
        headers: expect.objectContaining({
          apikey: 'public-anon-key',
          Authorization: 'Bearer recovery-access',
        }),
        body: JSON.stringify({ password: 'new-password-123' }),
      }),
    );
  });


  test('restores a valid persisted session without refreshing it', async () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-anon-key';
    localStorage.setItem('trading-journal-auth', JSON.stringify({
      access_token: 'still-valid',
      refresh_token: 'refresh-valid',
      expires_at: Math.floor(Date.now() / 1000) + 1800,
    }));

    const session = await restoreSession();

    expect(session.access_token).toBe('still-valid');
    expect(fetch).not.toHaveBeenCalled();
  });

  test('refreshes a persisted session before access token expiry', async () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-anon-key';
    localStorage.setItem('trading-journal-auth', JSON.stringify({
      access_token: 'expiring',
      refresh_token: 'refresh-old',
      expires_at: Math.floor(Date.now() / 1000) + 120,
    }));
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: 'fresh-access',
        refresh_token: 'fresh-refresh',
        expires_in: 3600,
      }),
    });

    const session = await restoreSession();

    expect(session.access_token).toBe('fresh-access');
    expect(fetch).toHaveBeenCalledWith(
      'https://project.supabase.co/auth/v1/token?grant_type=refresh_token',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ refresh_token: 'refresh-old' }),
      }),
    );
  });

  test('serializes concurrent refresh attempts so one refresh token is not reused', async () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-anon-key';
    localStorage.setItem('trading-journal-auth', JSON.stringify({
      access_token: 'expiring',
      refresh_token: 'refresh-old',
      expires_at: Math.floor(Date.now() / 1000) + 60,
    }));
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: 'fresh-access',
        refresh_token: 'fresh-refresh',
        expires_in: 3600,
      }),
    });

    const [one, two] = await Promise.all([getAccessToken(), getAccessToken()]);

    expect(one).toBe('fresh-access');
    expect(two).toBe('fresh-access');
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  test('clearSession removes browser authentication state', async () => {
    localStorage.setItem('trading-journal-auth', JSON.stringify({
      access_token: 'access-123',
      refresh_token: 'refresh-123',
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    }));
    clearSession();
    expect(await getAccessToken()).toBeNull();
  });
});
