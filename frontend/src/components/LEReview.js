import { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Gauge,
  MinusCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  TrendingUp,
  XCircle,
} from 'lucide-react';
import { tradesApi } from '../api';
import TradingChart from './TradingChart';

const money = (value) => value == null ? '—' : `$${Number(value).toFixed(2)}`;
const pct = (value, digits = 2) => value == null ? '—' : `${Number(value).toFixed(digits)}%`;

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

const cleanLabel = (value) => String(value || 'unknown')
  .replaceAll('_', ' ')
  .replace(/\b\w/g, char => char.toUpperCase());

const timeEt = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'America/New_York',
  })} ET`;
};

const levelStatusLabel = (meta) => {
  if (!meta) return 'UNVERIFIED';
  const status = String(meta.status || 'UNVERIFIED').replaceAll('_', ' ');
  return `${status} · ${feedLabel(meta.feed)}`;
};

const parseExecutions = (trade) => {
  if (Array.isArray(trade?.executions)) return trade.executions;
  try {
    const parsed = JSON.parse(trade?.executions || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
};

const checkVisual = (status) => {
  if (status === 'pass') return { icon: CheckCircle2, tone: 'pass', label: 'Pass' };
  if (status === 'fail') return { icon: XCircle, tone: 'fail', label: 'Fail' };
  if (status === 'caution') return { icon: CircleAlert, tone: 'caution', label: 'Caution' };
  return { icon: MinusCircle, tone: 'neutral', label: 'Unverified' };
};

function CheckRow({ label, item }) {
  const visual = checkVisual(item?.status);
  const Icon = visual.icon;
  return (
    <div className={`le-check-row ${visual.tone}`}>
      <span className="le-check-icon"><Icon size={15} /></span>
      <div className="le-check-copy">
        <strong>{label}</strong>
        <span>{item?.detail || 'Evidence unavailable.'}</span>
      </div>
      <span className={`le-state-badge ${visual.tone}`}>{visual.label}</span>
    </div>
  );
}

function SignalCard({ label, value, detail, state = 'neutral', icon: Icon = Activity }) {
  return (
    <article className={`le-signal-card ${state}`}>
      <div className="le-signal-icon"><Icon size={15} /></div>
      <div className="le-signal-copy">
        <span>{label}</span>
        <strong>{value}</strong>
        {detail && <small>{detail}</small>}
      </div>
    </article>
  );
}

function DataPoint({ label, value, detail, tone }) {
  return (
    <div className="le-data-point">
      <span>{label}</span>
      <strong className={tone ? `is-${tone}` : ''}>{value ?? '—'}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function ReviewTag({ tag, existing, applying, onApply }) {
  return (
    <article className="le-tag-card">
      <div className="le-tag-main">
        <div className="le-tag-meta">
          <span className="chip">{tag.tag_value}</span>
          <span>{String(tag.tag_type || '').toUpperCase()}</span>
          {tag.source === 'rule' ? (
            <span>RULE</span>
          ) : tag.confidence != null ? (
            <span>AI · {confidenceLabel(tag.confidence)}</span>
          ) : null}
        </div>
        {tag.reason && <p>{tag.reason}</p>}
      </div>
      <button
        type="button"
        className={existing ? 'btn btn-ghost btn-sm' : 'btn btn-secondary btn-sm'}
        disabled={existing || applying}
        onClick={onApply}
      >
        {existing ? <><CheckCircle2 size={13} /> Applied</> : applying ? 'Applying…' : 'Apply'}
      </button>
    </article>
  );
}

const CHECK_LABELS = {
  level_break: 'Directional level broke first',
  ema_alignment: 'Price aligned with 10m 8 EMA',
  ema_extension: 'Flag / EMA snugness',
  ema_beyond_broken_level: '8 EMA crossed the broken level',
  market_sign: 'SPY / QQQ confirm direction',
  chop_range: 'Outside PMH–PML chop',
};

const exitRelation = (value) => {
  if (value === 'after_confirmed_break') {
    return {
      value: 'After 8 EMA break',
      detail: 'Final exit followed a confirmed opposing 10-minute close.',
      state: 'pass',
    };
  }
  if (value === 'before_confirmed_break') {
    return {
      value: 'Before 8 EMA break',
      detail: 'The final exit occurred before the first confirmed opposing 10-minute close.',
      state: 'caution',
    };
  }
  if (value === 'no_confirmed_break_seen') {
    return {
      value: 'No break observed',
      detail: 'No confirmed opposing 10-minute 8 EMA close appeared in the review window.',
      state: 'neutral',
    };
  }
  return { value: 'Unavailable', detail: 'Exit/EMA relationship could not be established.', state: 'neutral' };
};

export default function LEReview({ trade, analysis, tags, onAnalysisChange, onTagsChange }) {
  const [review, setReview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [applying, setApplying] = useState('');
  const [batchApplying, setBatchApplying] = useState(false);

  const load = () => {
    setLoading(true);
    setError('');
    tradesApi.refreshLeCompliance(trade.trade_group)
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
    return (
      <div className="le-review-pro">
        <div className="le-review-loading" role="status">
          <Activity size={18} />
          <div>
            <strong>Building 10-minute 8 EMA review…</strong>
            <span>Reconstructing verified levels, entry structure, and post-entry EMA behavior.</span>
          </div>
        </div>
      </div>
    );
  }

  if (error && !review) {
    return (
      <div className="le-review-pro">
        <div className="notice neg" role="alert">{error}</div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
          <RefreshCw size={13} /> Retry
        </button>
      </div>
    );
  }

  if (!review?.available) {
    return (
      <div className="le-review-pro">
        <div className="le-review-unavailable">
          <ShieldCheck size={20} />
          <div>
            <strong>LE chart evidence is unavailable.</strong>
            <p>{review?.reason || 'The required market evidence could not be established.'}</p>
          </div>
        </div>
        {(review?.data_warnings || []).map((warning, index) => (
          <div key={index} className="text-muted" style={{ fontSize: 12.5 }}>{warning}</div>
        ))}
        <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
          <RefreshCw size={13} /> Refresh evidence
        </button>
      </div>
    );
  }

  const ev = review.evidence || {};
  const levels = ev.levels || {};
  const levelMeta = ev.level_meta || {};
  const breaks = ev.level_breaks_before_entry || {};
  const checks = ev.entry_checks || {};
  const management = ev.management_10m8ema || {};
  const emaLevel = ev.ema_vs_broken_level || {};
  const marketSign = ev.market_sign || {};
  const proven = review.auto_tags || [];
  const ai = review.ai;
  const strategy = ai?.strategy;
  const aiTags = ai?.suggested_tags || [];
  const executions = parseExecutions(trade);
  const compliance = review.compliance || {};
  const complianceScore = compliance.score || {};
  const complianceTone = compliance.classification === 'LE_COMPLIANT'
    ? 'pass'
    : compliance.classification === 'LE_VIOLATION'
      ? 'fail'
      : 'neutral';

  const allProvenApplied = proven.length > 0 && proven.every(
    tag => existing.has(`${tag.tag_type}::${tag.tag_value}`)
  );

  const breakNames = ['PDH', 'PDL', 'PMH', 'PML'].filter(name => breaks[name]);
  const breakSummary = breakNames.length ? breakNames.join(' + ') : 'None confirmed';
  const levelBreakState = breakNames.length ? 'pass' : 'fail';
  const alignmentState = ev.ema_alignment_valid === true ? 'pass' : ev.ema_alignment_valid === false ? 'fail' : 'neutral';
  const extensionState = ev.ema_extension_state === 'airgapped'
    ? 'fail'
    : ev.ema_extension_state === 'within_1pct'
      ? 'pass'
      : 'neutral';
  const emaCrossState = emaLevel.valid === true ? 'pass' : emaLevel.valid === false ? 'fail' : 'neutral';
  const marketSignState = marketSign.status === 'confirmed'
    ? 'pass'
    : ['mixed', 'failed'].includes(marketSign.status)
      ? 'fail'
      : 'neutral';
  const marketSignDetail = marketSign.status === 'confirmed'
    ? 'SPY and QQQ both agree on the 10m 8 EMA'
    : marketSign.status === 'mixed'
      ? 'SPY and QQQ are not aligned with each other'
      : marketSign.status === 'failed'
        ? 'SPY and QQQ both oppose the trade direction'
        : 'Consolidated market-sign evidence unavailable';
  const managementRead = exitRelation(management.exit_relation_to_ema_break);
  const structureTone = ev.entry_structure_status === 'aligned'
    ? 'pass'
    : ev.entry_structure_status === 'conflicted'
      ? 'fail'
      : ev.entry_structure_status === 'partial'
        ? 'caution'
        : 'neutral';
  const quality = ev.evidence_quality || {};
  const slopeDetail = ev.ema_slope_pct == null
    ? 'Slope unavailable'
    : `${ev.ema_slope_direction || 'unknown'} · ${Number(ev.ema_slope_pct) >= 0 ? '+' : ''}${Number(ev.ema_slope_pct).toFixed(2)}%`;

  return (
    <div className="le-review-pro">
      <header className="le-review-hero">
        <div className="le-review-hero-copy">
          <span className="le-review-kicker"><ShieldCheck size={13} /> LE system · objective review</span>
          <h2>10-Minute 8 EMA Structure Review</h2>
          <p>
            Review the trade against verified LE structure first: level break, Flag / EMA snugness,
            10-minute 8 EMA alignment, SPY / QQQ Market Sign, and final exit behavior.
          </p>
        </div>
        <div className="le-review-hero-actions">
          <div className={`le-quality-badge ${String(quality.level || '').toLowerCase()}`}>
            <span>Evidence</span>
            <strong>{quality.level || 'Unknown'}</strong>
            {quality.completeness_pct != null && <small>{quality.completeness_pct}% verified</small>}
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={load}>
            <RefreshCw size={13} /> Refresh
          </button>
        </div>
      </header>

      <div className="le-review-readonly-note">
        <ShieldCheck size={14} />
        <span>
          Market evidence and AI suggestions remain read-only. The deterministic LE compliance snapshot
          is cached for Brain so journal-wide audits can use the same verified result.
        </span>
        <span className="le-ruleset">{review.ruleset_version}</span>
      </div>

      {error && <div className="notice neg" role="alert">{error}</div>}

      <section className={`le-compliance-panel ${complianceTone}`} aria-label="LE compliance score">
        <div className="le-compliance-head">
          <div>
            <span className="le-section-kicker"><ShieldCheck size={12} /> PDF rule engine</span>
            <h3>13-Point LE Compliance</h3>
            <p>
              Pass / Fail / Unknown only. Unknown evidence never counts as a failure and never inflates the score.
            </p>
          </div>
          <span className={`le-compliance-classification ${complianceTone}`}>
            {compliance.classification_label || 'Incomplete evidence'}
          </span>
        </div>

        <div className="le-compliance-score-grid">
          <div>
            <span>Verified checks</span>
            <strong>
              {complianceScore.evaluated
                ? `${complianceScore.passed}/${complianceScore.evaluated}`
                : '—'}
            </strong>
            <small>
              {complianceScore.evaluated_pass_pct == null
                ? 'No evaluable checks'
                : `${complianceScore.evaluated_pass_pct}% passed`}
            </small>
          </div>
          <div>
            <span>Evidence coverage</span>
            <strong>{complianceScore.coverage_pct == null ? '—' : `${complianceScore.coverage_pct}%`}</strong>
            <small>{complianceScore.evaluated ?? 0} of {complianceScore.total ?? 13} evaluable</small>
          </div>
          <div>
            <span>Failed</span>
            <strong className={complianceScore.failed > 0 ? 'is-neg' : ''}>{complianceScore.failed ?? 0}</strong>
            <small>Verified rule violations</small>
          </div>
          <div>
            <span>Unknown</span>
            <strong>{complianceScore.unknown ?? 0}</strong>
            <small>Needs better evidence</small>
          </div>
        </div>

        <div className="le-compliance-check-list">
          {(compliance.checks || []).map(item => (
            <CheckRow key={item.id} label={item.label} item={item} />
          ))}
        </div>

        {(compliance.extra_findings || []).length > 0 && (
          <details className="le-compliance-extra">
            <summary>Additional LE non-negotiables</summary>
            <div className="le-compliance-check-list extra">
              {compliance.extra_findings.map(item => (
                <CheckRow key={item.id} label={item.label} item={item} />
              ))}
            </div>
          </details>
        )}
      </section>

      <section className="le-chart-panel" aria-label="Primary LE review chart">
        <div className="le-section-head">
          <div>
            <span className="le-section-kicker">Primary chart</span>
            <h3>{trade.ticker} · 10-minute execution structure</h3>
            <p>Timeframe is locked to the LE review timeframe. The blue line is the 8 EMA.</p>
          </div>
          <div className="le-chart-badges">
            <span>10m locked</span>
            <span>8 EMA</span>
            <span>PDH / PDL</span>
            <span>PMH / PML</span>
          </div>
        </div>
        <div className="le-chart-frame">
          <TradingChart
            ticker={trade.ticker}
            date={trade.date}
            tradeGroup={trade.trade_group}
            executions={executions}
            side={trade.side}
            defaultTimeframe="10Min"
            lockedTimeframe="10Min"
            showHeader={false}
            height={430}
          />
        </div>
      </section>

      <section className="le-signal-strip" aria-label="LE structure summary">
        <SignalCard
          label="Structure"
          value={cleanLabel(ev.entry_structure_status)}
          detail={`${ev.direction?.toUpperCase() || '—'} · ${cleanLabel(ev.session_window)}`}
          state={structureTone}
          icon={Target}
        />
        <SignalCard
          label="Level break"
          value={breakSummary}
          detail={ev.bars_since_level_break == null ? 'Timing unavailable' : `${ev.bars_since_level_break} completed 10m bar(s) after first break`}
          state={levelBreakState}
          icon={TrendingUp}
        />
        <SignalCard
          label="8 EMA alignment"
          value={ev.ema_alignment_valid == null ? 'Unverified' : ev.ema_alignment_valid ? 'Aligned' : 'Misaligned'}
          detail={`${cleanLabel(ev.price_vs_ema)} EMA · ${slopeDetail}`}
          state={alignmentState}
          icon={Activity}
        />
        <SignalCard
          label="EMA vs broken level"
          value={emaLevel.level ? `${emaLevel.valid ? 'Cleared' : 'Not cleared'} ${emaLevel.level}` : 'Unverified'}
          detail={emaLevel.level_price == null ? 'No verified broken level' : `8 EMA ${cleanLabel(emaLevel.position)} ${money(emaLevel.level_price)}`}
          state={emaCrossState}
          icon={Gauge}
        />
        <SignalCard
          label="Market Sign"
          value={marketSign.status ? cleanLabel(marketSign.status) : 'Unverified'}
          detail={marketSignDetail}
          state={marketSignState}
          icon={TrendingUp}
        />
        <SignalCard
          label="Final exit"
          value={managementRead.value}
          detail={managementRead.detail}
          state={managementRead.state}
          icon={Clock3}
        />
      </section>

      <div className="le-review-analysis-grid">
        <section className="le-review-panel">
          <div className="le-section-head compact">
            <div>
              <span className="le-section-kicker">Entry structure</span>
              <h3>Was the setup structurally ready?</h3>
            </div>
          </div>

          <div className="le-check-list">
            {Object.entries(CHECK_LABELS).map(([key, label]) => (
              <CheckRow key={key} label={label} item={checks[key]} />
            ))}
          </div>

          <div className="le-data-grid">
            <DataPoint label="Entry time" value={timeEt(ev.entry_time_et)} detail={cleanLabel(ev.session_window)} />
            <DataPoint label="Underlying at entry" value={money(ev.underlying_price_last_completed_1m)} detail="Last completed 1m close" />
            <DataPoint label="10m 8 EMA" value={money(ev.ema8_10m_last_completed)} detail={cleanLabel(ev.ema_integrity_status)} />
            <DataPoint label="EMA slope" value={cleanLabel(ev.ema_slope_direction)} detail={ev.ema_slope_pct == null ? '—' : `${Number(ev.ema_slope_pct) >= 0 ? '+' : ''}${pct(ev.ema_slope_pct)}`} />
            <DataPoint
              label="SPY Market Sign"
              value={marketSign.spy?.ema_aligned == null ? 'Unverified' : marketSign.spy.ema_aligned ? 'Aligned' : 'Opposed'}
              detail={marketSign.spy ? `${cleanLabel(marketSign.spy.position_vs_ema)} 10m 8 EMA · ${cleanLabel(marketSign.spy.integrity_status)}` : 'No verified SPY evidence'}
              tone={marketSign.spy?.ema_aligned === true ? 'pos' : marketSign.spy?.ema_aligned === false ? 'neg' : undefined}
            />
            <DataPoint
              label="QQQ Market Sign"
              value={marketSign.qqq?.ema_aligned == null ? 'Unverified' : marketSign.qqq.ema_aligned ? 'Aligned' : 'Opposed'}
              detail={marketSign.qqq ? `${cleanLabel(marketSign.qqq.position_vs_ema)} 10m 8 EMA · ${cleanLabel(marketSign.qqq.integrity_status)}` : 'No verified QQQ evidence'}
              tone={marketSign.qqq?.ema_aligned === true ? 'pos' : marketSign.qqq?.ema_aligned === false ? 'neg' : undefined}
            />
            <DataPoint
              label="Nearest broken level"
              value={ev.nearest_broken_level?.name || '—'}
              detail={ev.nearest_broken_level ? `${money(ev.nearest_broken_level.price)} · ${pct(ev.nearest_broken_level.distance_pct)} away` : 'No verified directional break'}
            />
            <DataPoint label="Price → EMA distance" value={pct(ev.ema_distance_pct)} detail={cleanLabel(ev.ema_extension_state)} tone={extensionState === 'fail' ? 'neg' : extensionState === 'pass' ? 'pos' : undefined} />
          </div>
        </section>

        <section className="le-review-panel">
          <div className="le-section-head compact">
            <div>
              <span className="le-section-kicker">Trade management</span>
              <h3>Did the final exit respect the 8 EMA?</h3>
            </div>
          </div>

          <div className={`le-management-callout ${managementRead.state}`}>
            <div>
              <span>Final exit vs first confirmed 10m 8 EMA break</span>
              <strong>{managementRead.value}</strong>
            </div>
            <p>{managementRead.detail}</p>
          </div>

          <div className="le-data-grid">
            <DataPoint label="Final exit time" value={timeEt(management.exit_time_et)} />
            <DataPoint label="Underlying near exit" value={money(management.exit_underlying_last_completed_1m)} detail="Last completed 1m close" />
            <DataPoint label="8 EMA near exit" value={money(management.exit_ema8_10m_last_completed)} detail={cleanLabel(management.exit_position_vs_ema)} />
            <DataPoint label="EMA retests held" value={management.ema_retests_held_before_exit ?? '—'} detail="Completed 10m touches that closed with trend" />
            <DataPoint label="First confirmed EMA break" value={timeEt(management.first_confirmed_ema_break_et)} detail={management.first_confirmed_ema_break_close == null ? 'No break observed' : `Close ${money(management.first_confirmed_ema_break_close)} · EMA ${money(management.first_confirmed_ema_break_value)}`} />
            <DataPoint label="30m after exit" value={pct(management.post_exit_favorable_move_pct_30m)} detail={management.post_exit_adverse_move_pct_30m == null ? 'Underlying continuation unavailable' : `Favorable underlying move · adverse ${pct(management.post_exit_adverse_move_pct_30m)}`} />
          </div>

          <p className="le-management-footnote">
            Post-exit movement is measured on the underlying only. It is review evidence, not an estimate of unrealized option P&amp;L.
          </p>
        </section>
      </div>

      <section className="le-review-panel">
        <div className="le-section-head">
          <div>
            <span className="le-section-kicker">Deterministic findings</span>
            <h3>Rule-derived tags</h3>
            <p>Only conditions proven by the market-data engine appear here.</p>
          </div>
          {proven.length > 0 && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={applyProven}
              disabled={batchApplying || allProvenApplied}
            >
              {allProvenApplied
                ? <><CheckCircle2 size={13} /> Applied</>
                : batchApplying ? 'Applying…' : 'Apply rule tags'}
            </button>
          )}
        </div>

        {proven.length ? (
          <div className="le-tag-grid">
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
          <div className="le-empty-state">No deterministic LE violation or setup tags were proven.</div>
        )}
      </section>

      <section className="le-review-panel">
        <div className="le-section-head">
          <div>
            <span className="le-section-kicker"><Sparkles size={12} /> Interpretation layer</span>
            <h3>Strategy classification</h3>
            <p>AI can interpret the verified evidence, but it cannot replace or contradict it.</p>
          </div>
          {ai?.provider && <span className="le-provider-badge">{ai.provider} · {ai.model}</span>}
        </div>

        {!ai?.available ? (
          <div className="le-empty-state">AI classification is unavailable. The deterministic chart review above remains valid.</div>
        ) : (
          <div className="le-ai-layout">
            <article className="le-strategy-card">
              <div>
                <span>Suggested strategy</span>
                <strong>{strategy?.value === 'NONE' ? 'No strategy suggested' : strategy?.value}</strong>
                {strategy?.confidence != null && <small>Confidence: {confidenceLabel(strategy.confidence)}</small>}
              </div>
              {strategy?.reason && <p>{strategy.reason}</p>}
              {strategy?.value && strategy.value !== 'NONE' && (
                <button
                  type="button"
                  className={analysis?.strategy === strategy.value ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm'}
                  disabled={analysis?.strategy === strategy.value || applying === 'strategy'}
                  onClick={applyStrategy}
                >
                  {analysis?.strategy === strategy.value
                    ? <><CheckCircle2 size={13} /> Applied</>
                    : applying === 'strategy' ? 'Applying…' : 'Apply strategy'}
                </button>
              )}
            </article>

            <div className="le-tag-grid">
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

            {aiTags.length === 0 && <div className="le-empty-state">No additional AI tags were suggested.</div>}

            {(ai.insufficient_evidence || []).length > 0 && (
              <div className="le-insufficient">
                <strong>Evidence still missing</strong>
                {ai.insufficient_evidence.map((item, index) => <span key={index}>• {item}</span>)}
              </div>
            )}
          </div>
        )}
      </section>

      <details className="le-evidence-details">
        <summary>
          <span><ShieldCheck size={14} /> Evidence provenance &amp; level verification</span>
          <small>{feedLabel(ev.market_data_feed?.underlying)}</small>
        </summary>

        <div className="le-evidence-body">
          <div className="le-data-grid levels">
            <DataPoint label="PDH" value={money(levels.PDH)} detail={levelStatusLabel(levelMeta.PDH)} />
            <DataPoint label="PDL" value={money(levels.PDL)} detail={levelStatusLabel(levelMeta.PDL)} />
            <DataPoint label="PMH" value={money(levels.PMH)} detail={levelStatusLabel(levelMeta.PMH)} />
            <DataPoint label="PML" value={money(levels.PML)} detail={levelStatusLabel(levelMeta.PML)} />
            <DataPoint label="Broker time zone" value={ev.execution_time_zone || '—'} />
            <DataPoint label="Market calendar" value={ev.market_calendar_verified ? 'Verified' : 'Unverified'} />
            <DataPoint label="SPY feed" value={feedLabel(ev.market_data_feed?.spy)} detail={cleanLabel(marketSign.spy?.integrity_status)} />
            <DataPoint label="QQQ feed" value={feedLabel(ev.market_data_feed?.qqq)} detail={cleanLabel(marketSign.qqq?.integrity_status)} />
          </div>

          {quality.reason && <p className="le-quality-reason">{quality.reason}</p>}

          {(review.data_warnings || []).length > 0 && (
            <div className="le-warning-list">
              <strong>Evidence limitations</strong>
              {review.data_warnings.map((warning, index) => <span key={index}>• {warning}</span>)}
            </div>
          )}
        </div>
      </details>
    </div>
  );
}
