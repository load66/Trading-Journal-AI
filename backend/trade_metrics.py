from __future__ import annotations

import json
import os
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

DEFAULT_EXECUTION_TIMEZONE = os.getenv("TRADE_EXECUTION_TIMEZONE", "America/Chicago")
MARKET_TIMEZONE = "America/New_York"

# Canonical point values used anywhere the app converts futures prices into dollars.
FUTURES_MULTIPLIERS = {
    "/ES": 50,
    "/MES": 5,
    "/NQ": 20,
    "/MNQ": 2,
    "/YM": 5,
    "/MYM": 0.5,
    "/RTY": 50,
    "/M2K": 5,
}

TRADE_MATH_VERSION = "2026.09.27.1"


def parse_executions(trade_or_value) -> list[dict]:
    raw = trade_or_value.get("executions") if isinstance(trade_or_value, dict) else trade_or_value
    if isinstance(raw, list):
        return [dict(e) for e in raw if isinstance(e, dict)]
    if isinstance(raw, str):
        try:
            value = json.loads(raw or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return [dict(e) for e in value if isinstance(e, dict)] if isinstance(value, list) else []
    return []


def entry_exit_actions(side: str | None) -> tuple[str, str]:
    return ("BOT", "SOLD") if str(side or "LONG").upper() == "LONG" else ("SOLD", "BOT")


def instrument_multiplier(instrument_type: str | None, ticker: str | None = None) -> float | None:
    instrument = str(instrument_type or "STOCK").upper()
    if instrument == "STOCK":
        return 1.0
    if instrument == "OPTION":
        return 100.0
    if instrument == "FUTURE":
        symbol = str(ticker or "").upper()
        root = next(
            (r for r in sorted(FUTURES_MULTIPLIERS, key=len, reverse=True) if symbol.startswith(r)),
            None,
        )
        return float(FUTURES_MULTIPLIERS[root]) if root else None
    return None


def _parse_clock(value: str | None):
    raw = str(value or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def execution_datetime(
    execution: dict,
    fallback_date: str | None = None,
    target_timezone: str | None = None,
) -> datetime | None:
    """Resolve a broker execution to an aware instant.

    Canonical timestamp_utc wins. Legacy Schwab/TOS rows are interpreted in the
    configured execution timezone (America/Chicago by default), never as ET by
    assumption.
    """
    canonical = str(execution.get("timestamp_utc") or "").strip()
    if canonical:
        try:
            dt = datetime.fromisoformat(canonical.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(ZoneInfo(target_timezone)) if target_timezone else dt
        except (ValueError, TypeError, KeyError):
            pass

    day = str(execution.get("date") or fallback_date or "").strip()
    clock = _parse_clock(execution.get("time"))
    if not day or clock is None:
        return None
    try:
        source_tz = ZoneInfo(str(execution.get("source_timezone") or DEFAULT_EXECUTION_TIMEZONE))
        local_dt = datetime.combine(datetime.strptime(day, "%Y-%m-%d").date(), clock, tzinfo=source_tz)
        return local_dt.astimezone(ZoneInfo(target_timezone)) if target_timezone else local_dt
    except (ValueError, TypeError, KeyError):
        return None


def _qty(execution: dict) -> float:
    try:
        return float(execution.get("qty") or 0)
    except (TypeError, ValueError):
        return 0.0


def _price(execution: dict) -> float:
    try:
        return float(execution.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _fee(execution: dict) -> float:
    try:
        return abs(float(execution.get("commission") or 0))
    except (TypeError, ValueError):
        return 0.0


def execution_fills(trade: dict) -> tuple[list[dict], list[dict]]:
    executions = parse_executions(trade)
    entry_action, exit_action = entry_exit_actions(trade.get("side"))
    entries = [e for e in executions if str(e.get("action") or "").upper() == entry_action]
    exits = [e for e in executions if str(e.get("action") or "").upper() == exit_action]
    return entries, exits


def trade_is_closed(trade: dict) -> bool:
    executions = parse_executions(trade)
    if not executions:
        # Legacy/manual records without fill detail use a realized P&L value as
        # the only available closed-trade signal.
        return trade.get("net_pnl") is not None

    bot = sum(_qty(e) for e in executions if str(e.get("action") or "").upper() == "BOT")
    sold = sum(_qty(e) for e in executions if str(e.get("action") or "").upper() == "SOLD")
    return bot > 0 and sold > 0 and abs(bot - sold) < 1e-6


def closed_trades(trades: list[dict]) -> list[dict]:
    return [trade for trade in trades if trade_is_closed(trade)]


def weighted_price(fills: list[dict]) -> float | None:
    qty = sum(_qty(e) for e in fills)
    if qty <= 0:
        return None
    return sum(_qty(e) * _price(e) for e in fills) / qty


def entry_notional(trade: dict) -> float | None:
    entries, _ = execution_fills(trade)
    multiplier = instrument_multiplier(trade.get("instrument_type"), trade.get("ticker"))
    if not entries or multiplier is None:
        return None
    total = sum(_qty(e) * _price(e) * multiplier for e in entries)
    return total if total > 0 else None


def trade_pl_percent(trade: dict) -> float | None:
    if not trade_is_closed(trade) or trade.get("net_pnl") is None:
        return None
    denominator = entry_notional(trade)
    if denominator is None or denominator <= 0:
        return None
    return round(float(trade.get("net_pnl") or 0) / denominator * 100, 2)


def hold_seconds(trade: dict) -> float | None:
    entries, exits = execution_fills(trade)
    if not entries or not exits:
        return None
    fallback = str(trade.get("date") or "")
    entry_times = [execution_datetime(e, fallback) for e in entries]
    exit_times = [execution_datetime(e, fallback) for e in exits]
    entry_times = [d for d in entry_times if d is not None]
    exit_times = [d for d in exit_times if d is not None]
    if not entry_times or not exit_times:
        return None
    seconds = (max(exit_times) - min(entry_times)).total_seconds()
    return seconds if seconds >= 0 else None


def first_entry_minutes(trade: dict, timezone_name: str = MARKET_TIMEZONE) -> int | None:
    entries, _ = execution_fills(trade)
    fallback = str(trade.get("date") or "")
    dts = [execution_datetime(e, fallback, timezone_name) for e in entries]
    dts = [d for d in dts if d is not None]
    if not dts:
        return None
    dt = min(dts)
    return dt.hour * 60 + dt.minute


def execution_financials(trade: dict, executions: list[dict] | None = None) -> dict:
    """Recompute realized trade dollars directly from broker/manual executions."""
    execs = executions if executions is not None else parse_executions(trade)
    multiplier = instrument_multiplier(trade.get("instrument_type"), trade.get("ticker"))
    fees = round(sum(_fee(e) for e in execs), 2)

    bot_qty = sum(_qty(e) for e in execs if str(e.get("action") or "").upper() == "BOT")
    sold_qty = sum(_qty(e) for e in execs if str(e.get("action") or "").upper() == "SOLD")
    closed = bool(execs) and bot_qty > 0 and sold_qty > 0 and abs(bot_qty - sold_qty) < 1e-6

    if multiplier is None:
        return {
            "closed": closed,
            "multiplier": None,
            "gross_pnl": None,
            "net_pnl": None,
            "commissions": fees,
            "bot_qty": bot_qty,
            "sold_qty": sold_qty,
        }

    if not closed:
        return {
            "closed": False,
            "multiplier": multiplier,
            "gross_pnl": 0.0,
            "net_pnl": 0.0,
            "commissions": fees,
            "bot_qty": bot_qty,
            "sold_qty": sold_qty,
        }

    gross = 0.0
    for e in execs:
        action = str(e.get("action") or "").upper()
        amount = _qty(e) * _price(e) * multiplier
        if action == "BOT":
            gross -= amount
        elif action == "SOLD":
            gross += amount
    gross = round(gross, 2)
    return {
        "closed": True,
        "multiplier": multiplier,
        "gross_pnl": gross,
        "net_pnl": round(gross - fees, 2),
        "commissions": fees,
        "bot_qty": bot_qty,
        "sold_qty": sold_qty,
    }


def performance_summary(trades: list[dict]) -> dict:
    rows = closed_trades(trades)
    pnls = [float(t.get("net_pnl") or 0) for t in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    flats = [p for p in pnls if p == 0]
    total = len(rows)

    daily: dict[str, float] = {}
    for trade in rows:
        day = str(trade.get("date") or "")
        if day:
            daily[day] = daily.get(day, 0.0) + float(trade.get("net_pnl") or 0)

    win_sum = sum(wins)
    loss_sum = sum(losses)
    trading_days = len(daily)
    green_days = sum(1 for value in daily.values() if value > 0)
    red_days = sum(1 for value in daily.values() if value < 0)
    flat_days = sum(1 for value in daily.values() if value == 0)

    return {
        "rows": rows,
        "total_net_pnl": round(sum(pnls), 2),
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "flat_trades": len(flats),
        "win_rate": round(len(wins) / total * 100, 2) if total else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(win_sum / abs(loss_sum), 2) if loss_sum else None,
        "expectancy": round(sum(pnls) / total, 2) if total else 0.0,
        "daily": daily,
        "trading_days": trading_days,
        "green_days": green_days,
        "red_days": red_days,
        "flat_days": flat_days,
        "day_win_rate": round(green_days / trading_days * 100, 1) if trading_days else 0.0,
    }


def median_value(values: list[float]) -> float | None:
    return float(median(values)) if values else None
