import { useEffect, useMemo, useRef, useState } from 'react';
import { Calculator, RotateCcw } from 'lucide-react';
import { leRiskPlanApi } from '../api';

const money = (value) => {
  const n = Number(value || 0);
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
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
  if (!capital) {
    return {
      hasCapital: false,
      exposureLow: 0,
      exposureHigh: 0,
      maxLoss: 0,
      minTarget: 0,
    };
  }

  return {
    hasCapital: true,
    exposureLow: capital * 0.20,
    exposureHigh: capital * 0.30,
    maxLoss: capital * 0.05,
    minTarget: capital * 0.10,
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
            <p>Set capital. Know your limits. Trade the plan.</p>
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
        <span>Trading capital</span>
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
          <span>Premium cap</span>
          <strong>{calc.hasCapital ? `${money(calc.exposureLow)}–${money(calc.exposureHigh)}` : '20–30%'}</strong>
          <small>20–30% of capital</small>
        </div>
        <div>
          <span>Max loss</span>
          <strong className="neg">{calc.hasCapital ? money(calc.maxLoss) : '5%'}</strong>
          <small>5% hard cap</small>
        </div>
        <div>
          <span>Min target</span>
          <strong className="pos">{calc.hasCapital ? money(calc.minTarget) : '10%'}</strong>
          <small>10% minimum</small>
        </div>
        <div>
          <span>Min R:R</span>
          <strong>2:1</strong>
          <small>reward ≥ 2× risk</small>
        </div>
      </div>


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
