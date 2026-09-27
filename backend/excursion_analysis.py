"""Deterministic MAE/MFE and exit-efficiency calculations.

Execution timestamps remain the source of truth for when a trade was open.
Alpaca 1-minute bars provide the market path.

Stocks use stock bars with actual execution-price anchors. Options use the
actual OCC option contract's 1-minute premium bars with Schwab option fills as
entry/exit anchors. Futures may still use a configured ETF proxy, so futures
excursion remains market-context rather than contract-premium excursion.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
EXCURSION_ALGORITHM_VERSION = "2026.09.27.2"


def _execs(trade: dict) -> list[dict]:
    raw = trade.get("executions") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def _exec_dt(e: dict) -> datetime | None:
    """Resolve an execution to the exchange (ET) timeline.

    Canonical UTC broker timestamps are authoritative. Legacy executions without
    provenance preserve the historical ET fallback so older/manual rows remain
    interpretable instead of being silently shifted.
    """
    raw_utc = str(e.get("timestamp_utc") or "").strip()
    if raw_utc:
        try:
            return datetime.fromisoformat(raw_utc.replace("Z", "+00:00")).astimezone(ET)
        except Exception:
            pass

    date = str(e.get("date") or "").strip()
    time = str(e.get("time") or "").strip()
    if not date or not time:
        return None

    source_timezone = str(e.get("source_timezone") or "").strip()
    try:
        naive = datetime.fromisoformat(f"{date}T{time}")
        if source_timezone:
            return naive.replace(tzinfo=ZoneInfo(source_timezone)).astimezone(ET)
        return naive.replace(tzinfo=ET)
    except Exception:
        return None


def _weighted_price(rows: list[dict]) -> float | None:
    qty = sum(float(r.get("qty") or 0) for r in rows)
    if qty <= 0:
        return None
    return sum(float(r.get("qty") or 0) * float(r.get("price") or 0) for r in rows) / qty


def trade_window(trade: dict) -> dict | None:
    side = str(trade.get("side") or "").upper()
    entry_action = "BOT" if side == "LONG" else "SOLD"
    exit_action = "SOLD" if side == "LONG" else "BOT"
    rows = _execs(trade)
    entries = sorted(
        [e for e in rows if str(e.get("action") or "").upper() == entry_action and _exec_dt(e)],
        key=_exec_dt,
    )
    exits = sorted(
        [e for e in rows if str(e.get("action") or "").upper() == exit_action and _exec_dt(e)],
        key=_exec_dt,
    )
    if not entries or not exits:
        return None
    return {
        "entry_dt": _exec_dt(entries[0]),
        "exit_dt": _exec_dt(exits[-1]),
        "first_entry_price": float(entries[0].get("price") or 0),
        "avg_entry": _weighted_price(entries),
        "avg_exit": _weighted_price(exits),
        "entries": entries,
        "exits": exits,
    }


def underlying_direction(trade: dict) -> int:
    """Return +1 when an underlying rise is favorable, -1 when a fall is favorable."""
    side = str(trade.get("side") or "").upper()
    inst = str(trade.get("instrument_type") or "STOCK").upper()
    if inst == "OPTION":
        opt = str(trade.get("option_type") or "").upper()
        if opt == "PUT":
            return -1 if side == "LONG" else 1
        return 1 if side == "LONG" else -1
    return 1 if side == "LONG" else -1


def _bar_dt(bar: dict) -> datetime | None:
    raw = str(bar.get("t") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
    except Exception:
        return None


def _bar_for_minute(bars: list[dict], dt: datetime) -> dict | None:
    minute = dt.replace(second=0, microsecond=0)
    for b in bars:
        bd = _bar_dt(b)
        if bd and bd.replace(second=0, microsecond=0) == minute:
            return b
    return None


def _execution_path_efficiency(
    trade: dict,
    bars: list[dict],
) -> tuple[float | None, float, float, float, float, float | None]:
    """Execution-aware P&L path and return excursion.

    Broker fills define cash flow, position size and cumulative entry capital.
    One-minute bars mark only periods when a position was actually open. When a
    fill occurs inside a minute, full high/low is intentionally withheld because
    its ordering relative to the fill is unknowable; actual fills plus the bar
    close are used instead.

    Return excursion is measured against cumulative entry capital deployed at
    that point. This keeps scale-ins honest (early moves are not diluted by
    capital added later) while partial exits retain the original deployed-capital
    denominator for the remaining trade.
    """
    executions = sorted(
        [e for e in _execs(trade) if _exec_dt(e)],
        key=_exec_dt,
    )
    if not executions:
        return None, 0.0, 0.0, 0.0, 0.0, None

    entry_action = "SOLD" if str(trade.get("side") or "").upper() == "SHORT" else "BOT"

    cashflow = 0.0
    signed_qty = 0.0
    entry_capital = 0.0
    max_pnl = 0.0
    min_pnl = 0.0
    max_return_pct = 0.0
    min_return_pct = 0.0
    idx = 0

    def mark(price: float | None):
        nonlocal max_pnl, min_pnl, max_return_pct, min_return_pct
        if price is None or price <= 0:
            return
        pnl = cashflow + signed_qty * float(price)
        max_pnl = max(max_pnl, pnl)
        min_pnl = min(min_pnl, pnl)
        if entry_capital > 1e-12:
            ret = pnl / entry_capital * 100
            max_return_pct = max(max_return_pct, ret)
            min_return_pct = min(min_return_pct, ret)

    def apply_execution(execution: dict):
        nonlocal cashflow, signed_qty, entry_capital
        qty = float(execution.get("qty") or 0)
        price = float(execution.get("price") or 0)
        action = str(execution.get("action") or "").upper()
        if qty <= 0 or price <= 0:
            return
        if action == "BOT":
            cashflow -= qty * price
            signed_qty += qty
        elif action == "SOLD":
            cashflow += qty * price
            signed_qty -= qty
        else:
            return
        if action == entry_action:
            entry_capital += qty * price
        mark(price)

    ordered_bars = sorted(
        [b for b in bars or [] if _bar_dt(b)],
        key=_bar_dt,
    )

    for bar in ordered_bars:
        bar_start = _bar_dt(bar)
        bar_end = bar_start + timedelta(minutes=1)

        while idx < len(executions) and _exec_dt(executions[idx]) < bar_start:
            apply_execution(executions[idx])
            idx += 1

        minute_execs = []
        probe = idx
        while probe < len(executions) and _exec_dt(executions[probe]) < bar_end:
            minute_execs.append(executions[probe])
            probe += 1

        if signed_qty != 0 and not minute_execs:
            high = float(bar.get("h") or 0)
            low = float(bar.get("l") or 0)
            if signed_qty > 0:
                mark(high)
                mark(low)
            else:
                mark(low)
                mark(high)

        for execution in minute_execs:
            apply_execution(execution)
            idx += 1

        if signed_qty != 0 and minute_execs:
            mark(float(bar.get("c") or 0))

    while idx < len(executions):
        apply_execution(executions[idx])
        idx += 1

    realized_pnl = cashflow
    max_pnl = max(max_pnl, realized_pnl)
    min_pnl = min(min_pnl, realized_pnl)
    realized_return_pct = (
        realized_pnl / entry_capital * 100 if entry_capital > 1e-12 else None
    )
    if realized_return_pct is not None:
        max_return_pct = max(max_return_pct, realized_return_pct)
        min_return_pct = min(min_return_pct, realized_return_pct)

    net_pnl = float(trade.get("net_pnl") or 0)
    efficiency = None
    if net_pnl > 0 and realized_pnl > 0 and max_pnl > 1e-12:
        efficiency = max(0.0, min(100.0, realized_pnl / max_pnl * 100))

    return (
        efficiency,
        max_pnl,
        min_pnl,
        max_return_pct,
        min_return_pct,
        realized_return_pct,
    )


def calculate_trade_excursion(trade: dict, bars: list[dict], *, bar_basis: str | None = None) -> dict:
    """Calculate market excursion while the trade was open.

    bar_basis describes the supplied market path:
      - stock_1m: actual stock bars
      - option_premium_1m: actual OCC option premium bars
      - proxy_1m: proxy/underlying bars (currently futures fallback)

    For stocks and options, execution fills anchor the realized entry/exit.
    Including those fills in the extrema prevents sparse 1-minute bars from
    reporting an MFE smaller than a realized profitable exit.
    """
    window = trade_window(trade)
    if not window:
        return {"available": False, "reason": "Trade is not closed or timestamps are incomplete."}
    entry_dt = window["entry_dt"]
    exit_dt = window["exit_dt"]
    if exit_dt < entry_dt:
        return {"available": False, "reason": "Exit timestamp precedes entry timestamp."}

    start_min = entry_dt.replace(second=0, microsecond=0)
    end_min = exit_dt.replace(second=0, microsecond=0)
    held = []
    for b in bars or []:
        bd = _bar_dt(b)
        if bd and start_min <= bd.replace(second=0, microsecond=0) <= end_min:
            held.append(b)
    if not held:
        return {"available": False, "reason": "No 1-minute market bars overlap the holding window."}

    inst = str(trade.get("instrument_type") or "STOCK").upper()
    side = str(trade.get("side") or "LONG").upper()
    entry_bar = _bar_for_minute(held, entry_dt)
    exit_bar = _bar_for_minute(held, exit_dt)

    if inst == "STOCK":
        basis = bar_basis or "stock_1m"
        entry_ref = window["first_entry_price"]
        exit_ref = window["avg_exit"]
        direction = underlying_direction(trade)
        actual_instrument_path = True
    elif inst == "OPTION":
        if bar_basis != "option_premium_1m":
            return {
                "available": False,
                "reason": "Option excursion requires contract-level option premium bars.",
            }
        basis = "option_premium_1m"
        entry_ref = window["first_entry_price"]
        exit_ref = window["avg_exit"]
        # On the option contract itself, LONG benefits from a premium rise and
        # SHORT benefits from a premium fall. Call/put direction is irrelevant.
        direction = 1 if side == "LONG" else -1
        actual_instrument_path = True
    else:
        basis = bar_basis or "proxy_1m"
        entry_ref = float((entry_bar or held[0]).get("c") or 0)
        exit_ref = float((exit_bar or held[-1]).get("c") or 0)
        direction = underlying_direction(trade)
        actual_instrument_path = False

    if not entry_ref or not exit_ref:
        return {"available": False, "reason": "Entry/exit market reference price is unavailable."}

    highs = [float(b.get("h") or 0) for b in held if float(b.get("h") or 0) > 0]
    lows = [float(b.get("l") or 0) for b in held if float(b.get("l") or 0) > 0]
    if not highs or not lows:
        return {"available": False, "reason": "Market bars are missing high/low prices."}

    if actual_instrument_path:
        # Sparse option bars can miss the exact second of a real broker fill.
        # The fills are authoritative and must be part of the attainable path.
        highs.extend([float(entry_ref), float(exit_ref)])
        lows.extend([float(entry_ref), float(exit_ref)])

    if direction > 0:
        favorable_price = max(highs)
        adverse_price = min(lows)
        mfe_pct = max(0.0, (favorable_price - entry_ref) / entry_ref * 100)
        mae_pct = max(0.0, (entry_ref - adverse_price) / entry_ref * 100)
        captured_pct = (exit_ref - entry_ref) / entry_ref * 100
    else:
        favorable_price = min(lows)
        adverse_price = max(highs)
        mfe_pct = max(0.0, (entry_ref - favorable_price) / entry_ref * 100)
        mae_pct = max(0.0, (adverse_price - entry_ref) / entry_ref * 100)
        captured_pct = (entry_ref - exit_ref) / entry_ref * 100

    efficiency = None
    path_max_pnl = None
    path_min_pnl = None
    path_max_return_pct = None
    path_min_return_pct = None
    realized_return_pct = None
    if actual_instrument_path:
        (
            efficiency,
            path_max_pnl,
            path_min_pnl,
            path_max_return_pct,
            path_min_return_pct,
            realized_return_pct,
        ) = _execution_path_efficiency(trade, held)
        # For stocks/options, Best/Worst Move is the actual position return path,
        # not a first-fill price comparison. This is execution-aware for scale-ins
        # and partial exits and guarantees the realized exit is part of the path.
        mfe_pct = max(0.0, float(path_max_return_pct or 0.0))
        mae_pct = max(0.0, -float(path_min_return_pct or 0.0))
        if realized_return_pct is not None:
            captured_pct = float(realized_return_pct)

    notes = {
        "stock_1m": "Stock excursion uses Alpaca 1-minute stock bars with actual Schwab fills as entry/exit anchors.",
        "option_premium_1m": "Option excursion uses Alpaca 1-minute OCC option-premium bars with actual Schwab option fills as entry/exit anchors.",
        "proxy_1m": "Futures excursion uses the configured ETF proxy and should be interpreted as market context.",
    }
    note = notes.get(basis, "Excursion uses 1-minute market bars and broker execution timestamps.")

    return {
        "available": True,
        "mfe_pct": round(mfe_pct, 4),
        "mae_pct": round(mae_pct, 4),
        "exit_efficiency": round(efficiency, 2) if efficiency is not None else None,
        "captured_directional_pct": round(captured_pct, 4),
        "path_max_pnl_per_unit": round(path_max_pnl, 6) if path_max_pnl is not None else None,
        "path_min_pnl_per_unit": round(path_min_pnl, 6) if path_min_pnl is not None else None,
        "entry_reference": round(entry_ref, 6),
        "exit_reference": round(exit_ref, 6),
        "favorable_price": round(favorable_price, 6),
        "adverse_price": round(adverse_price, 6),
        "basis": basis,
        "bar_count": len(held),
        "resolution": "1Min",
        "algorithm_version": EXCURSION_ALGORITHM_VERSION,
        "note": note,
    }
