import { useState } from 'react';

export default function Login({ onSignIn }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      await onSignIn(email.trim(), password);
    } catch (err) {
      setError(err?.message || 'Unable to sign in');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">Trading Journal AI</div>
        <h1>Private journal</h1>
        <p>Sign in to access your trading data.</p>
        <label>
          Email
          <input type="email" autoComplete="email" value={email}
            onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
      </form>
    </main>
  );
}
