import { useEffect, useMemo, useState } from 'react';
import { CheckCircle2, RefreshCw, ShieldCheck, Sparkles } from 'lucide-react';
import { tradesApi } from '../api';

const money = (value) => value == null ? '—' : `$${Number(value).toFixed(2)}`;
const pct = (value) => value == null ? '—' : `${Number(value).toFixed(2)}%`;
const confidenceLabel = (value) => {
  if (value == null) return 'Unknown';
  if (value >= 90) return 'High';
  if (value >= 70) return 'Medium';
  return 'Low';
};

const feedLabel = (feed) => {
  const value = String(feed || '').toLowerCase();
  if (value === 'sip') return 'SIP · consolidated';
  if (value === 'delayed_sip') return 'Delayed SIP · consolidated';
  if (value === 'iex') return 'IEX · fallback';
  return feed ? String(feed).toUpperCase() : 'Unknown';
};

const levelStatusLabel = (meta) => {
  if (!meta) return 'UNVERIFIED';
  const status = String(meta.status || 'UNVERIFIED').replaceAll('_', ' ');
  const review = meta.review_required ? ' · REVIEW EXTREME' : '';
  const feed = feedLabel(meta.feed);
  return `${status}${review} · ${feed}`;
};

const levelAuditLabel = (meta) => {
  if (!meta?.review_required) return null;
  const source = meta.source_bar?.time_et
    ? new Date(meta.source_bar.time_et).toLocaleTimeString([], {
        hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York'
      }) + ' ET'
    : 'unknown minute';
  const next = meta.next_distinct_extreme == null ? '—' : money(meta.next_distinct_extreme);
  const gap = meta.gap_to_next == null ? '—' : money(meta.gap_to_next);
  return `Source ${source} · next extreme ${next} · gap ${gap}`;
};

function EvidenceRow({ label, value, tone }) {
  if (value == null || value === '') return null;
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
      gap: 12, padding: '7px 0', borderBottom: '1px solid var(--divider-soft)',
    }}>
      <span style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{label}</span>
      <span className="num" style={{
        textAlign: 'right', fontSize: 13, fontWeight: 600,
        color: tone || 'var(--text-primary)',
      }}>{value}</span>
    </div>
  );
}

function ReviewTag({ tag, existing, applying, onApply }) {
  return (
    <div style={{
      border: '1px solid var(--divider)', borderRadius: 'var(--radius-md)',
      padding: 12, background: 'var(--surface-inset)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'flex-start' }}>
        <div>
          <div style={{ display: 'flex', gap: 7, alignItems: 'center', flexWrap: 'wrap' }}>
            <span className="chip">{tag.tag_value}</span>
            <span className="text-muted" style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.04em' }}>
              {tag.tag_type}
            </span>
            {tag.source === 'rule' ? (
              <span className="text-muted" style={{ fontSize: 11.5 }}>RULE</span>
            ) : tag.confidence != null ? (
              <span className="text-muted" style={{ fontSize: 11.5 }}>AI confidence: {confidenceLabel(tag.confidence)}</span>
            ) : null}
          </div>
          {tag.reason && (
            <div style={{ fontSize: 13, lineHeight: 1.5, marginTop: 7, color: 'var(--text-secondary)' }}>
              {tag.reason}
            </div>
          )}
        </div>
        <button
          type="button"
          className={existing ? 'btn btn-ghost btn-sm' : 'btn btn-secondary btn-sm'}
          disabled={existing || applying}
          onClick={onApply}
          style={{ flexShrink: 0 }}
        >
          {existing ? <><CheckCircle2 size={13} /> Applied</> : applying ? 'Applying…' : 'Apply'}
        </button>
      </div>
    </div>
  );
}

export default function LEReview({ trade, analysis, tags, onAnalysisChange, onTagsChange }) {
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [applying, setApplying] = useState('');
  const [batchApplying, setBatchApplying] = useState(false);

  const load = () => {
    setLoading(true);
    setError('');
    tradesApi.getLeReview(trade.trade_group)
      .then(r => setReview(r.data))
      .catch(e => setError(e?.response?.data?.detail || e.message || 'Could not load LE review.'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setReview(null);
    load();
    // trade_group is the identity of the review target.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trade.trade_group]);

  const existing = useMemo(
    () => new Set((tags || []).map(t => `${t.tag_type}::${t.tag_value}`)),
    [tags],
  );

  const applyTag = async (tag) => {
    const key = `${tag.tag_type}::${tag.tag_value}`;
    if (existing.has(key)) return;
    setApplying(key);
    setError('');
    try {
      const r = await tradesApi.addTag(trade.trade_group, {
        tag_type: tag.tag_type,
        tag_value: tag.tag_value,
      });
      onTagsChange?.(prev => [...prev, r.data]);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Could not apply tag.');
    } finally {
      setApplying('');
    }
  };

  const applyProven = async () => {
    const pending = (review?.auto_tags || []).filter(
      t => !existing.has(`${t.tag_type}::${t.tag_value}`)
    );
    if (!pending.length) return;
    setBatchApplying(true);
    setError('');
    try {
      const added = [];
      for (const tag of pending) {
        const r = await tradesApi.addTag(trade.trade_group, {
          tag_type: tag.tag_type,
          tag_value: tag.tag_value,
        });
        added.push(r.data);
      }
      if (added.length) onTagsChange?.(prev => [...prev, ...added]);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Could not apply rule tags.');
    } finally {
      setBatchApplying(false);
    }
  };

  const applyStrategy = async () => {
    const suggestion = review?.ai?.strategy;
    if (!suggestion || suggestion.value === 'NONE' || suggestion.value === analysis?.strategy) return;
    setApplying('strategy');
    setError('');
    try {
      const r = await tradesApi.updateAnalysis(trade.trade_group, { strategy: suggestion.value });
      onAnalysisChange?.(r.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Could not apply strategy.');
    } finally {
      setApplying('');
    }
  };

  if (loading) {
    return <div className="text-muted" role="status" style={{ paddingTop: 12 }}>Building LE evidence from market data…</div>;
  }

  if (error && !review) {
    return (
      <div style={{ paddingTop: 10 }}>
        <div className="notice neg" role="alert">{error}</div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={load} style={{ marginTop: 10 }}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  if (!review?.available) {
    return (
      <div style={{ paddingTop: 10 }}>
        <div className="notice caution">
          <strong>LE review unavailable.</strong>{' '}
          {review?.reason || 'The required evidence could not be established.'}
        </div>
        {(review?.data_warnings || []).map((w, i) => (
          <div key={i} className="text-muted" style={{ fontSize: 13, marginTop: 7 }}>{w}</div>
        ))}
        <button type="button" className="btn btn-ghost btn-sm" onClick={load} style={{ marginTop: 10 }}>
          <RefreshCw size={13} /> Refresh evidence
        </button>
      </div>
    );
  }

  const ev = review.evidence || {};
  const levels = ev.levels || {};
  const levelMeta = ev.level_meta || {};
  const breaks = ev.level_breaks_before_entry || {};
  const proven = review.auto_tags || [];
  const ai = review.ai;
  const strategy = ai?.strategy;
  const aiTags = ai?.suggested_tags || [];
  const allProvenApplied = proven.length > 0 && proven.every(
    t => existing.has(`${t.tag_type}::${t.tag_value}`)
  );

  const breakSummary = ['PDH', 'PDL', 'PMH', 'PML']
    .filter(k => breaks[k])
    .join(', ') || 'None confirmed';

  const benchmark = (item) => {
    if (!item || item.price == null || item.vwap == null) return 'Unknown';
    const pos = item.position_vs_vwap;
    const relation = pos === 'above' ? 'Above VWAP' : pos === 'below' ? 'Below VWAP' : 'At VWAP';
    return `${relation} · Price ${money(item.price)} · VWAP ${money(item.vwap)}`;
  };

  const marketSign = ev.market_sign?.status || 'unknown';
  const marketSignLabel = marketSign === 'confirmed'
    ? 'CONFIRMED'
    : marketSign === 'failed'
      ? 'FAILED'
      : marketSign === 'mixed'
        ? 'MIXED'
        : 'UNKNOWN';
  const marketSignTone = marketSign === 'confirmed'
    ? 'var(--result-pos)'
    : marketSign === 'failed'
      ? 'var(--result-neg)'
      : marketSign === 'mixed'
        ? 'var(--warning)'
        : undefined;

  return (
    <div style={{ paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="notice accent" style={{ fontSize: 13, lineHeight: 1.55 }}>
        <strong>Review mode:</strong> nothing is saved automatically. Rule-derived tags come from deterministic LE conditions using the configured market feed;
        Groq suggestions are interpretation only and require your approval.
      </div>

      {error && <div className="notice neg" role="alert">{error}</div>}

      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 7 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
            <ShieldCheck size={16} />
            <strong style={{ fontSize: 14 }}>LE market evidence</strong>
          </div>
          <span className="chip">{review.ruleset_version}</span>
        </div>
        <EvidenceRow label="Directional thesis" value={ev.direction?.toUpperCase()} />
        <EvidenceRow label="Entry time" value={ev.entry_time_et ? new Date(ev.entry_time_et).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' }) + ' ET' : '—'} />
        <EvidenceRow label="Broker time zone" value={ev.execution_time_zone} />
        <EvidenceRow label="Session" value={(ev.session_window || '').replaceAll('_', ' ')} />
        <EvidenceRow label="PDH" value={`${money(levels.PDH)} · ${levelStatusLabel(levelMeta.PDH)}`} />
        {levelAuditLabel(levelMeta.PDH) && <EvidenceRow label="PDH audit" value={levelAuditLabel(levelMeta.PDH)} tone="var(--warning)" />}
        <EvidenceRow label="PDL" value={`${money(levels.PDL)} · ${levelStatusLabel(levelMeta.PDL)}`} />
        {levelAuditLabel(levelMeta.PDL) && <EvidenceRow label="PDL audit" value={levelAuditLabel(levelMeta.PDL)} tone="var(--warning)" />}
        <EvidenceRow label="PMH" value={`${money(levels.PMH)} · ${levelStatusLabel(levelMeta.PMH)}`} />
        {levelAuditLabel(levelMeta.PMH) && <EvidenceRow label="PMH audit" value={levelAuditLabel(levelMeta.PMH)} tone="var(--warning)" />}
        <EvidenceRow label="PML" value={`${money(levels.PML)} · ${levelStatusLabel(levelMeta.PML)}`} />
        {levelAuditLabel(levelMeta.PML) && <EvidenceRow label="PML audit" value={levelAuditLabel(levelMeta.PML)} tone="var(--warning)" />}
        <EvidenceRow label="Official market calendar" value={ev.market_calendar_verified ? 'VERIFIED' : 'UNVERIFIED'} />
        <EvidenceRow label="Level breaks before entry" value={breakSummary} />
        <EvidenceRow label="Last completed 1m close" value={money(ev.underlying_price_last_completed_1m)} />
        <EvidenceRow label="Last completed 10m 8 EMA" value={`${money(ev.ema8_10m_last_completed)} · ${String(ev.ema_integrity_status || 'UNVERIFIED').replaceAll('_', ' ')}`} />
        <EvidenceRow label="Distance from 8 EMA" value={pct(ev.ema_distance_pct)} tone={ev.ema_distance_pct > 1 ? 'var(--result-neg)' : undefined} />
        <EvidenceRow label="SPY vs VWAP" value={benchmark(ev.spy)} />
        <EvidenceRow label="QQQ vs VWAP" value={benchmark(ev.qqq)} />
        <EvidenceRow
          label="Market Sign"
          value={ev.market_sign?.integrity_status === 'VERIFIED'
            ? marketSignLabel
            : `UNVERIFIED · observed ${String(ev.market_sign?.observed_status || 'unknown').toUpperCase()}`}
          tone={ev.market_sign?.integrity_status === 'VERIFIED' ? marketSignTone : undefined}
        />
        <EvidenceRow label="Market data feed" value={feedLabel(ev.market_data_feed?.underlying)} />
        <EvidenceRow
          label="Verified evidence coverage"
          value={ev.evidence_quality ? `${ev.evidence_quality.level} · ${ev.evidence_quality.completeness_pct}% trusted inputs` : 'Unknown'}
        />
      </div>

      {ev.evidence_quality?.reason && (
        <div className="text-muted" style={{ fontSize: 12.5, lineHeight: 1.5 }}>
          Evidence integrity: {ev.evidence_quality.reason}
        </div>
      )}

      {(review.data_warnings || []).length > 0 && (
        <div className="notice caution" style={{ fontSize: 13 }}>
          <strong>Evidence limitations</strong>
          <div style={{ marginTop: 5 }}>
            {review.data_warnings.map((w, i) => <div key={i}>• {w}</div>)}
          </div>
        </div>
      )}

      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, marginBottom: 8 }}>
          <strong style={{ fontSize: 14 }}>Rule-derived tags</strong>
          {proven.length > 0 && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={applyProven}
              disabled={batchApplying || allProvenApplied}
            >
              {allProvenApplied ? <><CheckCircle2 size={13} /> Applied</> : batchApplying ? 'Applying…' : 'Apply rule tags'}
            </button>
          )}
        </div>
        {proven.length ? (
          <div style={{ display: 'grid', gap: 8 }}>
            {proven.map(tag => {
              const key = `${tag.tag_type}::${tag.tag_value}`;
              return (
                <ReviewTag
                  key={key}
                  tag={tag}
                  existing={existing.has(key)}
                  applying={applying === key}
                  onApply={() => applyTag(tag)}
                />
              );
            })}
          </div>
        ) : (
          <div className="text-muted" style={{ fontSize: 13 }}>No deterministic LE tags were derived from the available evidence.</div>
        )}
      </div>

      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 8 }}>
          <Sparkles size={16} />
          <strong style={{ fontSize: 14 }}>Groq interpretation</strong>
          {ai?.provider && <span className="chip">{ai.provider} · {ai.model}</span>}
        </div>

        {!ai?.available ? (
          <div className="text-muted" style={{ fontSize: 13 }}>
            Groq classification is unavailable. Deterministic evidence above is still valid.
          </div>
        ) : (
          <>
            <div style={{
              border: '1px solid var(--divider)', borderRadius: 'var(--radius-md)',
              padding: 12, background: 'var(--surface-inset)', marginBottom: 10,
            }}>
              <div className="field-label" style={{ marginBottom: 5 }}>Suggested strategy</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 700 }}>
                    {strategy?.value === 'NONE' ? 'No strategy suggested' : strategy?.value}
                    {strategy?.confidence != null && <span className="text-muted" style={{ marginLeft: 7, fontSize: 12 }}>AI confidence: {confidenceLabel(strategy.confidence)}</span>}
                  </div>
                  {strategy?.reason && <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.5, marginTop: 5 }}>{strategy.reason}</div>}
                </div>
                {strategy?.value && strategy.value !== 'NONE' && (
                  <button
                    type="button"
                    className={analysis?.strategy === strategy.value ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm'}
                    disabled={analysis?.strategy === strategy.value || applying === 'strategy'}
                    onClick={applyStrategy}
                    style={{ flexShrink: 0 }}
                  >
                    {analysis?.strategy === strategy.value ? <><CheckCircle2 size={13} /> Applied</> : applying === 'strategy' ? 'Applying…' : 'Apply strategy'}
                  </button>
                )}
              </div>
            </div>

            <div style={{ display: 'grid', gap: 8 }}>
              {aiTags.map(tag => {
                const key = `${tag.tag_type}::${tag.tag_value}`;
                return (
                  <ReviewTag
                    key={key}
                    tag={tag}
                    existing={existing.has(key)}
                    applying={applying === key}
                    onApply={() => applyTag(tag)}
                  />
                );
              })}
            </div>
            {aiTags.length === 0 && (
              <div className="text-muted" style={{ fontSize: 13 }}>No additional AI tags were suggested.</div>
            )}

            {(ai.insufficient_evidence || []).length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="field-label" style={{ marginBottom: 4 }}>Insufficient evidence</div>
                {ai.insufficient_evidence.map((item, i) => (
                  <div key={i} className="text-muted" style={{ fontSize: 12.5, marginTop: 3 }}>• {item}</div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      <button type="button" className="btn btn-ghost btn-sm" onClick={load} style={{ alignSelf: 'flex-start' }}>
        <RefreshCw size={13} /> Refresh evidence
      </button>
    </div>
  );
}
