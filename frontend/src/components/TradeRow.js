import { useState } from 'react';
import { tradesApi } from '../api';
import { ChevronRight } from 'lucide-react';

const signed$ = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  return (n > 0 ? '+' : n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
};

function ConfidenceDot({ level }) {
  return (
    <span
      className={`confidence-dot confidence-${level || 'unmatched'}`}
      title={`Match confidence: ${level || 'unmatched'}`}
    />
  );
}

function PLPercent({ trade }) {
  const value = trade.pl_pct;
  if (value == null || !Number.isFinite(Number(value))) {
    return <span className="text-faint">P/L % unavailable</span>;
  }
  const n = Number(value);
  const cls = n > 0 ? 'pos' : n < 0 ? 'neg' : 'text-muted';
  const title = trade.instrument_type === 'OPTION'
    ? 'Net P/L ÷ entry option premium (100× contract multiplier).'
    : trade.instrument_type === 'FUTURE'
      ? 'Net P/L ÷ entry futures notional using the contract multiplier.'
      : 'Net P/L ÷ entry stock notional.';
  return (
    <span className={`num ${cls}`} title={title}>
      {n > 0 ? '+' : ''}{n.toFixed(1)}%
    </span>
  );
}

function SetupBadge({ setup, strategy }) {
  if (!setup || setup === 'NONE') {
    return strategy
      ? (
        <span className="trade-setup-badge" title={strategy}>
          <span className="trade-strategy-text">{strategy}</span>
        </span>
      )
      : <span className="text-faint">Not tagged</span>;
  }

  const title = [
    `${setup} (playbook setup)`,
    strategy ? `Strategy: ${strategy}` : null,
  ].filter(Boolean).join('\n');

  return (
    <span className="trade-setup-badge" title={title}>
      <span>{setup}</span>
    </span>
  );
}

function ExcursionCell({ trade }) {
  if (trade.excursion_stale) return <span className="text-faint">Recalculating</span>;
  const mfe = Number(trade.mfe_pct);
  const mae = Number(trade.mae_pct);
  const hasMfe = Number.isFinite(mfe);
  const hasMae = Number.isFinite(mae);
  if (!hasMfe && !hasMae) return <span className="text-faint">No path data</span>;

  return (
    <div className="trade-excursion-cell" title="Maximum favorable / adverse excursion while the trade was open">
      <span className="pos">{hasMfe ? `+${Math.abs(mfe).toFixed(1)}` : '—'}</span>
      <span className="trade-excursion-sep">/</span>
      <span className="neg">{hasMae ? `-${Math.abs(mae).toFixed(1)}` : '—'}</span>
      <small>% best / worst</small>
    </div>
  );
}

function ExitQuality({ trade }) {
  if (Number(trade.net_pnl || 0) <= 0) return <span className="text-faint">—</span>;
  const value = Number(trade.exit_efficiency);
  if (!Number.isFinite(value)) return <span className="text-faint">—</span>;
  const cls = value >= 60 ? 'pos' : value >= 35 ? 'caution' : 'neg';
  return (
    <div className="trade-exit-quality" title="How much of the covered favorable move was retained at exit">
      <span className={`num ${cls}`}>{value.toFixed(1)}%</span>
      <small>Exit capture</small>
    </div>
  );
}

export default function TradeRow({ trade, openTime, onOpenDetail, customSetups = [], onCustomSetupsChanged }) {
  const pnl = trade.net_pnl ?? 0;
  const pnlTone = pnl > 0 ? 'pos' : pnl < 0 ? 'neg' : '';
  const side = (trade.side || '').toUpperCase();
  const realizedR = trade.realized_r != null ? Number(trade.realized_r)
    : trade.r_multiple != null ? Number(trade.r_multiple)
      : null;
  const stopDistance = Number(trade.stop_loss);
  const targetDistance = Number(trade.target_price);
  const plannedRR = Number.isFinite(stopDistance) && stopDistance > 0
    && Number.isFinite(targetDistance) && targetDistance > 0
      ? targetDistance / stopDistance
      : null;
  const riskPerTrade = Number(trade.risk_per_trade);
  const entryExecs = Array.isArray(trade.executions)
    ? trade.executions
    : (() => { try { return JSON.parse(trade.executions || '[]'); } catch { return []; } })();
  const optionEntries = entryExecs.filter(e => side === 'LONG' ? e.action === 'BOT' : e.action === 'SOLD');
  const optionEntryQty = optionEntries.reduce((sum, fill) => sum + Number(fill.qty || 0), 0);
  const optionEntryPremium = optionEntries.reduce((sum, fill) => sum + Number(fill.qty || 0) * Number(fill.price || 0), 0);
  const maxPremiumRisk = trade.instrument_type === 'OPTION' && side === 'LONG' && optionEntryQty > 0
    ? optionEntryPremium * 100
    : null;
  const usesMaxPremiumBaseline = Number.isFinite(riskPerTrade) && riskPerTrade > 0
    && Number.isFinite(maxPremiumRisk)
    && Math.abs(riskPerTrade - maxPremiumRisk) < 0.01
    && trade.r_multiple == null;
  const showRealizedR = realizedR != null && Number.isFinite(realizedR);

  const handleOpen = () => { if (onOpenDetail) onOpenDetail(trade); };
  const ADD_NEW = '__add_new__';

  function SetupEditor({ trade: rowTrade }) {
    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [local, setLocal] = useState({
      setup: rowTrade.setup,
      source: rowTrade.setup_source,
    });

    // Options/futures still show their saved strategy. Only the stock playbook
    // setup tag is edited inline.
    if (rowTrade.instrument_type && rowTrade.instrument_type !== 'STOCK') {
      return (
        <SetupBadge
          setup={null}
          strategy={rowTrade.strategy}
        />
      );
    }

    const save = async (value) => {
      if (value === ADD_NEW) {
        const name = window.prompt(
          'Name your setup, e.g. "Bookmap absorption read".\n\n'
          + 'It gets added to the dropdown for every trade.'
        );
        if (!name || !name.trim()) { setEditing(false); return; }
        setSaving(true);
        try {
          await tradesApi.createCustomSetup({ name: name.trim() });
          if (onCustomSetupsChanged) await onCustomSetupsChanged();
          value = name.trim();
        } catch (e) {
          alert('Could not add setup: ' + (e?.response?.data?.detail || e.message));
          setSaving(false);
          setEditing(false);
          return;
        }
      }

      setSaving(true);
      try {
        const { data } = await tradesApi.setSetup(rowTrade.id, value === '' ? null : value);
        setLocal({
          setup: data.setup,
          source: data.setup_source,
        });
        setEditing(false);
      } catch (e) {
        alert('Could not save setup: ' + (e?.response?.data?.detail || e.message));
      } finally {
        setSaving(false);
      }
    };

    if (editing) {
      return (
        <select
          autoFocus
          disabled={saving}
          aria-label={`Setup for ${rowTrade.ticker} on ${rowTrade.date}`}
          defaultValue={local.setup === 'NONE' ? 'NONE' : (local.setup || '')}
          onChange={e => save(e.target.value)}
          onBlur={() => setEditing(false)}
          onKeyDown={e => { if (e.key === 'Escape') setEditing(false); }}
          className="trade-setup-select"
        >
          <option value="">(clear tag)</option>
          {customSetups.length > 0 && (
            <optgroup label="Playbook">
              {customSetups.map(cs => (
                <option key={cs.id} value={cs.name}>
                  {cs.name}{cs.side ? ` (${cs.side.toLowerCase()})` : ''}
                </option>
              ))}
            </optgroup>
          )}
          <optgroup label="Other">
            <option value="NONE">No setup</option>
            <option value={ADD_NEW}>+ Add a new setup…</option>
          </optgroup>
        </select>
      );
    }

    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        title="Click to set the stock playbook setup"
        aria-label={`Setup: ${local.setup || rowTrade.strategy || 'none'}. Change setup`}
        className="trade-setup-button"
      >
        <SetupBadge
          setup={local.setup}
          strategy={rowTrade.strategy}
        />
        {local.source === 'manual' && <span className="text-faint trade-manual-mark" title="Manually tagged">✎</span>}
      </button>
    );
  }

  return (
    <tr
      className="row-link trade-view-row"
      onClick={handleOpen}
      tabIndex={0}
      onKeyDown={e => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          handleOpen();
        }
      }}
    >
      <td className="trade-date-cell">
        <div className="num">{trade.date}</div>
        {openTime && <div className="num text-muted">{openTime.slice(0, 5)}</div>}
      </td>

      <td className="trade-symbol-cell">
        <div className="trade-symbol">{trade.ticker}</div>
        <div className="trade-symbol-meta">
          <span className={`badge badge-${trade.instrument_type?.toLowerCase()}`}>{trade.instrument_type}</span>
          <span>{side === 'LONG' ? 'Long' : side === 'SHORT' ? 'Short' : trade.side}</span>
        </div>
      </td>

      <td className="trade-strategy-cell" onClick={e => e.stopPropagation()} onKeyDown={e => e.stopPropagation()}>
        <SetupEditor trade={trade} />
      </td>

      <td className="trade-r-cell">
        <div
          className="trade-r-status"
          title={plannedRR != null
            ? `Planned R:R from ${stopDistance.toFixed(2)} stop distance and ${targetDistance.toFixed(2)} target distance.`
            : 'Planned R:R has not been set yet.'}
        >
          <span className={`num ${plannedRR != null ? 'trade-r-planned' : 'trade-r-missing'}`}>
            {plannedRR != null ? `1:${plannedRR.toFixed(2)}` : 'Not Set'}
          </span>
          {showRealizedR && (
            <small
              className={realizedR > 0 ? 'pos' : realizedR < 0 ? 'neg' : 'text-muted'}
              title={usesMaxPremiumBaseline
                ? 'Realized R uses the saved max-premium risk baseline.'
                : 'Realized R uses the saved planned-risk amount.'}
            >
              {realizedR > 0 ? '+' : ''}{realizedR.toFixed(2)}R realized
            </small>
          )}
        </div>
      </td>


      <td className="trade-excursion-col"><ExcursionCell trade={trade} /></td>
      <td className="trade-exit-col"><ExitQuality trade={trade} /></td>

      <td className="trade-result-cell">
        <div className={`num ${pnlTone} trade-result-pnl`}>{signed$(pnl)}</div>
        <PLPercent trade={trade} />
      </td>

      <td className="trade-open-cell">
        <span className="text-muted">
          {trade.match_confidence && <ConfidenceDot level={trade.match_confidence} />}
          <ChevronRight size={16} aria-hidden="true" />
        </span>
      </td>
    </tr>
  );
}
