import { authConfigured, signInWithPassword, getAccessToken, clearSession } from './auth';

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
    delete process.env.REACT_APP_SUPABASE_ANON_KEY;
    expect(authConfigured()).toBe(false);
  });

  test('requires both public Supabase build variables', () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    delete process.env.REACT_APP_SUPABASE_ANON_KEY;
    expect(authConfigured()).toBe(false);

    process.env.REACT_APP_SUPABASE_ANON_KEY = 'public-anon-key';
    expect(authConfigured()).toBe(true);
  });

  test('sign in persists the returned session and exposes its access token', async () => {
    process.env.REACT_APP_SUPABASE_URL = 'https://project.supabase.co';
    process.env.REACT_APP_SUPABASE_ANON_KEY = 'public-anon-key';
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
