import { useState, useEffect, useCallback, useRef } from 'react';
import { Search, ChevronUp, ChevronDown } from 'lucide-react';
import { tradesApi } from '../api';
import TradeRow from './TradeRow';
import { PageHeader, KpiStrip, KpiCell, MoneyValue } from './ui';

const PAGE_SIZE = 25;
const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const fmtDay = (d) => {
  if (!d) return '';
  const [y, m, day] = d.split('-');
  return `${MONTHS_SHORT[Number(m) - 1]} ${Number(day)}, ${y}`;
};

function getOpenTime(trade) {
  const execs = trade.executions || [];
  if (!execs.length) return '';
  return [...execs].sort((a, b) => (a.time || '').localeCompare(b.time || ''))[0].time || '';
}

function SortIcon({ col, sortCol, sortDir }) {
  if (sortCol !== col) return <ChevronDown size={12} style={{ opacity: 0.35 }} aria-hidden="true" />;
  return sortDir === 'asc'
    ? <ChevronUp size={12} aria-hidden="true" />
    : <ChevronDown size={12} aria-hidden="true" />;
}

export default function Trades({ accountId, initialDateFrom = '', initialDateTo = '', onOpenDetail, onSetRisk }) {
  const [trades, setTrades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [isMobileView, setIsMobileView] = useState(() => (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(max-width: 768px)').matches
  ));

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
    const query = window.matchMedia('(max-width: 768px)');
    const sync = () => setIsMobileView(query.matches);
    sync();
    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', sync);
      return () => query.removeEventListener('change', sync);
    }
    query.addListener(sync);
    return () => query.removeListener(sync);
  }, []);

  const [customSetups, setCustomSetups] = useState([]);
  const reloadCustomSetups = useCallback(async () => {
    try {
      const res = await tradesApi.listCustomSetups();
      setCustomSetups(res.data || []);
    } catch { /* dropdown still works with the built-in setups */ }
  }, []);
  useEffect(() => { reloadCustomSetups(); }, [reloadCustomSetups]);

  const [sortCol, setSortCol] = useState('datetime');
  const [sortDir, setSortDir] = useState('desc');

  const [ticker, setTicker] = useState('');
  const [instrType, setInstrType] = useState('');
  const [reviewFilter, setReviewFilter] = useState('');
  const [dateFrom, setDateFrom] = useState(initialDateFrom);
  const [dateTo, setDateTo] = useState(initialDateTo);

  // A slow reply from an earlier filter must not overwrite the newest one.
  const loadRun = useRef(0);
  const load = useCallback(async () => {
    const run = ++loadRun.current;
    setLoading(true);
    setError(null);
    try {
      const params = {};
      if (accountId != null) params.account_id = accountId;
      if (ticker) params.ticker = ticker;
      if (instrType) params.instrument_type = instrType;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const res = await tradesApi.list(params);
      if (run !== loadRun.current) return;
      setTrades(res.data);
    } catch (e) {
      if (run !== loadRun.current) return;
      setError(e.message);
    } finally {
      if (run === loadRun.current) setLoading(false);
    }
  }, [accountId, ticker, instrType, dateFrom, dateTo]);

  useEffect(() => { load(); setPage(1); }, [load]);

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortCol(col);
      setSortDir(col === 'datetime' ? 'desc' : 'asc');
    }
    setPage(1);
  };

  const reviewCount = (trade) => [trade.entry_reason, trade.exit_reason, trade.mistakes]
    .filter(value => String(value || '').trim()).length;

  const visibleTrades = trades.filter(trade => {
    const count = reviewCount(trade);
    if (reviewFilter === 'needs') return count < 3;
    if (reviewFilter === 'reviewed') return count === 3;
    if (reviewFilter === 'mistake') return Boolean(String(trade.mistakes || '').trim());
    return true;
  });

  const sorted = [...visibleTrades].sort((a, b) => {
    let av, bv;
    switch (sortCol) {
      case 'datetime':
        av = (a.date || '') + '|' + getOpenTime(a);
        bv = (b.date || '') + '|' + getOpenTime(b);
        break;
      case 'ticker': av = a.ticker || ''; bv = b.ticker || ''; break;
      case 'net_pnl': av = a.net_pnl ?? 0; bv = b.net_pnl ?? 0; break;
      case 'r_multiple':
        av = a.realized_r ?? a.r_multiple ?? -999;
        bv = b.realized_r ?? b.r_multiple ?? -999;
        break;
      default: av = a.date || ''; bv = b.date || '';
    }
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return sortDir === 'asc' ? cmp : -cmp;
  });

  const completedTrades = visibleTrades.filter(trade => !trade.is_open);
  const totalNet = completedTrades.reduce((sum, trade) => sum + (trade.net_pnl || 0), 0);
  const winners = completedTrades.filter(trade => (trade.net_pnl || 0) > 0).length;
  const winRate = completedTrades.length ? (winners / completedTrades.length * 100).toFixed(1) : 0;

  const paginated = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
  const firstShown = sorted.length ? (page - 1) * PAGE_SIZE + 1 : 0;
  const lastShown = Math.min(page * PAGE_SIZE, sorted.length);

  const period = dateFrom || dateTo
    ? `${dateFrom ? fmtDay(dateFrom) : 'Start'} to ${dateTo ? fmtDay(dateTo) : 'today'}`
    : 'All dates';

  // Plain render function (not a component) so headers keep focus across re-sorts.
  const sortTh = (col, label, className) => (
    <th key={col} className={className} aria-sort={sortCol === col ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="th-sort" onClick={() => handleSort(col)} data-active={sortCol === col ? 'true' : undefined}>
        {label} <SortIcon col={col} sortCol={sortCol} sortDir={sortDir} />
      </button>
    </th>
  );

  return (
    <div className="trade-view-page">
      <PageHeader
        title="Trade View"
        subtitle="Scan results, strategy, review quality, excursion and R in one place. Click any row for the full trade review."
      />

      <div className="trade-view-kpis">
        <KpiStrip label="Filtered trade summary">
        <KpiCell label="Selected period" value={<span style={{ fontSize: 22 }}>{period}</span>} />
        <KpiCell label="Net P&L" value={<MoneyValue value={totalNet} />} tone={totalNet >= 0 ? 'pos' : 'neg'} />
        <KpiCell label="Trades" value={<span className="num">{visibleTrades.length.toLocaleString('en-US')}</span>} />
        <KpiCell label="Win rate" value={<span className="num">{winRate}%</span>} foot={<><span className="num">{winners}</span> winners</>} />
        </KpiStrip>
      </div>

      {/* Filter bar */}
      <div role="search" aria-label="Filter trades" className="trade-view-filters">
        <div className="trade-view-filter trade-view-filter-ticker">
          <label className="field-label" htmlFor="tv-ticker">Ticker</label>
          <div className="trade-view-search">
            <Search size={15} aria-hidden="true" />
            <input
              id="tv-ticker"
              placeholder="Ticker..."
              value={ticker}
              onChange={e => setTicker(e.target.value)}
            />
          </div>
        </div>
        <div className="trade-view-filter">
          <label className="field-label" htmlFor="tv-type">Type</label>
          <select id="tv-type" value={instrType} onChange={e => setInstrType(e.target.value)}>
            <option value="">All Types</option>
            <option value="STOCK">Stock</option>
            <option value="OPTION">Option</option>
            <option value="FUTURE">Future</option>
          </select>
        </div>
        <div className="trade-view-filter">
          <label className="field-label" htmlFor="tv-review">Review</label>
          <select id="tv-review" value={reviewFilter} onChange={e => { setReviewFilter(e.target.value); setPage(1); }}>
            <option value="">All reviews</option>
            <option value="needs">Needs review</option>
            <option value="reviewed">Fully reviewed</option>
            <option value="mistake">Mistake flagged</option>
          </select>
        </div>
        <div className="trade-view-filter">
          <label className="field-label" htmlFor="tv-from">From</label>
          <input id="tv-from" type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
        </div>
        <div className="trade-view-filter">
          <label className="field-label" htmlFor="tv-to">To</label>
          <input id="tv-to" type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} />
        </div>
        {(ticker || instrType || reviewFilter || dateFrom || dateTo) && (
          <button
            type="button"
            className="btn btn-ghost trade-view-clear"
            onClick={() => { setTicker(''); setInstrType(''); setReviewFilter(''); setDateFrom(''); setDateTo(''); }}
          >
            Clear filters
          </button>
        )}
      </div>

      {error && (
        <div className="notice neg" role="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <section className="card panel-flush trade-view-results" aria-label="Trades">
        {isMobileView ? (
          <div className="trade-view-mobile-list" aria-label="Trades">
            {loading ? (
              [...Array(5)].map((_, i) => (
                <div className="trade-mobile-card trade-mobile-card-skeleton" key={i} aria-hidden="true">
                  <div className="skeleton" style={{ height: 18, width: '44%' }} />
                  <div className="skeleton" style={{ height: 13, width: '70%', marginTop: 8 }} />
                  <div className="skeleton" style={{ height: 52, width: '100%', marginTop: 12 }} />
                </div>
              ))
            ) : paginated.length === 0 ? (
              <div className="empty trade-view-mobile-empty">
                No trades found. Import a CSV to get started.
              </div>
            ) : (
              paginated.map(trade => (
                <TradeRow
                  key={`mobile-${trade.id}`}
                  trade={trade}
                  openTime={getOpenTime(trade)}
                  onOpenDetail={(t) => onOpenDetail(t, paginated)}
                  customSetups={customSetups}
                  onCustomSetupsChanged={reloadCustomSetups}
                  mobile
                />
              ))
            )}
          </div>
        ) : (
          <div className="table-container trade-view-desktop-table">
            <table className="trade-view-table">
              <thead>
                <tr>
                  {sortTh('datetime', 'Date / Time')}
                  {sortTh('ticker', 'Trade')}
                  <th title="The setup or strategy used for this trade.">Setup / Strategy</th>
                  <th className="num" title="Planned reward-to-risk. Realized R appears underneath when available.">Planned R:R</th>
                  <th title="Best move in your favor versus worst move against you while the trade was open.">Best / Worst Move</th>
                  <th title="How much of the favorable move you kept when you exited.">Exit Capture</th>
                  {sortTh('net_pnl', 'Profit / Loss', 'num')}
                  <th><span className="sr-only">Open trade</span></th>
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  [...Array(5)].map((_, i) => (
                    <tr key={i}>
                      {[...Array(8)].map((_, j) => (
                        <td key={j}><div className="skeleton" style={{ height: 16, width: '80%' }} /></td>
                      ))}
                    </tr>
                  ))
                ) : paginated.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="empty">
                      No trades found. Import a CSV to get started.
                    </td>
                  </tr>
                ) : (
                  paginated.map(trade => (
                    <TradeRow
                      key={trade.id}
                      trade={trade}
                      openTime={getOpenTime(trade)}
                      onOpenDetail={(t) => onOpenDetail(t, paginated)}
                      onSetRisk={(t) => onSetRisk && onSetRisk(t, paginated)}
                      customSetups={customSetups}
                      onCustomSetupsChanged={reloadCustomSetups}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        <div className="trade-view-pagination">
          <span className="text-muted" style={{ fontSize: 13 }}>
            Showing <span className="num">{firstShown}-{lastShown}</span> of <span className="num">{sorted.length}</span> trades
          </span>
          {totalPages > 1 && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>
                ← Prev
              </button>
              <span className="text-muted num" style={{ fontSize: 13 }}>Page {page} of {totalPages}</span>
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages}>
                Next →
              </button>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
