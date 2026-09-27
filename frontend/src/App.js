import { useState, useEffect, useCallback, useRef } from 'react';
import './index.css';
import { accountsApi, kpisApi } from './api';
import AppHeader from './components/AppHeader';
import Dashboard from './components/Dashboard';
import Trades from './components/Trades';
import Calendar from './components/Calendar';
import Import from './components/Import';
import Diary from './components/Diary';
import AddTradeModal from './components/AddTradeModal';
import TradeDetail from './components/TradeDetail';
import Brain from './components/Brain';
import DailySummary from './components/DailySummary';
import Reports from './components/Reports';
import Help from './components/Help';
import Settings from './components/Settings';
import Login from './components/Login';
import PasswordReset from './components/PasswordReset';
import {
  AUTH_CHANGED_EVENT,
  authConfigured,
  authRequired,
  clearSession,
  consumePasswordRecoveryCallback,
  getSession,
  requestPasswordReset,
  restoreSession,
  signInWithPassword,
  startSessionAutoRefresh,
  updatePassword,
} from './auth';

const JOURNAL_NAV_STATE_KEY = 'trading-journal:navigation-state-v1';
const VALID_PAGES = new Set(['dashboard', 'trades', 'trade-detail', 'calendar', 'import', 'diary', 'day-review', 'reports', 'help', 'settings']);

function readJournalNavState() {
  try {
    const raw = sessionStorage.getItem(JOURNAL_NAV_STATE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!VALID_PAGES.has(parsed?.page)) parsed.page = 'dashboard';
    if (parsed.page === 'trade-detail' && !parsed.selectedTrade) parsed.page = 'trades';
    return parsed;
  } catch {
    return {};
  }
}

function writeJournalNavState(state) {
  try {
    sessionStorage.setItem(JOURNAL_NAV_STATE_KEY, JSON.stringify(state));
  } catch {
    // Navigation persistence is a convenience; storage failure must not block the journal.
  }
}

function JournalApp({ onSignOut }) {
  const initialNavState = useRef(readJournalNavState()).current;
  const [page, setPage] = useState(initialNavState.page || 'dashboard');
  const [accounts, setAccounts] = useState([]);
  const [selectedAccountId, setSelectedAccountId] = useState(initialNavState.selectedAccountId ?? null);
  const [showAddTrade, setShowAddTrade] = useState(false);
  const [tradesFilter, setTradesFilter] = useState(initialNavState.tradesFilter || { dateFrom: '', dateTo: '' });
  const [selectedTrade, setSelectedTrade] = useState(initialNavState.selectedTrade || null);
  const [tradeNavList, setTradeNavList] = useState([]);
  const [tradeDetailIntent, setTradeDetailIntent] = useState(initialNavState.tradeDetailIntent || null);
  const [selectedDate, setSelectedDate] = useState(initialNavState.selectedDate || new Date().toISOString().split('T')[0]);
  // Open Day Review on the most recent session, not on today. Today has no
  // trades on a weekend, a holiday, or any day before the market opens.
  const seededDate = useRef(Boolean(initialNavState.selectedDate));
  useEffect(() => {
    if (seededDate.current) return;
    seededDate.current = true;
    kpisApi.get({})
      .then(r => {
        const days = r.data?.daily_pnl || [];
        if (days.length) setSelectedDate(days[days.length - 1].date);
      })
      .catch(() => {});
  }, []);
  const [brainOpen, setBrainOpen] = useState(false);
  const [reportsInitialTab, setReportsInitialTab] = useState(initialNavState.reportsInitialTab || 'overview');

  const loadAccounts = useCallback(async () => {
    try {
      const res = await accountsApi.list();
      setAccounts(res.data);
    } catch (e) {
      console.error('Failed to load accounts', e);
    }
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  useEffect(() => {
    writeJournalNavState({
      page,
      selectedAccountId,
      tradesFilter,
      selectedTrade,
      tradeDetailIntent,
      selectedDate,
      reportsInitialTab,
    });
  }, [page, selectedAccountId, tradesFilter, selectedTrade, tradeDetailIntent, selectedDate, reportsInitialTab]);

  const handleTradeAdded = () => {
    setShowAddTrade(false);
    if (page === 'dashboard') setPage('_refresh');
    setTimeout(() => setPage('dashboard'), 0);
  };

  const handleCalendarDayClick = (date) => {
    setSelectedDate(date);
    setPage('day-review');
  };

  const navigate = (p, options = {}) => {
    if (p !== 'trades') setTradesFilter({ dateFrom: '', dateTo: '' });
    if (p !== 'trade-detail') {
      setSelectedTrade(null);
      setTradeDetailIntent(null);
    }
    if (p === 'reports') setReportsInitialTab(options.tab || 'overview');
    setPage(p);
  };

  const handleOpenDetail = (trade, list = [], options = {}) => {
    setSelectedTrade(trade);
    setTradeNavList(list);
    setTradeDetailIntent(options.intent || null);
    setPage('trade-detail');
  };

  return (
    <div className="app-shell">
      <AppHeader
        page={page}
        onNavigate={navigate}
        accounts={accounts}
        selectedAccountId={selectedAccountId}
        onSelectAccount={setSelectedAccountId}
        onAddTrade={() => setShowAddTrade(true)}
        onAccountCreated={loadAccounts}
        brainOpen={brainOpen}
        onToggleBrain={() => setBrainOpen(v => !v)}
        onSignOut={onSignOut}
      />

      <main className="app-main" id="main">
        {page === 'dashboard' && (
          <Dashboard
            accountId={selectedAccountId}
            accounts={accounts}
            selectedAccountId={selectedAccountId}
            onSelectAccount={setSelectedAccountId}
            onDayClick={handleCalendarDayClick}
            onOpenDetail={handleOpenDetail}
            onViewAllTrades={() => navigate('trades')}
            onViewSmokingGun={() => navigate('reports', { tab: 'smoking-gun' })}
          />
        )}
        {page === 'trades' && (
          <Trades
            key={`${tradesFilter.dateFrom}|${tradesFilter.dateTo}`}
            accountId={selectedAccountId}
            initialDateFrom={tradesFilter.dateFrom}
            initialDateTo={tradesFilter.dateTo}
            onOpenDetail={handleOpenDetail}
            onSetRisk={(trade, list) => handleOpenDetail(trade, list, { intent: 'planned-risk' })}
          />
        )}
        {page === 'trade-detail' && selectedTrade && (
          <TradeDetail
            key={selectedTrade.id}
            trade={selectedTrade}
            tradeNavList={tradeNavList}
            onBack={() => { setSelectedTrade(null); setTradeDetailIntent(null); setPage('trades'); }}
            onTradeUpdate={(updated) => setSelectedTrade(updated)}
            onNavigate={(trade) => { setTradeDetailIntent(null); setSelectedTrade(trade); }}
            onOpenDetail={handleOpenDetail}
            focusPlannedRisk={tradeDetailIntent === 'planned-risk'}
          />
        )}
        {page === 'calendar' && (
          <Calendar
            accountId={selectedAccountId}
            onDayClick={handleCalendarDayClick}
          />
        )}
        {page === 'import' && (
          <Import
            accountId={selectedAccountId}
            accounts={accounts}
            onImportDone={() => {}}
          />
        )}
        {page === 'diary' && <Diary accountId={selectedAccountId} />}
        {page === 'day-review' && (
          <DailySummary
            accountId={selectedAccountId}
            date={selectedDate}
            onDateChange={setSelectedDate}
            onOpenDetail={handleOpenDetail}
          />
        )}
        {page === 'reports' && <Reports accountId={selectedAccountId} initialTab={reportsInitialTab} />}
        {page === 'help' && <Help />}
        {page === 'settings' && <Settings accounts={accounts} onAccountsChanged={loadAccounts} />}
      </main>

      <Brain accountId={selectedAccountId} open={brainOpen} onOpenChange={setBrainOpen} />

      {showAddTrade && (
        <AddTradeModal
          accounts={accounts}
          defaultAccountId={selectedAccountId}
          onClose={() => setShowAddTrade(false)}
          onSaved={handleTradeAdded}
        />
      )}
    </div>
  );
}


export default function App() {
  const required = authRequired();
  const [session, setSession] = useState(() => getSession());
  const [passwordRecovery, setPasswordRecovery] = useState(false);

  useEffect(() => {
    if (!required) return undefined;

    const recoverySession = consumePasswordRecoveryCallback();
    if (recoverySession) {
      setSession(recoverySession);
      setPasswordRecovery(true);
    }

    const sync = () => setSession(getSession());
    window.addEventListener(AUTH_CHANGED_EVENT, sync);

    if (!recoverySession) {
      restoreSession().then((restored) => {
        if (restored) setSession(restored);
      });
    }

    const stopAutoRefresh = startSessionAutoRefresh({
      onSession: setSession,
      onSignedOut: () => setSession(null),
    });

    return () => {
      stopAutoRefresh();
      window.removeEventListener(AUTH_CHANGED_EVENT, sync);
    };
  }, [required]);

  if (!required) return <JournalApp />;
  if (!authConfigured()) {
    return (
      <main className="login-shell">
        <div className="login-card" role="alert">
          <h1>Deployment configuration required</h1>
          <p>Supabase public authentication settings are missing from this build.</p>
        </div>
      </main>
    );
  }
  if (passwordRecovery && session?.access_token) {
    return (
      <PasswordReset
        onUpdatePassword={async (password) => {
          await updatePassword(password);
          setPasswordRecovery(false);
          setSession(getSession());
        }}
        onCancel={() => {
          clearSession();
          setSession(null);
          setPasswordRecovery(false);
        }}
      />
    );
  }

  if (!session?.access_token) {
    return (
      <Login
        onSignIn={async (email, password) => {
          const next = await signInWithPassword(email, password);
          setSession(next);
        }}
        onResetPassword={requestPasswordReset}
      />
    );
  }

  return <JournalApp onSignOut={() => {
    clearSession();
    setSession(null);
  }} />;
}
