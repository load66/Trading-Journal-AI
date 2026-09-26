import { useEffect, useState } from 'react';
import {
  AUTH_ENABLED,
  getAuthConfigurationError,
  getStoredSession,
  signIn,
  signOut,
  subscribeAuth,
} from './auth';

export default function AuthGate({
  children,
  authEnabled = AUTH_ENABLED,
}) {
  const [session, setSession] = useState(() =>
    authEnabled ? getStoredSession() : null
  );
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!authEnabled) return undefined;
    return subscribeAuth(setSession);
  }, [authEnabled]);

  if (!authEnabled) return children;

  const configError = getAuthConfigurationError();
  if (configError) {
    return (
      <main className="auth-screen">
        <section className="auth-card" aria-labelledby="auth-title">
          <div className="auth-kicker">Configuration required</div>
          <h1 id="auth-title">Private Trading Journal</h1>
          <p className="auth-copy">
            Hosted authentication is enabled, but the public Supabase
            configuration is incomplete.
          </p>
          <div className="notice neg" role="alert">{configError}</div>
        </section>
      </main>
    );
  }

  if (session) {
    return (
      <>
        <div className="auth-session-control">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={async () => {
              await signOut();
              setSession(null);
            }}
          >
            Sign out
          </button>
        </div>
        {children}
      </>
    );
  }

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const nextSession = await signIn(email.trim(), password);
      setSession(nextSession);
      setPassword('');
    } catch (err) {
      setError(err?.message || 'Sign in failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-kicker">Owner access</div>
        <h1 id="auth-title">Private Trading Journal</h1>
        <p className="auth-copy">
          Sign in with the owner account configured for this journal.
        </p>

        <form className="auth-form" onSubmit={handleSubmit}>
          <label>
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              required
              autoFocus
            />
          </label>

          <label>
            <span>Password</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>

          {error && (
            <div className="notice neg" role="alert">{error}</div>
          )}

          <button
            type="submit"
            className="btn btn-primary auth-submit"
            disabled={submitting}
          >
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        <p className="auth-footnote">
          New account registration is disabled for this private journal.
        </p>
      </section>
    </main>
  );
}
