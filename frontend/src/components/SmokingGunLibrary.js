import { useCallback, useEffect, useState } from 'react';
import { smokingGunLibraryApi } from '../api';
import { PanelHead } from './ui';

const fmt$ = (value) => {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  return (n > 0 ? '+' : '') + n.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  });
};

const populationLabel = (filters) => {
  const parts = [];
  if (filters?.tickers?.length) parts.push(filters.tickers.join(', '));
  if (filters?.instrument_types?.length) parts.push(filters.instrument_types.join(', '));
  return parts.length ? parts.join(' · ') : 'All stored trades in range';
};

const safeFilename = (headers, fallback) => {
  const raw = headers?.['content-disposition'] || headers?.get?.('content-disposition') || '';
  const match = /filename="?([^";]+)"?/i.exec(raw);
  return match?.[1] || fallback;
};

const downloadBlob = (blob, filename) => {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
};

export default function SmokingGunLibrary({ accountId, onOpen }) {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [confirmId, setConfirmId] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(() => {
    let active = true;
    setLoading(true);
    setError('');
    const params = accountId == null ? {} : { account_id: accountId };
    smokingGunLibraryApi.list(params)
      .then((response) => {
        if (active) setReports(response.data || []);
      })
      .catch((err) => {
        if (active) setError(err?.response?.data?.detail || err?.message || 'Could not load saved reports.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [accountId]);

  useEffect(() => load(), [load]);

  const handleDownload = async (report, kind) => {
    try {
      setBusyId(report.id);
      setError('');
      const response = kind === 'html'
        ? await smokingGunLibraryApi.downloadHtml(report.id)
        : await smokingGunLibraryApi.downloadLedger(report.id);
      const fallback = kind === 'html'
        ? 'smoking-gun-' + report.date_from + '-' + report.date_to + '.html'
        : 'smoking-gun-ledger-' + report.date_from + '-' + report.date_to + '.csv';
      downloadBlob(response.data, safeFilename(response.headers, fallback));
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Could not download report.');
    } finally {
      setBusyId(null);
    }
  };

  const handleDelete = async (reportId) => {
    try {
      setBusyId(reportId);
      setError('');
      await smokingGunLibraryApi.remove(reportId);
      setReports((current) => current.filter((report) => report.id !== reportId));
      setConfirmId(null);
    } catch (err) {
      setError(err?.response?.data?.detail || err?.message || 'Could not delete report.');
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="card" aria-label="Smoking Gun Report Library">
      <PanelHead
        title="Report Library"
        sub="Saved immutable audit snapshots. Library rows stay lightweight until you open a report."
      />

      {error && <div className="notice caution" role="alert">{error}</div>}

      {loading ? (
        <div className="skeleton" style={{ height: 180 }} />
      ) : reports.length === 0 ? (
        <div className="empty">
          No saved Smoking Gun reports yet. Generate one from ChatGPT to build your audit history.
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 12 }}>
          {reports.map((report) => (
            <article
              key={report.id}
              aria-label={'Smoking Gun report: ' + report.title}
              className="card"
              style={{ padding: 16, background: 'var(--surface-inset)' }}
            >
              <div style={{ display: 'flex', gap: 12, justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                <div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                    <strong style={{ fontSize: 16 }}>{report.title}</strong>
                    <span className={'chip ' + (report.is_stale ? 'neg' : 'pos')}>
                      {report.is_stale ? 'SOURCE CHANGED' : 'CURRENT'}
                    </span>
                    <span className="v3-thin">v{report.report_version}</span>
                  </div>
                  <div className="text-muted" style={{ marginTop: 5, fontSize: 12 }}>
                    {report.date_from} → {report.date_to} · Generated {report.generated_at}
                  </div>
                  <div className="text-muted" style={{ marginTop: 3, fontSize: 12 }}>
                    Population: {populationLabel(report.filters)}
                  </div>
                </div>
                <div className={'num ' + (Number(report.net_pnl) >= 0 ? 'pos' : 'neg')} style={{ fontSize: 22, fontWeight: 700 }}>
                  {fmt$(report.net_pnl)}
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 8, marginTop: 14 }}>
                <div><span className="text-muted">Trades</span><div className="num">{report.trade_count ?? '—'}</div></div>
                <div><span className="text-muted">Primary edge</span><div>{report.primary_edge || '—'}</div></div>
                <div><span className="text-muted">Primary leak</span><div>{report.primary_leak || '—'}</div></div>
                <div><span className="text-muted">Status</span><div>{report.status || 'complete'}</div></div>
              </div>

              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 16 }}>
                <button type="button" className="btn btn-primary" onClick={() => onOpen?.(report.id)}>
                  Open Report
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={busyId === report.id}
                  onClick={() => handleDownload(report, 'html')}
                >
                  Download HTML
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={busyId === report.id}
                  onClick={() => handleDownload(report, 'csv')}
                >
                  Download Trade Ledger
                </button>
                <button type="button" className="btn" onClick={() => setConfirmId(report.id)}>
                  Delete Report
                </button>
              </div>

              {confirmId === report.id && (
                <div className="notice caution" style={{ marginTop: 12 }}>
                  <div>Delete this saved snapshot? Source trades will not be changed.</div>
                  <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                    <button
                      type="button"
                      className="btn btn-danger"
                      disabled={busyId === report.id}
                      onClick={() => handleDelete(report.id)}
                    >
                      Confirm delete
                    </button>
                    <button type="button" className="btn" onClick={() => setConfirmId(null)}>Cancel</button>
                  </div>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
