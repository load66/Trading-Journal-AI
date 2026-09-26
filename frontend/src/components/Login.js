import { useState } from 'react';

export default function Login({ onSignIn, onResetPassword }) {
  const [mode, setMode] = useState('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    setNotice('');
    setBusy(true);
    try {
      if (mode === 'reset') {
        await onResetPassword(email.trim());
        setNotice('Password reset email sent. Open the newest email and follow the link to set a new password.');
      } else {
        await onSignIn(email.trim(), password);
      }
    } catch (err) {
      setError(err?.message || (mode === 'reset' ? 'Unable to send reset email' : 'Unable to sign in'));
    } finally {
      setBusy(false);
    }
  };

  const switchMode = (nextMode) => {
    setMode(nextMode);
    setError('');
    setNotice('');
    setPassword('');
  };

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">Trading Journal AI</div>
        <h1>{mode === 'reset' ? 'Reset password' : 'Private journal'}</h1>
        <p>
          {mode === 'reset'
            ? 'Enter your account email and we’ll send a secure link back to this journal.'
            : 'Sign in to access your trading data.'}
        </p>

        <label>
          Email
          <input type="email" autoComplete="email" value={email}
            onChange={(e) => setEmail(e.target.value)} required />
        </label>

        {mode === 'signin' && (
          <label>
            Password
            <input type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required />
          </label>
        )}

        {error && <div className="login-error" role="alert">{error}</div>}
        {notice && <div className="login-notice" role="status">{notice}</div>}

        <button type="submit" disabled={busy}>
          {busy
            ? (mode === 'reset' ? 'Sending…' : 'Signing in…')
            : (mode === 'reset' ? 'Send reset link' : 'Sign in')}
        </button>

        <button
          type="button"
          className="login-link"
          onClick={() => switchMode(mode === 'reset' ? 'signin' : 'reset')}
          disabled={busy}
        >
          {mode === 'reset' ? 'Back to sign in' : 'Forgot password?'}
        </button>
      </form>
    </main>
  );
}
