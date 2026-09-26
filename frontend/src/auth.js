export const AUTH_ENABLED =
  String(process.env.REACT_APP_AUTH_ENABLED || '').toLowerCase() === 'true';

export const AUTH_SESSION_STORAGE_KEY = 'trading-journal.auth.session.v1';

const SUPABASE_URL = (process.env.REACT_APP_SUPABASE_URL || '').replace(/\/+$/, '');
const SUPABASE_PUBLISHABLE_KEY =
  process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY || '';
const REFRESH_SKEW_MS = 60_000;

const subscribers = new Set();
let refreshPromise = null;

function storageAvailable() {
  return typeof window !== 'undefined' && window.localStorage;
}

function notify(session) {
  subscribers.forEach((listener) => {
    try {
      listener(session);
    } catch (_) {
      // A UI listener cannot be allowed to break session persistence.
    }
  });
}

export function getAuthConfigurationError() {
  if (!AUTH_ENABLED) return null;
  if (!SUPABASE_URL) return 'REACT_APP_SUPABASE_URL is missing.';
  if (!SUPABASE_PUBLISHABLE_KEY) {
    return 'REACT_APP_SUPABASE_PUBLISHABLE_KEY is missing.';
  }
  return null;
}

export function getStoredSession() {
  if (!storageAvailable()) return null;
  const raw = window.localStorage.getItem(AUTH_SESSION_STORAGE_KEY);
  if (!raw) return null;

  try {
    const session = JSON.parse(raw);
    return session?.access_token ? session : null;
  } catch (_) {
    window.localStorage.removeItem(AUTH_SESSION_STORAGE_KEY);
    return null;
  }
}

function normalizeSession(payload, fallbackRefreshToken = null) {
  const rawExpiry = Number(payload.expires_at || 0);
  const expiresAt = rawExpiry > 0
    ? (rawExpiry < 1_000_000_000_000 ? rawExpiry * 1000 : rawExpiry)
    : Date.now() + Number(payload.expires_in || 3600) * 1000;

  return {
    ...payload,
    refresh_token: payload.refresh_token || fallbackRefreshToken,
    expires_at: expiresAt,
  };
}

function persistSession(payload, fallbackRefreshToken = null) {
  const session = normalizeSession(payload, fallbackRefreshToken);
  if (!session.access_token) {
    throw new Error('Authentication response did not include an access token.');
  }
  if (storageAvailable()) {
    window.localStorage.setItem(
      AUTH_SESSION_STORAGE_KEY,
      JSON.stringify(session),
    );
  }
  notify(session);
  return session;
}

export function clearSession() {
  if (storageAvailable()) {
    window.localStorage.removeItem(AUTH_SESSION_STORAGE_KEY);
  }
  notify(null);
}

async function parseResponse(response) {
  try {
    return await response.json();
  } catch (_) {
    return {};
  }
}

function authError(data, fallback) {
  return (
    data?.message
    || data?.msg
    || data?.error_description
    || data?.error
    || fallback
  );
}

async function tokenRequest(grantType, body) {
  const configError = getAuthConfigurationError();
  if (configError) throw new Error(configError);

  const response = await fetch(
    `${SUPABASE_URL}/auth/v1/token?grant_type=${grantType}`,
    {
      method: 'POST',
      headers: {
        apikey: SUPABASE_PUBLISHABLE_KEY,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    },
  );
  const data = await parseResponse(response);
  if (!response.ok) {
    throw new Error(authError(data, 'Authentication failed.'));
  }
  return data;
}

export async function signIn(email, password) {
  const data = await tokenRequest('password', { email, password });
  return persistSession(data);
}

async function refreshSession(session) {
  if (!session?.refresh_token) {
    clearSession();
    throw new Error('Your session has expired. Please sign in again.');
  }

  if (!refreshPromise) {
    refreshPromise = tokenRequest(
      'refresh_token',
      { refresh_token: session.refresh_token },
    )
      .then((data) => persistSession(data, session.refresh_token))
      .catch((error) => {
        clearSession();
        throw error;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

export async function getAccessToken() {
  if (!AUTH_ENABLED) return null;

  const session = getStoredSession();
  if (!session) return null;

  if (Number(session.expires_at || 0) > Date.now() + REFRESH_SKEW_MS) {
    return session.access_token;
  }

  const refreshed = await refreshSession(session);
  return refreshed.access_token;
}

export async function signOut() {
  const session = getStoredSession();
  clearSession();

  if (
    !session?.access_token
    || !SUPABASE_URL
    || !SUPABASE_PUBLISHABLE_KEY
  ) {
    return;
  }

  try {
    await fetch(`${SUPABASE_URL}/auth/v1/logout`, {
      method: 'POST',
      headers: {
        apikey: SUPABASE_PUBLISHABLE_KEY,
        Authorization: `Bearer ${session.access_token}`,
      },
    });
  } catch (_) {
    // Local logout is authoritative; remote token expiry/revocation is best effort.
  }
}

export function subscribeAuth(listener) {
  subscribers.add(listener);
  return () => subscribers.delete(listener);
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === AUTH_SESSION_STORAGE_KEY) {
      notify(getStoredSession());
    }
  });
}
