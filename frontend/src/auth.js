const STORAGE_KEY = 'trading-journal-auth';
const AUTH_EVENT = 'trading-journal-auth-changed';
const REFRESH_EARLY_SECONDS = 300;

let refreshPromise = null;

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

async function authRequest(path, body, { method = 'POST', accessToken = '' } = {}) {
  if (!authConfigured()) throw new Error('Authentication is not configured');
  const headers = {
    apikey: publishableKey(),
    'Content-Type': 'application/json',
  };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;

  const response = await fetch(`${supabaseUrl()}/auth/v1/${path}`, {
    method,
    headers,
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

export function getPasswordRecoveryRedirectUrl() {
  const publicPath = (process.env.PUBLIC_URL || '').replace(/\/+$/, '');
  return `${window.location.origin}${publicPath}/`;
}

export async function requestPasswordReset(email) {
  const redirectTo = getPasswordRecoveryRedirectUrl();
  await authRequest(`recover?redirect_to=${encodeURIComponent(redirectTo)}`, { email });
}

export function consumePasswordRecoveryCallback() {
  const hash = window.location.hash || '';
  if (!hash.startsWith('#')) return null;

  const params = new URLSearchParams(hash.slice(1));
  if (params.get('type') !== 'recovery') return null;

  const accessToken = params.get('access_token');
  const refreshToken = params.get('refresh_token');
  if (!accessToken || !refreshToken) return null;

  const session = saveSession({
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: params.get('token_type') || 'bearer',
    expires_in: Number(params.get('expires_in') || 3600),
  });

  const cleanUrl = `${window.location.pathname}${window.location.search}`;
  window.history.replaceState({}, document.title, cleanUrl);
  return session;
}

export async function updatePassword(password) {
  const session = readSession();
  if (!session?.access_token) throw new Error('Password recovery session has expired');
  await authRequest('user', { password }, {
    method: 'PUT',
    accessToken: session.access_token,
  });
  return session;
}

async function refreshSession(session) {
  if (!session?.refresh_token) return null;
  if (refreshPromise) return refreshPromise;

  refreshPromise = (async () => {
    try {
      const refreshed = await authRequest('token?grant_type=refresh_token', {
        refresh_token: session.refresh_token,
      });
      return saveSession(refreshed);
    } catch {
      clearSession();
      return null;
    } finally {
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

function needsRefresh(session, earlySeconds = REFRESH_EARLY_SECONDS) {
  if (!session?.access_token || !session?.refresh_token) return false;
  const now = Math.floor(Date.now() / 1000);
  return Number(session.expires_at || 0) <= now + earlySeconds;
}

export function getSession() {
  return readSession();
}

export async function restoreSession() {
  if (!authConfigured()) return readSession();

  const session = readSession();
  if (!session?.access_token || !session?.refresh_token) return null;
  if (!needsRefresh(session)) return session;
  return refreshSession(session);
}

export function startSessionAutoRefresh({ onSession, onSignedOut } = {}) {
  let timer = null;
  let stopped = false;

  const schedule = () => {
    if (stopped) return;
    if (timer) clearTimeout(timer);

    const session = readSession();
    if (!session?.refresh_token) return;

    const now = Math.floor(Date.now() / 1000);
    const refreshAt = Number(session.expires_at || now) - REFRESH_EARLY_SECONDS;
    const delayMs = Math.max(5000, (refreshAt - now) * 1000);

    timer = setTimeout(async () => {
      const next = await restoreSession();
      if (stopped) return;
      if (next) onSession?.(next);
      else onSignedOut?.();
      schedule();
    }, delayMs);
  };

  const refreshWhenActive = async () => {
    if (stopped || document.visibilityState === 'hidden') return;
    const next = await restoreSession();
    if (stopped) return;
    if (next) onSession?.(next);
    else if (readSession() === null) onSignedOut?.();
    schedule();
  };

  document.addEventListener('visibilitychange', refreshWhenActive);
  window.addEventListener('focus', refreshWhenActive);
  schedule();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    document.removeEventListener('visibilitychange', refreshWhenActive);
    window.removeEventListener('focus', refreshWhenActive);
  };
}

export async function getAccessToken() {
  if (!authRequired() && !authConfigured()) return null;
  const session = await restoreSession();
  return session?.access_token || null;
}

export function clearSession() {
  localStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event(AUTH_EVENT));
}

export const AUTH_CHANGED_EVENT = AUTH_EVENT;
