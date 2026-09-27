import { useState, useEffect, useRef } from 'react';
import { createChart, ColorType, CrosshairMode, LineStyle } from 'lightweight-charts';
import { chartApi, tradesApi } from '../api';

// lightweight-charts paints to canvas and cannot resolve CSS var(), so colours
// are read from the design tokens at render time. Fallbacks are the token values.
function cssVar(name, fallback) {
  if (typeof window === 'undefined') return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}
function withAlpha(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
function chartTheme() {
  return {
    bg: cssVar('--surface-panel', '#1B222D'),
    text: cssVar('--text-secondary', '#96A4B6'),
    grid: cssVar('--divider-soft', '#262F3D'),
    border: cssVar('--divider', '#303B4B'),
    up: cssVar('--result-pos', '#66D7AC'),
    down: cssVar('--result-neg', '#F28B94'),
    vwap: cssVar('--text-secondary', '#96A4B6'),
    ema8: '#F5C451',
    prevLevel: cssVar('--accent-line', '#91A8FF'),
    premarketLevel: cssVar('--caution', '#E9BA78'),
    stop: cssVar('--caution', '#E9BA78'),
    target: cssVar('--accent-line', '#91A8FF'),
  };
}

// lightweight-charts renders labels from UTC-like timestamps. We intentionally
// project real instants onto an ET wall-clock timeline, but do it with IANA time zones
// so DST is correct for every trade date.
export const MARKET_TIME_ZONE = 'America/New_York';
export const BROKER_EXECUTION_TIME_ZONE = 'America/Chicago';

function partsInZone(instant, timeZone) {
  return Object.fromEntries(
    new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(instant)
      .filter(p => p.type !== 'literal')
      .map(p => [p.type, p.value])
  );
}

export function zonedWallTimeToInstant(dateStr, timeStr, timeZone) {
  if (!dateStr || !timeStr) return null;
  const [year, month, day] = dateStr.split('-').map(Number);
  const [hour, minute, second = 0] = timeStr.split(':').map(Number);
  if (![year, month, day, hour, minute, second].every(Number.isFinite)) return null;

  const desiredWallUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
  let utcMs = desiredWallUtcMs;
  const formatter = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hourCycle: 'h23',
  });

  for (let i = 0; i < 2; i += 1) {
    const parts = Object.fromEntries(
      formatter.formatToParts(new Date(utcMs))
        .filter(p => p.type !== 'literal')
        .map(p => [p.type, p.value])
    );
    const renderedWallUtcMs = Date.UTC(
      Number(parts.year), Number(parts.month) - 1, Number(parts.day),
      Number(parts.hour), Number(parts.minute), Number(parts.second)
    );
    utcMs += desiredWallUtcMs - renderedWallUtcMs;
  }
  return new Date(utcMs);
}

export function toTs(isoUtcStr) {
  const instant = new Date(isoUtcStr);
  if (Number.isNaN(instant.getTime())) return null;
  const p = partsInZone(instant, MARKET_TIME_ZONE);
  return Math.floor(Date.UTC(
    Number(p.year), Number(p.month) - 1, Number(p.day),
    Number(p.hour), Number(p.minute), Number(p.second)
  ) / 1000);
}

// Daily/weekly bars are whole calendar dates and do not need an intraday ET projection.
const toDayTs = (isoUtcStr) => Math.floor(new Date(isoUtcStr).getTime() / 1000);

function executionInstant(fill, fallbackDate) {
  const canonical = String(fill?.timestamp_utc || '').trim();
  if (canonical) {
    const instant = new Date(canonical);
    if (!Number.isNaN(instant.getTime())) return instant;
  }

  const dateStr = fill?.date || fallbackDate;
  const timeStr = fill?.time;
  const sourceZone = fill?.source_timezone || BROKER_EXECUTION_TIME_ZONE;
  return zonedWallTimeToInstant(dateStr, timeStr, sourceZone);
}

function projectedExecutionTs(instant, bucketMin = 5) {
  if (!instant) return null;
  const p = partsInZone(instant, MARKET_TIME_ZONE);
  const totalMin = Math.floor((Number(p.hour) * 60 + Number(p.minute)) / bucketMin) * bucketMin;
  const hour = Math.floor(totalMin / 60);
  const minute = totalMin % 60;

  return Math.floor(Date.UTC(
    Number(p.year), Number(p.month) - 1, Number(p.day),
    hour, minute, 0
  ) / 1000);
}

// Legacy helper retained for stored executions that predate canonical timestamp provenance.
export function execToTs(dateStr, timeStr, bucketMin = 5) {
  return projectedExecutionTs(
    zonedWallTimeToInstant(dateStr, timeStr, BROKER_EXECUTION_TIME_ZONE),
    bucketMin,
  );
}

export function executionTimeLabelET(dateOrFill, timeStr = null) {
  const fill = typeof dateOrFill === 'object'
    ? dateOrFill
    : { date: dateOrFill, time: timeStr };
  const instant = executionInstant(fill, fill.date);
  if (!instant) return null;
  const p = partsInZone(instant, MARKET_TIME_ZONE);
  const seconds = String(p.second || '00');
  return fill.timestamp_precision === 'second'
    ? `${p.hour}:${p.minute}:${seconds} ET`
    : `${p.hour}:${p.minute} ET`;
}

function formatFillDetail(fill) {
  const qty = Number(fill.qty || 0);
  const price = Number(fill.price || 0);
  return `${qty}@${price.toFixed(2)}`;
}

export function buildExecutionMarkerGroups(executions, fallbackDate, bucketMin = 5) {
  const groups = new Map();

  for (const fill of executions || []) {
    if (!fill?.time) continue;
    const instant = executionInstant(fill, fallbackDate);
    const ts = projectedExecutionTs(instant, bucketMin);
    const minuteLabel = executionTimeLabelET(fill);
    if (!ts || !minuteLabel) continue;

    const isBuy = String(fill.action || '').toUpperCase() === 'BOT';
    const key = `${ts}|${isBuy ? 'BUY' : 'SELL'}`;
    if (!groups.has(key)) {
      groups.set(key, { isBuy, time: ts, minutes: new Map() });
    }
    const group = groups.get(key);
    if (!group.minutes.has(minuteLabel)) group.minutes.set(minuteLabel, []);
    group.minutes.get(minuteLabel).push(fill);
  }

  return [...groups.values()]
    .map(group => {
      const minuteParts = [...group.minutes.entries()].map(([minuteLabel, fills]) => {
        const counts = new Map();
        for (const fill of fills) {
          const detail = formatFillDetail(fill);
          counts.set(detail, (counts.get(detail) || 0) + 1);
        }
        const details = [...counts.entries()]
          .map(([detail, count]) => count > 1 ? `${detail} ×${count}` : detail)
          .join(' + ');
        return `${minuteLabel} · ${details}`;
      });
      return {
        isBuy: group.isBuy,
        time: group.time,
        text: minuteParts.join(' | '),
      };
    })
    .sort((a, b) => a.time - b.time || Number(b.isBuy) - Number(a.isBuy));
}

const avgPrice = (fills) => {
  const qty = fills.reduce((s, f) => s + (f.qty || 0), 0);
  if (!qty) return null;
  return fills.reduce((s, f) => s + (f.qty || 0) * (f.price || 0), 0) / qty;
};

const TIMEFRAMES = [
  { id: '1Min', label: '1m' },
  { id: '3Min', label: '3m' },
  { id: '5Min', label: '5m' },
  { id: '10Min', label: '10m' },
  { id: '15Min', label: '15m' },
  { id: '30Min', label: '30m' },
  { id: '1Hour', label: '1H' },
  { id: '1Day', label: 'Daily' },
  { id: '1Week', label: 'Weekly' },
];
const TF_MINUTES = { '1Min': 1, '3Min': 3, '5Min': 5, '10Min': 10, '15Min': 15, '30Min': 30, '1Hour': 60 };
const WIDE_RANGE_TFS = new Set(['1Day', '1Week']);
// How many calendar days of backward history each timeframe will load before
// the lazy-load-on-zoom-out gives up. Finer intraday bars get a small cap so a
// single request doesn't balloon (1-min bars for 90 days would be ~35k bars);
// daily/weekly can go much further back since even a decade of daily bars is
// only ~2500 rows.
const MAX_DAYS_BACK = {
  '1Min': 5, '3Min': 10, '5Min': 20, '10Min': 30, '15Min': 45, '30Min': 60, '1Hour': 90,
  '1Day': 3650, '1Week': 5475,
};
// Default window per timeframe. 1m-15m load the trade day and open on its regular
// session (9:30-16:00 ET); zooming out past the first bar pulls in earlier days.
// 30m/1H open on the last month and daily/weekly on the last year, ending on the
// trade date.
const INITIAL_DAYS_BACK = { '30Min': 31, '1Hour': 31, '1Day': 366, '1Week': 366 };
const SESSION_TFS = new Set(['1Min', '3Min', '5Min', '10Min', '15Min']);
// Chart times live on the ET-shifted timeline (see toTs), so ET wall-clock
// times parse as literal UTC.
const etWallTs = (dateStr, hhmm) => Math.floor(new Date(`${dateStr}T${hhmm}:00Z`).getTime() / 1000);
// Legend entries that can be switched on and off. Not remembered between trades.
const DEFAULT_VISIBLE = { buy: true, sell: true, vwap: true, ema8: true, prevLevels: true, premarketLevels: true, sl: true, target: true };

// Show or hide the toggleable layers on an existing chart.
function applyLayers(layers, visible) {
  if (!layers) return;
  (layers.vwap || []).forEach(line => line.applyOptions({ visible: visible.vwap }));
  if (layers.ema8) layers.ema8.applyOptions({ visible: visible.ema8 });
  (layers.levels || []).forEach(({ line, group, name }) => {
    const shown = visible[group];
    line.applyOptions({
      lineVisible: shown,
      axisLabelVisible: shown,
      title: shown ? name : '',
    });
  });
  if (layers.sl) layers.sl.applyOptions({ lineVisible: visible.sl, axisLabelVisible: visible.sl, title: visible.sl ? 'SL' : '' });
  if (layers.target) layers.target.applyOptions({ lineVisible: visible.target, axisLabelVisible: visible.target, title: visible.target ? 'Target' : '' });
  if (layers.candles) {
    const shown = layers.markers
      .filter(m => (m.isBuy ? visible.buy : visible.sell))
      .map(({ isBuy, ...m }) => m);
    layers.candles.setMarkers(shown);
  }
}

export default function TradingChart({
  ticker, date, tradeGroup = null, defaultTimeframe = '10Min',
  executions = [], side = 'LONG', analysis = null,
  height = 320,
}) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const [bars, setBars] = useState([]);
  const [daysBack, setDaysBack] = useState(() => INITIAL_DAYS_BACK[defaultTimeframe] || 1);
  const [warning, setWarning] = useState(null);
  const [leLevels, setLeLevels] = useState({});
  const [levelFeed, setLevelFeed] = useState(null);
  const [levelWarning, setLevelWarning] = useState(null);
  const [loading, setLoading] = useState(true);
  const isWide = WIDE_RANGE_TFS.has(timeframe);
  const [visible, setVisible] = useState(DEFAULT_VISIBLE);
  const visibleRef = useRef(DEFAULT_VISIBLE);
  // Handles to everything a legend toggle controls, so toggling never rebuilds
  // the chart (and never loses the current zoom).
  const layersRef = useRef({ candles: null, vwap: [], ema8: null, levels: [], markers: [], sl: null, target: null });
  // Set right before a zoom-out-triggered fetch, holding the visible window so
  // it can be restored once the wider dataset lands — otherwise the chart would
  // jump back to fitContent() every time more history streams in.
  const savedRangeRef = useRef(null);
  const loadingMoreRef = useRef(false);
  // True only for the very first fetch after a ticker/date/timeframe change —
  // distinct from daysBack itself, since daily/weekly start at a large
  // default (240/730) rather than 1, so "daysBack === 1" can't tell an
  // initial load apart from a background zoom-out extension for those.
  const isInitialFetchRef = useRef(true);
  // Identifies the current ticker/date/timeframe selection, so a single effect
  // can tell "this is a new selection" apart from "daysBack grew from a
  // zoom-out" without needing a second effect — two effects both reacting to
  // a timeframe change fire in the same commit before state settles, which
  // fired the fetch once with the stale daysBack and again with the reset
  // value once it caught up.
  const selectionKeyRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setLeLevels({});
    setLevelFeed(null);
    setLevelWarning(null);

    if (!tradeGroup) return () => { cancelled = true; };

    tradesApi.getLeLevels(tradeGroup)
      .then(r => {
        if (cancelled) return;
        setLeLevels(r.data?.levels || {});
        setLevelFeed(r.data?.feed || null);
        const warnings = Array.isArray(r.data?.warnings) ? r.data.warnings : [];
        setLevelWarning(warnings[0] || null);
      })
      .catch(() => {
        if (!cancelled) setLevelWarning('LE chart levels unavailable.');
      });

    return () => { cancelled = true; };
  }, [tradeGroup]);

  useEffect(() => {
    const key = `${ticker}|${date}|${timeframe}`;
    const isNewSelection = selectionKeyRef.current !== key;

    if (isNewSelection) {
      selectionKeyRef.current = key;
      savedRangeRef.current = null;
      loadingMoreRef.current = false;
      isInitialFetchRef.current = true;
      const initialDaysBack = INITIAL_DAYS_BACK[timeframe] || 1;
      if (initialDaysBack !== daysBack) {
        // Resolve daysBack first and let the re-render (with settled state)
        // do the actual fetch, instead of fetching now with the stale value.
        setDaysBack(initialDaysBack);
        return;
      }
    }

    const isInitialLoad = isInitialFetchRef.current;
    if (isInitialLoad) { setLoading(true); setBars([]); setWarning(null); }
    chartApi.get(ticker, date, timeframe, daysBack)
      .then(r => { setBars(r.data.bars || []); setWarning(r.data.warning || null); })
      .catch(() => { if (isInitialLoad) setWarning('Failed to load chart data'); })
      .finally(() => {
        if (isInitialLoad) setLoading(false);
        isInitialFetchRef.current = false;
        loadingMoreRef.current = false;
      });
  }, [ticker, date, timeframe, daysBack]);

  useEffect(() => {
    if (loading || !bars.length || !containerRef.current) return;

    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }

    const barTs = isWide ? toDayTs : toTs;
    const T = chartTheme();
    layersRef.current = { candles: null, vwap: [], ema8: null, levels: [], markers: [], sl: null, target: null };

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: T.bg },
        textColor: T.text,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: T.grid },
        horzLines: { color: T.grid },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: {
        borderColor: T.border,
        scaleMargins: { top: 0.1, bottom: 0.22 },
      },
      timeScale: {
        borderColor: T.border,
        timeVisible: !isWide,
        secondsVisible: false,
      },
      width: containerRef.current.clientWidth,
      height,
    });
    chartRef.current = chart;

    // ── Candlestick series ────────────────────────────────────────────────
    const candleSeries = chart.addCandlestickSeries({
      upColor: T.up,
      downColor: T.down,
      borderUpColor: T.up,
      borderDownColor: T.down,
      wickUpColor: T.up,
      wickDownColor: T.down,
    });

    const candleData = bars.map(b => ({
      time: barTs(b.t),
      open: b.o, high: b.h, low: b.l, close: b.c,
    }));
    candleSeries.setData(candleData);
    layersRef.current.candles = candleSeries;

    // ── 8 EMA (10-minute review default) ────────────────────────────────
    if (timeframe === '10Min' && candleData.length) {
      const alpha = 2 / (8 + 1);
      let ema = candleData[0].close;
      const emaData = candleData.map((point, index) => {
        ema = index === 0 ? point.close : (point.close * alpha) + (ema * (1 - alpha));
        return { time: point.time, value: ema };
      });
      const emaSeries = chart.addLineSeries({
        color: T.ema8,
        lineWidth: 2,
        lineStyle: LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: true,
        title: '8 EMA',
        crosshairMarkerVisible: true,
      });
      emaSeries.setData(emaData);
      layersRef.current.ema8 = emaSeries;
    }

    // ── VWAP line ─────────────────────────────────────────────────────────
    // Alpaca's per-bar `vw` is just that bar's own volume-weighted price, which
    // tracks the candles almost exactly and isn't a useful indicator on its own.
    // A session VWAP is the running cumulative average from the open, so it's
    // built here as a running sum rather than plotted bar-by-bar. Only meaningful
    // within a single session, so skip it on the daily/weekly wide-context view.
    if (!isWide) {
      // Restarts at 9:30 ET each day: bars are on the ET-shifted timeline, so the
      // UTC date and minutes read back out are ET. Pre-market and after-hours bars
      // get no VWAP. One line per session, so days are not joined to each other.
      const sessions = [];
      let current = null;
      let cumPV = 0;
      let cumVol = 0;
      for (const b of bars) {
        const ts = barTs(b.t);
        const d = new Date(ts * 1000);
        const minuteOfDay = d.getUTCHours() * 60 + d.getUTCMinutes();
        if (minuteOfDay < 9 * 60 + 30 || minuteOfDay >= 16 * 60) continue;
        const day = d.toISOString().slice(0, 10);
        if (!current || current.day !== day) {
          current = { day, points: [] };
          sessions.push(current);
          cumPV = 0;
          cumVol = 0;
        }
        if (b.vw != null && b.v) {
          cumPV += b.vw * b.v;
          cumVol += b.v;
        }
        if (cumVol > 0) current.points.push({ time: ts, value: cumPV / cumVol });
      }
      const withPoints = sessions.filter(sess => sess.points.length);
      layersRef.current.vwap = withPoints.map((sess, i) => {
        const isLast = i === withPoints.length - 1;
        const line = chart.addLineSeries({
          color: T.vwap,
          lineWidth: 2,
          lineStyle: LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: isLast,
          title: isLast ? 'VWAP' : '',
          crosshairMarkerVisible: isLast,
        });
        line.setData(sess.points);
        return line;
      });
    }

    // ── Volume histogram ──────────────────────────────────────────────────
    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: 'vol',
    });
    chart.priceScale('vol').applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
      borderVisible: false,
    });
    volSeries.setData(bars.map(b => ({
      time: barTs(b.t),
      value: b.v,
      color: b.c >= b.o ? withAlpha(T.up, 0.32) : withAlpha(T.down, 0.32),
    })));

    // ── Price lines: entry, exit, stop, target ────────────────────────────
    const entryFills = executions.filter(e => side === 'LONG' ? e.action === 'BOT' : e.action === 'SOLD');
    const exitFills  = executions.filter(e => side === 'LONG' ? e.action === 'SOLD' : e.action === 'BOT');
    const ae = avgPrice(entryFills);
    const ax = avgPrice(exitFills);

    if (ae) candleSeries.createPriceLine({ price: ae, color: T.up, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Entry' });
    if (ax) candleSeries.createPriceLine({ price: ax, color: T.down, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: 'Exit' });
    if (analysis?.stop_loss) layersRef.current.sl = candleSeries.createPriceLine({ price: Number(analysis.stop_loss), color: T.stop, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'SL' });
    if (analysis?.target_price) layersRef.current.target = candleSeries.createPriceLine({ price: Number(analysis.target_price), color: T.target, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: 'Target' });

    // ── LE reference levels: PDH / PDL / PMH / PML ─────────────────────
    // These are fetched from the same deterministic SIP-first LE engine used
    // by LE Review, so the visual chart and rule analysis share one source.
    if (!isWide) {
      const levelSpecs = [
        { name: 'PDH', value: leLevels?.PDH, group: 'prevLevels', color: T.prevLevel, style: LineStyle.Solid },
        { name: 'PDL', value: leLevels?.PDL, group: 'prevLevels', color: T.prevLevel, style: LineStyle.Solid },
        { name: 'PMH', value: leLevels?.PMH, group: 'premarketLevels', color: T.premarketLevel, style: LineStyle.Dashed },
        { name: 'PML', value: leLevels?.PML, group: 'premarketLevels', color: T.premarketLevel, style: LineStyle.Dashed },
      ];

      layersRef.current.levels = levelSpecs
        .filter(spec => Number.isFinite(Number(spec.value)) && Number(spec.value) > 0)
        .map(spec => ({
          name: spec.name,
          group: spec.group,
          line: candleSeries.createPriceLine({
            price: Number(spec.value),
            color: spec.color,
            lineWidth: 2,
            lineStyle: spec.style,
            axisLabelVisible: true,
            title: spec.name,
          }),
        }));
    }

    // ── Execution markers ────────────────────────────────────────────────
    // Colored and shaped by the actual fill action (matches the Buy/Sell legend
    // below the chart), not by entry/exit role — a short's opening fill is a SELL,
    // so styling it as "entry = green/up" made it look like a buy. Only meaningful
    // at intraday resolution: on the daily/weekly view a 35-minute trade is a
    // fraction of one bar, so there's nothing sensible to anchor a marker to.
    if (!isWide) {
      const bucketMin = TF_MINUTES[timeframe] || 5;
      const markers = buildExecutionMarkerGroups(executions, date, bucketMin)
        .map(marker => ({
          ...marker,
          position: marker.isBuy ? 'belowBar' : 'aboveBar',
          color: marker.isBuy ? T.up : T.down,
          shape: marker.isBuy ? 'arrowUp' : 'arrowDown',
        }));
      layersRef.current.markers = markers;
    }
    applyLayers(layersRef.current, visibleRef.current);

    // Restore the pre-fetch window when this render is a background history
    // extension (below), so the view holds still instead of snapping back to
    // fitContent() every time more bars land.
    if (savedRangeRef.current) {
      chart.timeScale().setVisibleRange(savedRangeRef.current);
      savedRangeRef.current = null;
    } else if (SESSION_TFS.has(timeframe)) {
      // Open on the trade day's regular session. Falls back to the whole load
      // when the day has no bars inside 9:30-16:00.
      const bucketSec = (TF_MINUTES[timeframe] || 5) * 60;
      const from = etWallTs(date, '09:30');
      const to = etWallTs(date, '16:00') - bucketSec;
      const inSession = candleData.some(c => c.time >= from && c.time <= to);
      if (inSession) chart.timeScale().setVisibleRange({ from, to });
      else chart.timeScale().fitContent();
    } else {
      chart.timeScale().fitContent();
    }

    // Zoom-out-to-load-more-history: barsBefore counts real bars between the
    // left edge of the visible window and the first loaded bar. Right after
    // fitContent() it's exactly 0 — the window's left edge sits precisely on
    // the first bar, showing only what's loaded (a single session for the
    // fine intraday timeframes, by design). It only goes NEGATIVE once the
    // user actually zooms or pans past the start of the data and blank space
    // is on screen — that's the real trigger. A positive threshold like the
    // "< 10" this used to be misfires on every initial load, since a fresh
    // fitContent() view already satisfies it at 0.
    const maxDays = MAX_DAYS_BACK[timeframe] || 30;
    // Only a zoom or pan by the user loads more history. Resizes and the initial
    // positioning also move the visible range and must not pull in extra days.
    let userMoved = false;
    const markUserMove = () => { userMoved = true; };
    const el = containerRef.current;
    ['wheel', 'mousedown', 'touchstart'].forEach(evt => el.addEventListener(evt, markUserMove, { passive: true }));
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || !userMoved || loadingMoreRef.current || daysBack >= maxDays) return;
      const barsInfo = candleSeries.barsInLogicalRange(range);
      if (barsInfo != null && barsInfo.barsBefore < -1) {
        loadingMoreRef.current = true;
        savedRangeRef.current = chart.timeScale().getVisibleRange();
        setDaysBack(d => Math.min(maxDays, Math.max(d * 3, d + 4)));
      }
    });

    const ro = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      ['wheel', 'mousedown', 'touchstart'].forEach(evt => el.removeEventListener(evt, markUserMove));
      chart.remove();
      chartRef.current = null;
    };
  }, [bars, executions, side, analysis, leLevels, height, loading, date, timeframe, isWide, daysBack]);

  useEffect(() => {
    visibleRef.current = visible;
    applyLayers(layersRef.current, visible);
  }, [visible]);

  const toggle = (key) => setVisible(v => ({ ...v, [key]: !v[key] }));
  const hasPrevLevels = ['PDH', 'PDL'].some(k => Number.isFinite(Number(leLevels?.[k])));
  const hasPremarketLevels = ['PMH', 'PML'].some(k => Number.isFinite(Number(leLevels?.[k])));
  const feedLabel = levelFeed === 'sip'
    ? 'SIP'
    : levelFeed === 'delayed_sip'
      ? 'Delayed SIP'
      : levelFeed === 'iex'
        ? 'IEX fallback'
        : levelFeed ? String(levelFeed).toUpperCase() : null;
  const legendItems = [
    { key: 'buy', label: 'Buy fill', swatch: { width: 9, height: 9, borderRadius: '50%', background: 'var(--result-pos)' } },
    { key: 'sell', label: 'Sell fill', swatch: { width: 9, height: 9, borderRadius: '50%', background: 'var(--result-neg)' } },
    !isWide && { key: 'vwap', label: 'VWAP', swatch: { width: 16, height: 2, background: 'var(--text-secondary)' } },
    timeframe === '10Min' && { key: 'ema8', label: '8 EMA', swatch: { width: 16, height: 2, background: '#F5C451' } },
    !isWide && hasPrevLevels && { key: 'prevLevels', label: 'PDH / PDL', swatch: { width: 16, height: 2, background: 'var(--accent-line)' } },
    !isWide && hasPremarketLevels && { key: 'premarketLevels', label: 'PMH / PML', swatch: { width: 16, height: 2, borderTop: '2px dashed var(--caution)' } },
    analysis?.stop_loss && { key: 'sl', label: 'SL', swatch: { width: 16, height: 2, background: 'var(--caution)' } },
    analysis?.target_price && { key: 'target', label: 'Target', swatch: { width: 16, height: 2, background: 'var(--accent-line)' } },
  ].filter(Boolean);

  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: 8, flexWrap: 'wrap', gap: 8,
      }}>
        <h2 className="section-title" style={{ fontSize: 17 }}>
          {ticker} · {TIMEFRAMES.find(t => t.id === timeframe)?.label} Chart · <span className="num text-muted" style={{ fontWeight: 500 }}>{date}</span>
        </h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <div className="seg" role="group" aria-label="Chart timeframe">
            {TIMEFRAMES.map(tf => (
              <button
                type="button"
                key={tf.id}
                className="seg-btn"
                aria-pressed={timeframe === tf.id}
                onClick={() => setTimeframe(tf.id)}
              >
                {tf.label}
              </button>
            ))}
          </div>
          {executions.length > 0 && timeframe !== '1Min' && (
            <button
              type="button"
              className="chart-legend-item"
              onClick={() => setTimeframe('1Min')}
              title="Switch to 1-minute candles so execution markers can be placed at the exact broker-reported minute."
            >
              Exact fills · 1m
            </button>
          )}
        </div>
      </div>

      {loading ? (
        <div className="skeleton" style={{ height, borderRadius: 8 }} />
      ) : warning && !bars.length ? (
        <div role="status" style={{
          display: 'flex', alignItems: 'center', gap: 10,
          color: 'var(--text-secondary)', fontSize: 13.5,
          background: 'var(--surface-inset)', borderRadius: 'var(--radius-md)',
          padding: '14px 16px', borderLeft: '2px solid var(--caution)',
        }}>
          {warning}
        </div>
      ) : (
        <>
          {warning && <div role="status" style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>{warning}</div>}
          <div className="chart-legend" role="group" aria-label="Show or hide chart layers">
            {legendItems.map(item => (
              <button
                type="button"
                key={item.key}
                className="chart-legend-item"
                aria-pressed={visible[item.key]}
                title={`${visible[item.key] ? 'Hide' : 'Show'} ${item.label}`}
                onClick={() => toggle(item.key)}
              >
                <span aria-hidden="true" style={{ display: 'inline-block', ...item.swatch }} />
                {item.label}
              </button>
            ))}
            {!isWide && feedLabel && (hasPrevLevels || hasPremarketLevels) && (
              <span className="text-muted" title={levelWarning || undefined} style={{ fontSize: 11.5, marginLeft: 4 }}>
                LE levels · {feedLabel}
              </span>
            )}
          </div>
          {!isWide && executions.length > 0 && (
            <div className="text-muted" style={{ fontSize: 11.5, margin: '3px 0 7px' }}>
              {timeframe === '1Min'
                ? 'Execution markers are shown at the broker source minute. Multiple fills in the same minute are grouped without losing quantity or price.'
                : `Execution markers are anchored to the containing ${TIMEFRAMES.find(t => t.id === timeframe)?.label || timeframe} candle; labels preserve the broker-reported ET minute. Use Exact fills · 1m for minute-by-minute placement.`}
            </div>
          )}
          <div ref={containerRef} style={{ width: '100%', background: 'var(--surface-panel)', borderRadius: 'var(--radius-md)' }} />
        </>
      )}
    </div>
  );
}
