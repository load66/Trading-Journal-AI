const STORAGE_KEY = 'trading-journal-auth';
const AUTH_EVENT = 'trading-journal-auth-changed';

const supabaseUrl = () => (process.env.REACT_APP_SUPABASE_URL || '').replace(/\/+$/, '');
const publishableKey = () => process.env.REACT_APP_SUPABASE_PUBLISHABLE_KEY || '';

export const authRequired = () =>
  String(process.env.REACT_APP_AUTH_REQUIRED || '').toLowerCase() === 'true';

export const authConfigured = () => Boolean(supabaseUrl() && publishableKey());

function readSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function saveSession(session) {
  const expiresAt = session.expires_at || Math.floor(Date.now() / 1000) + Number(session.expires_in || 3600);
  const stored = { ...session, expires_at: expiresAt };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  window.dispatchEvent(new Event(AUTH_EVENT));
  return stored;
}

async function authRequest(path, body) {
  if (!authConfigured()) throw new Error('Authentication is not configured');
  const response = await fetch(`${supabaseUrl()}/auth/v1/${path}`, {
    method: 'POST',
    headers: {
      apikey: publishableKey(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.msg || payload.error_description || payload.error || 'Authentication failed');
  }
  return payload;
}

export async function signInWithPassword(email, password) {
  const session = await authRequest('token?grant_type=password', { email, password });
  return saveSession(session);
}

async function refreshSession(session) {
  if (!session?.refresh_token) return null;
  try {
    const refreshed = await authRequest('token?grant_type=refresh_token', {
      refresh_token: session.refresh_token,
    });
    return saveSession(refreshed);
  } catch {
    clearSession();
    return null;
  }
}

export function getSession() {
  return readSession();
}

export async function getAccessToken() {
  if (!authRequired() && !authConfigured()) return null;
  const session = readSession();
  if (!session?.access_token) return null;
  const now = Math.floor(Date.now() / 1000);
  if (Number(session.expires_at || 0) <= now + 60) {
    const refreshed = await refreshSession(session);
    return refreshed?.access_token || null;
  }
  return session.access_token;
}

export function clearSession() {
  localStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event(AUTH_EVENT));
}

export const AUTH_CHANGED_EVENT = AUTH_EVENT;
