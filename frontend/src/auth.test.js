describe('Supabase auth client', () => {
  beforeEach(() => {
    jest.resetModules();
    window.localStorage.clear();
    global.fetch = jest.fn();
    process.env.REACT_APP_AUTH_ENABLED = 'true';
    process.env.REACT_APP_SUPABASE_URL = 'https://example.supabase.co';
    process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY = 'public-key';
  });

  afterEach(() => {
    jest.restoreAllMocks();
    delete process.env.REACT_APP_AUTH_ENABLED;
    delete process.env.REACT_APP_SUPABASE_URL;
    delete process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY;
  });

  test('password sign-in persists a normalized session', async () => {
    jest.spyOn(Date, 'now').mockReturnValue(1_000_000);
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: 'access',
        refresh_token: 'refresh',
        expires_in: 3600,
        user: { id: 'owner' },
      }),
    });

    const auth = require('./auth');
    const session = await auth.signIn('owner@example.com', 'secret');

    expect(fetch).toHaveBeenCalledWith(
      'https://example.supabase.co/auth/v1/token?grant_type=password',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ apikey: 'public-key' }),
      }),
    );
    expect(session.expires_at).toBe(1_000_000 + 3_600_000);
    expect(auth.getStoredSession().access_token).toBe('access');
  });

  test('access token refreshes before expiration', async () => {
    jest.spyOn(Date, 'now').mockReturnValue(10_000);
    const auth = require('./auth');
    window.localStorage.setItem(
      auth.AUTH_SESSION_STORAGE_KEY,
      JSON.stringify({
        access_token: 'old',
        refresh_token: 'refresh',
        expires_at: 20_000,
      }),
    );
    fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        access_token: 'new',
        refresh_token: 'refresh-2',
        expires_in: 3600,
      }),
    });

    await expect(auth.getAccessToken()).resolves.toBe('new');
    expect(fetch.mock.calls[0][0]).toContain('grant_type=refresh_token');
  });

  test('failed refresh clears the unusable session', async () => {
    const auth = require('./auth');
    window.localStorage.setItem(
      auth.AUTH_SESSION_STORAGE_KEY,
      JSON.stringify({
        access_token: 'old',
        refresh_token: 'refresh',
        expires_at: 1,
      }),
    );
    fetch.mockResolvedValue({
      ok: false,
      json: async () => ({ message: 'refresh rejected' }),
    });

    await expect(auth.getAccessToken()).rejects.toThrow('refresh rejected');
    expect(auth.getStoredSession()).toBeNull();
  });

  test('sign-out clears local session even if the network logout fails', async () => {
    const auth = require('./auth');
    window.localStorage.setItem(
      auth.AUTH_SESSION_STORAGE_KEY,
      JSON.stringify({
        access_token: 'access',
        refresh_token: 'refresh',
        expires_at: Date.now() + 3_600_000,
      }),
    );
    fetch.mockRejectedValue(new Error('offline'));

    await auth.signOut();
    expect(auth.getStoredSession()).toBeNull();
  });
});
