from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from csv_parser import FUTURES_MULTIPLIERS

DEFAULT_EXECUTION_TIMEZONE = "America/Chicago"
EPSILON_QTY = 1e-9


def parse_executions(trade: dict) -> list[dict]:
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
    return ("SOLD", "BOT") if str(side or "").upper() == "SHORT" else ("BOT", "SOLD")


def entry_fills(trade: dict) -> list[dict]:
    entry_action, _ = entry_exit_actions(trade.get("side"))
    return [e for e in parse_executions(trade) if str(e.get("action") or "").upper() == entry_action]


def exit_fills(trade: dict) -> list[dict]:
    _, exit_action = entry_exit_actions(trade.get("side"))
    return [e for e in parse_executions(trade) if str(e.get("action") or "").upper() == exit_action]


def execution_timestamp(execution: dict, fallback_date: str | None = None) -> datetime | None:
    """Return an aware execution instant, preferring the immutable UTC timestamp."""
    raw_utc = str(execution.get("timestamp_utc") or "").strip()
    if raw_utc:
        try:
            return datetime.fromisoformat(raw_utc.replace("Z", "+00:00"))
        except ValueError:
            pass

    date_value = str(execution.get("date") or fallback_date or "").strip()
    time_value = str(execution.get("time") or "").strip()
    if not date_value or not time_value:
        return None

    timezone_name = str(execution.get("source_timezone") or DEFAULT_EXECUTION_TIMEZONE)
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_EXECUTION_TIMEZONE)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p"):
        try:
            return datetime.strptime(f"{date_value} {time_value}", fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def execution_clock_minutes(execution: dict) -> float | None:
    """Broker-local clock minutes for time-of-day analysis."""
    raw = str(execution.get("time") or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            value = datetime.strptime(raw, fmt)
            return value.hour * 60 + value.minute + value.second / 60
        except ValueError:
            continue
    return None


def trade_multiplier(trade: dict) -> float:
    instrument = str(trade.get("instrument_type") or "STOCK").upper()
    if instrument == "OPTION":
        return 100.0
    if instrument == "FUTURE":
        ticker = str(trade.get("ticker") or "").upper()
        root = next(
            (r for r in sorted(FUTURES_MULTIPLIERS, key=len, reverse=True) if ticker.startswith(r)),
            None,
        )
        if root:
            return float(FUTURES_MULTIPLIERS[root])
    return 1.0


def position_quantities(trade: dict) -> tuple[float, float]:
    executions = parse_executions(trade)
    qty_bot = sum(float(e.get("qty") or 0) for e in executions if str(e.get("action") or "").upper() == "BOT")
    qty_sold = sum(float(e.get("qty") or 0) for e in executions if str(e.get("action") or "").upper() == "SOLD")
    return qty_bot, qty_sold


def is_open_position(trade: dict) -> bool:
    executions = parse_executions(trade)
    if not executions:
        return False
    qty_bot, qty_sold = position_quantities(trade)
    if qty_bot <= EPSILON_QTY and qty_sold <= EPSILON_QTY:
        return False
    return abs(qty_bot - qty_sold) > EPSILON_QTY


def is_closed_trade(trade: dict) -> bool:
    """Completed-trade eligibility used by all performance surfaces.

    Execution quantity balance is authoritative when executions exist. Legacy
    rows without execution detail fall back to a populated realized net P&L.
    """
    if trade.get("net_pnl") is None:
        return False

    executions = parse_executions(trade)
    if not executions:
        return True

    qty_bot, qty_sold = position_quantities(trade)
    return (
        qty_bot > EPSILON_QTY
        and qty_sold > EPSILON_QTY
        and abs(qty_bot - qty_sold) <= EPSILON_QTY
    )


def completed_trades(trades: list[dict]) -> list[dict]:
    return [trade for trade in trades if is_closed_trade(trade)]


def weighted_price(fills: list[dict]) -> float | None:
    qty = sum(float(fill.get("qty") or 0) for fill in fills)
    if qty <= EPSILON_QTY:
        return None
    return sum(
        float(fill.get("qty") or 0) * float(fill.get("price") or 0)
        for fill in fills
    ) / qty


def entry_notional(trade: dict) -> float | None:
    entries = entry_fills(trade)
    qty = sum(float(fill.get("qty") or 0) for fill in entries)
    avg_entry = weighted_price(entries)
    if qty <= EPSILON_QTY or avg_entry is None or avg_entry <= 0:
        return None
    return abs(qty * avg_entry * trade_multiplier(trade))


def trade_pl_percent(trade: dict) -> float | None:
    if trade.get("net_pnl") is None:
        return None
    denominator = entry_notional(trade)
    if denominator is None or denominator <= 0:
        return None
    return round(float(trade.get("net_pnl") or 0) / denominator * 100, 2)


def first_entry_datetime(trade: dict) -> datetime | None:
    values = [
        execution_timestamp(fill, trade.get("date"))
        for fill in entry_fills(trade)
    ]
    values = [value for value in values if value is not None]
    return min(values) if values else None


def last_exit_datetime(trade: dict) -> datetime | None:
    values = [
        execution_timestamp(fill, trade.get("date"))
        for fill in exit_fills(trade)
    ]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def hold_seconds(trade: dict) -> float | None:
    entry = first_entry_datetime(trade)
    exit_ = last_exit_datetime(trade)
    if entry is None or exit_ is None:
        return None
    try:
        seconds = (exit_ - entry).total_seconds()
    except TypeError:
        return None
    return seconds if seconds >= 0 else None


def first_entry_clock_minutes(trade: dict) -> float | None:
    values = [
        execution_clock_minutes(fill)
        for fill in entry_fills(trade)
    ]
    values = [value for value in values if value is not None]
    return min(values) if values else None


def realized_r(
    trade: dict,
    risk_per_trade: float | None = None,
    stored_r_multiple: float | None = None,
) -> float | None:
    """Canonical realized R.

    Explicit planned dollar risk is authoritative because R = realized net P&L
    divided by planned risk. A legacy stored R is only a fallback when the
    planned risk amount is unavailable.
    """
    if risk_per_trade is not None:
        risk = abs(float(risk_per_trade))
        if risk > 0 and trade.get("net_pnl") is not None:
            return round(float(trade.get("net_pnl") or 0) / risk, 4)
    if stored_r_multiple is not None:
        return round(float(stored_r_multiple), 4)
    return None


def recompute_closed_pnl(trade: dict) -> dict | None:
    """Rebuild gross/net P&L from execution cash flows for invariant checks."""
    if not is_closed_trade(trade):
        return None

    executions = parse_executions(trade)
    if not executions:
        return None

    multiplier = trade_multiplier(trade)
    gross = 0.0
    commissions = 0.0
    for execution in executions:
        qty = float(execution.get("qty") or 0)
        price = float(execution.get("price") or 0)
        action = str(execution.get("action") or "").upper()
        if qty <= 0 or price < 0:
            continue
        value = qty * price * multiplier
        if action == "BOT":
            gross -= value
        elif action == "SOLD":
            gross += value
        commissions += abs(float(execution.get("commission") or 0))

    return {
        "gross_pnl": round(gross, 2),
        "commissions": round(commissions, 2),
        "net_pnl": round(gross - commissions, 2),
    }


def net_profit_factor(trades: list[dict]) -> float | None:
    rows = completed_trades(trades)
    wins = sum(float(t.get("net_pnl") or 0) for t in rows if float(t.get("net_pnl") or 0) > 0)
    losses = abs(sum(float(t.get("net_pnl") or 0) for t in rows if float(t.get("net_pnl") or 0) < 0))
    return round(wins / losses, 2) if losses > 0 else None


def gross_profit_factor(trades: list[dict]) -> float | None:
    rows = completed_trades(trades)
    wins = sum(float(t.get("gross_pnl") or 0) for t in rows if float(t.get("gross_pnl") or 0) > 0)
    losses = abs(sum(float(t.get("gross_pnl") or 0) for t in rows if float(t.get("gross_pnl") or 0) < 0))
    return round(wins / losses, 2) if losses > 0 else None


def expectancy(trades: list[dict]) -> float:
    rows = completed_trades(trades)
    return round(sum(float(t.get("net_pnl") or 0) for t in rows) / len(rows), 2) if rows else 0.0


def daily_net_pnl(trades: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for trade in completed_trades(trades):
        date = str(trade.get("date") or "")
        if date:
            totals[date] += float(trade.get("net_pnl") or 0)
    return dict(totals)


def accuracy_issues(trade: dict, tolerance: float = 0.02) -> list[dict]:
    """Return deterministic stored-vs-execution invariant violations."""
    issues: list[dict] = []
    rebuilt = recompute_closed_pnl(trade)
    if rebuilt is None:
        return issues

    for field in ("gross_pnl", "commissions", "net_pnl"):
        stored = trade.get(field)
        if stored is None:
            issues.append({"field": field, "kind": "missing", "expected": rebuilt[field], "actual": None})
            continue
        actual = round(float(stored), 2)
        if abs(actual - rebuilt[field]) > tolerance:
            issues.append({
                "field": field,
                "kind": "execution_mismatch",
                "expected": rebuilt[field],
                "actual": actual,
            })
    return issues
