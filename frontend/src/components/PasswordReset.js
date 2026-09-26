import { useState } from 'react';

export default function PasswordReset({ onUpdatePassword, onCancel }) {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError('');

    if (password.length < 8) {
      setError('Use at least 8 characters.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match.');
      return;
    }

    setBusy(true);
    try {
      await onUpdatePassword(password);
    } catch (err) {
      setError(err?.message || 'Unable to update password');
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">Trading Journal AI</div>
        <h1>Set new password</h1>
        <p>Create a new password for your private journal account.</p>

        <label>
          New password
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </label>

        <label>
          Confirm new password
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            minLength={8}
            required
          />
        </label>

        {error && <div className="login-error" role="alert">{error}</div>}

        <button type="submit" disabled={busy}>
          {busy ? 'Updating…' : 'Update password'}
        </button>
        <button type="button" className="login-link" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </form>
    </main>
  );
}
