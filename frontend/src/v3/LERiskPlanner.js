import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Calculator, CheckCircle2, RotateCcw, ShieldCheck } from 'lucide-react';
import { leRiskPlanApi } from '../api';

const money = (value) => {
  const n = Number(value || 0);
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
};

const money2 = (value) => {
  const n = Number(value || 0);
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const positive = (value) => {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
};

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

function nextWeekdayISO() {
  const date = new Date();
  while (date.getDay() === 0 || date.getDay() === 6) date.setDate(date.getDate() + 1);
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

const emptyPlan = () => ({
  capital: '',
  exposure_pct: 30,
  direction: 'call',
  option_price: '',
  delta: '',
  underlying_entry: '',
  stop_price: '',
  target_price: '',
  rule_committed: false,
  trade1: '',
  trade2: '',
  third_trade_a_plus: false,
  trade3_done: false,
});

const numericKeys = ['capital', 'exposure_pct', 'option_price', 'delta', 'underlying_entry', 'stop_price', 'target_price'];

function apiPayload(plan, date, accountId) {
  const payload = {
    account_id: accountId == null ? null : accountId,
    date,
    ...plan,
  };
  numericKeys.forEach((key) => {
    if (key === 'exposure_pct') {
      payload[key] = clamp(Number(plan[key] || 30), 20, 30);
    } else {
      payload[key] = plan[key] === '' || plan[key] == null ? null : Number(plan[key]);
    }
  });
  return payload;
}

export function calculateLESize(plan = {}) {
  const capital = positive(plan.capital);
  const exposurePct = clamp(Number(plan.exposure_pct || 30), 20, 30);
  if (!capital) {
    return {
      hasCapital: false,
      exposurePct,
      exposureLow: 0,
      exposureHigh: 0,
      maxLoss: 0,
      minTarget: 0,
      canSize: false,
    };
  }

  const exposureLow = capital * 0.20;
  const exposureHigh = capital * 0.30;
  const maxLoss = capital * 0.05;
  const minTarget = capital * 0.10;
  const exposureBudget = capital * (exposurePct / 100);

  const optionPrice = positive(plan.option_price);
  const rawDelta = Number(plan.delta);
  const delta = Number.isFinite(rawDelta) && rawDelta !== 0 && Math.abs(rawDelta) <= 1 ? Math.abs(rawDelta) : null;
  const entry = positive(plan.underlying_entry);
  const stop = positive(plan.stop_price);
  const direction = plan.direction === 'put' ? 'put' : 'call';

  if (!optionPrice || !delta || !entry || !stop) {
    return {
      hasCapital: true,
      exposurePct,
      exposureLow,
      exposureHigh,
      exposureBudget,
      maxLoss,
      minTarget,
      canSize: false,
    };
  }

  const premiumPerContract = optionPrice * 100;
  const spotRisk = Math.abs(entry - stop);
  const estimatedLossPerContract = spotRisk * delta * 100;
  const contractsByExposure = Math.floor(exposureBudget / premiumPerContract);
  const contractsByRisk = estimatedLossPerContract > 0 ? Math.floor(maxLoss / estimatedLossPerContract) : 0;
  const contracts = Math.max(0, Math.min(contractsByExposure, contractsByRisk));
  const premiumUsed = contracts * premiumPerContract;
  const actualRisk = contracts * estimatedLossPerContract;
  const stopSideValid = direction === 'call' ? stop < entry : stop > entry;

  const target = positive(plan.target_price);
  let potentialReward = null;
  let rr = null;
  let meetsTarget = null;
  let meetsRR = null;
  let targetSideValid = null;
  let requiredSpotMove = null;
  let requiredTargetPrice = null;

  if (contracts > 0) {
    if (target) {
      const targetMove = Math.abs(target - entry);
      const rewardPerContract = targetMove * delta * 100;
      potentialReward = rewardPerContract * contracts;
      rr = actualRisk > 0 ? potentialReward / actualRisk : null;
      meetsTarget = potentialReward >= minTarget;
      meetsRR = rr != null && rr >= 2;
      targetSideValid = direction === 'call' ? target > entry : target < entry;
    } else {
      requiredSpotMove = minTarget / (delta * 100 * contracts);
      requiredTargetPrice = direction === 'call'
        ? entry + requiredSpotMove
        : Math.max(0, entry - requiredSpotMove);
    }
  }

  const constraint = contractsByRisk < contractsByExposure ? 'Risk cap' : 'Premium exposure';

  return {
    hasCapital: true,
    exposurePct,
    exposureLow,
    exposureHigh,
    exposureBudget,
    maxLoss,
    minTarget,
    canSize: true,
    direction,
    optionPrice,
    delta,
    entry,
    stop,
    stopSideValid,
    spotRisk,
    premiumPerContract,
    estimatedLossPerContract,
    contractsByExposure,
    contractsByRisk,
    contracts,
    premiumUsed,
    actualRisk,
    actualRiskPct: capital ? actualRisk / capital * 100 : 0,
    premiumPct: capital ? premiumUsed / capital * 100 : 0,
    constraint,
    target,
    potentialReward,
    rr,
    meetsTarget,
    meetsRR,
    targetSideValid,
    requiredSpotMove,
    requiredTargetPrice,
  };
}

export function getThreeTradeGuard(plan = {}) {
  const t1 = plan.trade1 || '';
  const t2 = plan.trade2 || '';
  const aPlus = Boolean(plan.third_trade_a_plus);
  const trade3Done = Boolean(plan.trade3_done);

  if (!plan.rule_committed) {
    return {
      tone: 'neutral',
      label: 'COMMIT TO THE RULE',
      message: 'Check the rule before the session. Maximum three trades; the first two outcomes decide whether trade three is allowed.',
      thirdEligible: false,
    };
  }
  if (!t1) {
    return { tone: 'good', label: 'READY · TRADE 1', message: 'Start with an A+ setup. Risk is defined before entry.', thirdEligible: false };
  }
  if (!t2) {
    return { tone: 'good', label: 'TRADE 2 ALLOWED', message: `Trade 1 was ${t1}. Stay selective; the second outcome decides the rest of the day.`, thirdEligible: false };
  }

  if (t1 === 'red' && t2 === 'red') {
    return { tone: 'stop', label: 'STOP · TWO REDS', message: 'Done for the day. A third trade would violate the LE Three Trade Rule.', thirdEligible: false };
  }
  if (t1 === 'red' && t2 === 'green') {
    return { tone: 'stop', label: 'STOP · RECOVERED', message: 'You recovered the first loss. Walk away rather than giving it back.', thirdEligible: false };
  }

  const thirdPath = t1 === 'green' && t2 === 'green' ? 'green-green' : 'green-red';
  if (trade3Done) {
    return { tone: 'stop', label: 'STOP · THREE TRADES', message: 'Three trades reached. The session is closed.', thirdEligible: false };
  }
  if (!aPlus) {
    return {
      tone: thirdPath === 'green-red' ? 'caution' : 'neutral',
      label: thirdPath === 'green-red' ? 'CAUTION · A+ ONLY' : 'A+ SETUP ONLY',
      message: thirdPath === 'green-red'
        ? 'You gave back a win. Stop unless a true A+ setup appears.'
        : 'You are reading the market well, but trade three is allowed only for an A+ setup.',
      thirdEligible: false,
    };
  }
  return {
    tone: thirdPath === 'green-red' ? 'caution' : 'good',
    label: thirdPath === 'green-red' ? 'FINAL TRADE · CAUTION' : 'FINAL TRADE ALLOWED',
    message: 'A+ setup confirmed. Trade three is the final trade of the session.',
    thirdEligible: true,
  };
}

function ResultToggle({ label, value, onChange, disabled = false }) {
  return (
    <div className="le-risk-result-row">
      <span>{label}</span>
      <div className="le-risk-seg" role="group" aria-label={`${label} result`}>
        {['green', 'red'].map((outcome) => (
          <button
            key={outcome}
            type="button"
            disabled={disabled}
            className={value === outcome ? `active ${outcome}` : ''}
            aria-pressed={value === outcome}
            onClick={() => onChange(value === outcome ? '' : outcome)}
          >
            {outcome === 'green' ? 'Green' : 'Red'}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function LERiskPlanner({ accountId }) {
  const [date, setDate] = useState(nextWeekdayISO);
  const [plan, setPlan] = useState(emptyPlan);
  const [hydrated, setHydrated] = useState(false);
  const [saveState, setSaveState] = useState('saved');
  const lastSaved = useRef('');
  const loadSeq = useRef(0);

  useEffect(() => {
    const seq = ++loadSeq.current;
    setHydrated(false);
    setSaveState('loading');
    const params = { date };
    if (accountId != null) params.account_id = accountId;

    leRiskPlanApi.get(params)
      .then((response) => {
        if (seq !== loadSeq.current) return;
        const data = response.data || {};
        const next = { ...emptyPlan(), ...data };
        delete next.date;
        setPlan(next);
        lastSaved.current = JSON.stringify(apiPayload(next, date, accountId));
        setHydrated(true);
        setSaveState('saved');
      })
      .catch(() => {
        if (seq !== loadSeq.current) return;
        const next = emptyPlan();
        setPlan(next);
        lastSaved.current = JSON.stringify(apiPayload(next, date, accountId));
        setHydrated(true);
        setSaveState('error');
      });
  }, [accountId, date]);

  useEffect(() => {
    if (!hydrated) return undefined;
    const payload = apiPayload(plan, date, accountId);
    const serialized = JSON.stringify(payload);
    if (serialized === lastSaved.current) return undefined;

    setSaveState('saving');
    const timer = setTimeout(() => {
      leRiskPlanApi.put(payload)
        .then(() => {
          lastSaved.current = serialized;
          setSaveState('saved');
        })
        .catch(() => setSaveState('error'));
    }, 450);

    return () => clearTimeout(timer);
  }, [accountId, date, hydrated, plan]);

  const calc = useMemo(() => calculateLESize(plan), [plan]);
  const guard = useMemo(() => getThreeTradeGuard(plan), [plan]);

  const update = (key, value) => setPlan((current) => ({ ...current, [key]: value }));
  const setOutcome = (key, value) => setPlan((current) => ({
    ...current,
    [key]: value,
    third_trade_a_plus: false,
    trade3_done: false,
  }));

  const resetDay = () => {
    const params = { date };
    if (accountId != null) params.account_id = accountId;
    leRiskPlanApi.remove(params)
      .then((response) => {
        const next = { ...emptyPlan(), ...(response.data || {}) };
        delete next.date;
        setPlan(next);
        lastSaved.current = JSON.stringify(apiPayload(next, date, accountId));
        setSaveState('saved');
      })
      .catch(() => setSaveState('error'));
  };

  return (
    <section className="le-risk-planner" aria-labelledby="le-risk-title">
      <div className="le-risk-head">
        <div className="le-risk-heading">
          <span className="le-risk-icon"><Calculator size={16} /></span>
          <div>
            <h3 id="le-risk-title">LE Daily Risk Plan</h3>
            <p>Risk before reward · plan the session before the first click.</p>
          </div>
        </div>
        <span className={`le-risk-save ${saveState}`} aria-live="polite">
          {saveState === 'saving' ? 'Saving…' : saveState === 'error' ? 'Save issue' : saveState === 'loading' ? 'Loading…' : 'Saved'}
        </span>
      </div>

      <div className="le-risk-date-row">
        <label>
          <span>Session</span>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <button type="button" className="le-risk-reset" onClick={resetDay} title="Reset this day">
          <RotateCcw size={13} /> Reset
        </button>
      </div>

      <label className="le-risk-capital">
        <span>Current trading capital</span>
        <div className="le-risk-money-input">
          <b>$</b>
          <input
            inputMode="decimal"
            type="number"
            min="0"
            step="100"
            placeholder="10,000"
            value={plan.capital ?? ''}
            onChange={(e) => update('capital', e.target.value)}
          />
        </div>
      </label>

      <div className="le-risk-rule-grid" aria-label="LE capital limits">
        <div>
          <span>Premium exposure</span>
          <strong>{calc.hasCapital ? `${money(calc.exposureLow)}–${money(calc.exposureHigh)}` : '20–30%'}</strong>
          <small>20–30% of capital</small>
        </div>
        <div>
          <span>Max actual loss</span>
          <strong className="neg">{calc.hasCapital ? money(calc.maxLoss) : '5%'}</strong>
          <small>hard loss cap</small>
        </div>
        <div>
          <span>Min first target</span>
          <strong className="pos">{calc.hasCapital ? money(calc.minTarget) : '10%'}</strong>
          <small>≥ 10% of capital</small>
        </div>
        <div>
          <span>Minimum R:R</span>
          <strong>2:1</strong>
          <small>reward ≥ 2× risk</small>
        </div>
      </div>

      <details className="le-risk-sizing">
        <summary>
          <span><ShieldCheck size={14} /> Contract sizing</span>
          <small>{calc.canSize ? (calc.contracts > 0 ? `${calc.contracts} contract${calc.contracts === 1 ? '' : 's'}` : 'No contract fits') : 'Optional inputs'}</small>
        </summary>
        <div className="le-risk-sizing-body">
          <div className="le-risk-fields">
            <label>
              <span>Direction</span>
              <select value={plan.direction} onChange={(e) => update('direction', e.target.value)}>
                <option value="call">Call / long</option>
                <option value="put">Put / short</option>
              </select>
            </label>
            <label>
              <span>Exposure</span>
              <select value={plan.exposure_pct} onChange={(e) => update('exposure_pct', Number(e.target.value))}>
                <option value={20}>20%</option>
                <option value={25}>25%</option>
                <option value={30}>30%</option>
              </select>
            </label>
            <label>
              <span>Option price</span>
              <input type="number" min="0" step="0.01" placeholder="4.20" value={plan.option_price ?? ''} onChange={(e) => update('option_price', e.target.value)} />
            </label>
            <label>
              <span>Delta</span>
              <input type="number" min="-1" max="1" step="0.01" placeholder="0.62" value={plan.delta ?? ''} onChange={(e) => update('delta', e.target.value)} />
            </label>
            <label>
              <span>Underlying entry</span>
              <input type="number" min="0" step="0.01" placeholder="150.00" value={plan.underlying_entry ?? ''} onChange={(e) => update('underlying_entry', e.target.value)} />
            </label>
            <label>
              <span>Technical stop</span>
              <input type="number" min="0" step="0.01" placeholder="Entry-candle low/high" value={plan.stop_price ?? ''} onChange={(e) => update('stop_price', e.target.value)} />
            </label>
            <label className="wide">
              <span>Underlying target <em>optional</em></span>
              <input type="number" min="0" step="0.01" placeholder="Leave blank to calculate minimum target" value={plan.target_price ?? ''} onChange={(e) => update('target_price', e.target.value)} />
            </label>
          </div>

          {calc.canSize && (
            <>
              <div className="le-risk-contract-result">
                <div className="le-risk-contract-primary">
                  <span>LE size</span>
                  <strong>{calc.contracts}</strong>
                  <small>{calc.contracts === 0 ? 'No contract fits both limits' : `${calc.constraint} is binding`}</small>
                </div>
                <dl>
                  <div><dt>Premium used</dt><dd>{money2(calc.premiumUsed)} <small>({calc.premiumPct.toFixed(1)}%)</small></dd></div>
                  <div><dt>Est. loss at stop</dt><dd className="neg">{money2(calc.actualRisk)} <small>({calc.actualRiskPct.toFixed(2)}%)</small></dd></div>
                  <div><dt>Exposure limit</dt><dd>{calc.contractsByExposure} contracts</dd></div>
                  <div><dt>Risk limit</dt><dd>{calc.contractsByRisk} contracts</dd></div>
                </dl>
              </div>

              {!calc.stopSideValid && (
                <div className="le-risk-inline-warning"><AlertTriangle size={13} /> Stop is on the wrong side of entry for this direction.</div>
              )}

              {calc.contracts > 0 && (calc.target ? (
                <div className={`le-risk-target-check ${calc.meetsTarget && calc.meetsRR && calc.targetSideValid ? 'good' : 'caution'}`}>
                  <CheckCircle2 size={14} />
                  <span>
                    Target estimates {money2(calc.potentialReward)} reward · {calc.rr == null ? '—' : calc.rr.toFixed(2) + ':1'} R:R
                    {!calc.targetSideValid ? ' · target is on the wrong side' : !calc.meetsTarget ? ' · below 10% target' : !calc.meetsRR ? ' · below 2:1' : ' · LE checks pass'}
                  </span>
                </div>
              ) : (
                <div className="le-risk-target-check neutral">
                  <ShieldCheck size={14} />
                  <span>Minimum 10% target requires about a {money2(calc.requiredSpotMove)} underlying move to ~{money2(calc.requiredTargetPrice)}.</span>
                </div>
              ))}
            </>
          )}
        </div>
      </details>

      <div className="le-three-rule">
        <div className="le-three-head">
          <div>
            <span className="le-three-kicker">SESSION GUARD</span>
            <h4>Stop at Three or Broke You'll Be</h4>
          </div>
          <span className="le-three-max">MAX 3</span>
        </div>

        <label className="le-rule-commit">
          <input type="checkbox" checked={Boolean(plan.rule_committed)} onChange={(e) => update('rule_committed', e.target.checked)} />
          <span>I will follow the Three Trade Rule today.</span>
        </label>

        <ResultToggle label="Trade 1" value={plan.trade1} onChange={(value) => setOutcome('trade1', value)} disabled={!plan.rule_committed} />
        <ResultToggle label="Trade 2" value={plan.trade2} onChange={(value) => setOutcome('trade2', value)} disabled={!plan.rule_committed || !plan.trade1} />

        {plan.trade1 && plan.trade2 && !['red-red', 'red-green'].includes(`${plan.trade1}-${plan.trade2}`) && (
          <label className="le-rule-commit compact">
            <input
              type="checkbox"
              checked={Boolean(plan.third_trade_a_plus)}
              onChange={(e) => setPlan((current) => ({ ...current, third_trade_a_plus: e.target.checked, trade3_done: false }))}
            />
            <span>Trade 3 is a true A+ setup.</span>
          </label>
        )}

        {guard.thirdEligible && (
          <label className="le-rule-commit compact">
            <input type="checkbox" checked={Boolean(plan.trade3_done)} onChange={(e) => update('trade3_done', e.target.checked)} />
            <span>Trade 3 completed — session closed.</span>
          </label>
        )}

        <div className={`le-three-status ${guard.tone}`} aria-live="polite">
          <strong>{guard.label}</strong>
          <span>{guard.message}</span>
        </div>
      </div>
    </section>
  );
}
