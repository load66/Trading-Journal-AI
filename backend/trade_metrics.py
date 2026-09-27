from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from csv_parser import FUTURES_MULTIPLIERS

BROKER_EXECUTION_TIMEZONE = os.getenv("TRADE_EXECUTION_TIMEZONE", "America/Chicago")
MARKET_TIMEZONE = "America/New_York"


def load_executions(trade: dict) -> list[dict]:
    raw = trade.get("executions") or []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def entry_exit_actions(side: str | None) -> tuple[str, str]:
    return ("BOT", "SOLD") if str(side or "LONG").upper() == "LONG" else ("SOLD", "BOT")


def execution_datetime(
    execution: dict,
    *,
    fallback_date: str | None = None,
    target_timezone: str | None = None,
) -> datetime | None:
    """Return one execution as an aware datetime.

    Canonical UTC provenance wins when present. Legacy Schwab/TOS rows are
    interpreted in TRADE_EXECUTION_TIMEZONE (America/Chicago by default), which
    matches the broker-export wall clock used by this journal.
    """
    raw_utc = str(execution.get("timestamp_utc") or "").strip()
    instant = None
    if raw_utc:
        try:
            instant = datetime.fromisoformat(raw_utc.replace("Z", "+00:00"))
        except ValueError:
            instant = None

    if instant is None:
        date = str(execution.get("date") or fallback_date or "").strip()
        time = str(execution.get("time") or "").strip()
        if not date or not time:
            return None
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p"):
            try:
                parsed = datetime.strptime(f"{date} {time}", fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
        source_tz = str(execution.get("source_timezone") or BROKER_EXECUTION_TIMEZONE)
        try:
            instant = parsed.replace(tzinfo=ZoneInfo(source_tz))
        except Exception:
            return None

    if target_timezone:
        try:
            return instant.astimezone(ZoneInfo(target_timezone))
        except Exception:
            return None
    return instant


def execution_sort_key(execution: dict, fallback_date: str | None = None):
    dt = execution_datetime(execution, fallback_date=fallback_date)
    return (
        dt or datetime.max.replace(tzinfo=ZoneInfo("UTC")),
        int(execution.get("source_row") or 0),
    )


def split_entry_exit(trade: dict) -> tuple[list[dict], list[dict]]:
    entry_action, exit_action = entry_exit_actions(trade.get("side"))
    execs = load_executions(trade)
    date = trade.get("date")
    entries = [e for e in execs if str(e.get("action") or "").upper() == entry_action]
    exits = [e for e in execs if str(e.get("action") or "").upper() == exit_action]
    entries.sort(key=lambda e: execution_sort_key(e, date))
    exits.sort(key=lambda e: execution_sort_key(e, date))
    return entries, exits


def weighted_price(fills: list[dict]) -> float | None:
    qty = sum(float(e.get("qty") or 0) for e in fills)
    if qty <= 0:
        return None
    return sum(float(e.get("qty") or 0) * float(e.get("price") or 0) for e in fills) / qty


def position_quantities(trade: dict) -> tuple[float, float]:
    execs = load_executions(trade)
    bot = sum(float(e.get("qty") or 0) for e in execs if str(e.get("action") or "").upper() == "BOT")
    sold = sum(float(e.get("qty") or 0) for e in execs if str(e.get("action") or "").upper() == "SOLD")
    return bot, sold


def trade_is_closed(trade: dict) -> bool:
    """Completed-trade predicate used by every performance surface."""
    execs = load_executions(trade)
    if not execs:
        return trade.get("net_pnl") is not None
    bot, sold = position_quantities(trade)
    return bot > 0 and sold > 0 and abs(bot - sold) < 1e-6


def instrument_multiplier(instrument_type: str | None, ticker: str | None = None) -> float:
    instrument = str(instrument_type or "STOCK").upper()
    if instrument == "OPTION":
        return 100.0
    if instrument == "FUTURE":
        symbol = str(ticker or "").upper()
        root = next((r for r in sorted(FUTURES_MULTIPLIERS, key=len, reverse=True) if symbol.startswith(r)), None)
        return float(FUTURES_MULTIPLIERS[root]) if root else 1.0
    return 1.0


def entry_notional(trade: dict) -> float | None:
    entries, _ = split_entry_exit(trade)
    qty = sum(float(e.get("qty") or 0) for e in entries)
    avg = weighted_price(entries)
    if qty <= 0 or avg is None or avg <= 0:
        return None
    return abs(avg * qty * instrument_multiplier(trade.get("instrument_type"), trade.get("ticker")))


def trade_pl_percent(trade: dict) -> float | None:
    notional = entry_notional(trade)
    if notional or notional == 0:
        return None if notional is None or notional <= 0 else round(float(trade.get("net_pnl") or 0) / notional * 100, 2)
    return None


def trade_entry_exit_datetimes(trade: dict, target_timezone: str | None = None):
    entries, exits = split_entry_exit(trade)
    if not entries or not exits:
        return None, None
    fallback = trade.get("date")
    return (
        execution_datetime(entries[0], fallback_date=fallback, target_timezone=target_timezone),
        execution_datetime(exits[-1], fallback_date=fallback, target_timezone=target_timezone),
    )


def hold_seconds(trade: dict) -> float | None:
    entry_dt, exit_dt = trade_entry_exit_datetimes(trade)
    if not entry_dt or not exit_dt:
        return None
    seconds = (exit_dt - entry_dt).total_seconds()
    return seconds if seconds >= 0 else None


def is_overnight_trade(trade: dict) -> bool:
    """Whether first entry and final exit fall on different U.S. market dates."""
    entry_dt, exit_dt = trade_entry_exit_datetimes(trade, target_timezone=MARKET_TIMEZONE)
    if not entry_dt or not exit_dt:
        return False
    return entry_dt.date() != exit_dt.date()


def entry_market_minutes(trade: dict) -> int | None:
    entry_dt, _ = trade_entry_exit_datetimes(trade, target_timezone=MARKET_TIMEZONE)
    if not entry_dt:
        return None
    return entry_dt.hour * 60 + entry_dt.minute


def manual_pnl(
    side: str,
    instrument_type: str,
    ticker: str | None,
    entry: float,
    exit_price: float | None,
    qty: float,
    commissions: float,
) -> tuple[float, float]:
    if exit_price is None:
        return 0.0, 0.0
    multiplier = instrument_multiplier(instrument_type, ticker)
    gross = (
        (exit_price - entry) if str(side).upper() == "LONG"
        else (entry - exit_price)
    ) * qty * multiplier
    gross = round(gross, 2)
    return gross, round(gross - float(commissions or 0), 2)


def daily_totals(trades: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for trade in trades:
        date = str(trade.get("date") or "")
        if not date:
            continue
        totals[date] = totals.get(date, 0.0) + float(trade.get("net_pnl") or 0)
    return totals


def median(values: list[float]) -> float | None:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return None
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
