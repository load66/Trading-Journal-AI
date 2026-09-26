from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx


LE_RULESET_VERSION = "LE_2026_09_v4_INTEGRITY"
ET = ZoneInfo("America/New_York")
EXECUTION_TIMEZONE_NAME = os.getenv("TRADE_EXECUTION_TIMEZONE", "America/Chicago")
EXECUTION_TZ = ZoneInfo(EXECUTION_TIMEZONE_NAME)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
ALPACA_FALLBACK_FEED = os.getenv("ALPACA_DATA_FEED", "iex")
ALPACA_TRADING_BASE_URL = os.getenv("ALPACA_TRADING_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")

ALLOWED_STRATEGIES = (
    "LE L-Entry — Level Retest",
    "LE E-Entry — 10m 8 EMA Retest",
    "LE Purple Profits — 8 EMA Pullback",
)

AI_TAGS = {
    "setup": {
        "A++ Level + EMA",
        "Flag-Line-Sign",
    },
    "execution": {
        "Clean Entry",
        "Early Entry",
        "Late Entry",
    },
    "mistake": {
        "Chased Entry",
        "Forced Setup",
        "Sold Too Early",
    },
}

_RULE_TAGS = {
    ("setup", "Outside Day"),
    ("setup", "Inside Day"),
    ("setup", "PDH Break"),
    ("setup", "PDL Break"),
    ("setup", "PMH Break"),
    ("setup", "PML Break"),
    ("mistake", "Entered First 10m"),
    ("mistake", "Airgapped from 8 EMA"),
    ("mistake", "No Level Break"),
    ("mistake", "Traded Chop"),
    ("mistake", "No Market Sign"),
    ("outcome", "Break-Even"),
}


def _parse_executions(trade: dict) -> list[dict]:
    value = trade.get("executions") or []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def market_direction(trade: dict) -> str:
    """Return directional thesis for the underlying, not option ownership side."""
    side = (trade.get("side") or "LONG").upper()
    instrument = (trade.get("instrument_type") or "").upper()
    if instrument == "OPTION":
        option_type = (trade.get("option_type") or "").upper()
        if option_type == "CALL":
            return "bullish" if side == "LONG" else "bearish"
        if option_type == "PUT":
            return "bearish" if side == "LONG" else "bullish"
    return "bullish" if side == "LONG" else "bearish"


def entry_datetime(trade: dict) -> datetime | None:
    side = (trade.get("side") or "LONG").upper()
    entry_action = "BOT" if side == "LONG" else "SOLD"
    candidates: list[datetime] = []
    fallback_date = trade.get("date")
    for fill in _parse_executions(trade):
        if (fill.get("action") or "").upper() != entry_action:
            continue
        day = fill.get("date") or fallback_date
        clock = fill.get("time")
        if not day or not clock:
            continue
        try:
            source_dt = datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=EXECUTION_TZ)
            candidates.append(source_dt.astimezone(ET))
        except ValueError:
            continue
    return min(candidates) if candidates else None


def _bar_dt(bar: dict) -> datetime:
    raw = str(bar.get("t") or "")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def _is_rth(dt: datetime) -> bool:
    return time(9, 30) <= dt.time() < time(16, 0)


def _is_premarket(dt: datetime) -> bool:
    return time(4, 0) <= dt.time() < time(9, 30)


def _aggregate_10m(bars: list[dict]) -> list[dict]:
    buckets: dict[tuple[date, int], dict] = {}
    for bar in bars:
        dt = _bar_dt(bar)
        if not _is_rth(dt):
            continue
        minute = dt.hour * 60 + dt.minute
        slot = ((minute - 570) // 10) * 10 + 570
        key = (dt.date(), slot)
        if key not in buckets:
            start_dt = datetime.combine(
                dt.date(), time(slot // 60, slot % 60), tzinfo=ET
            )
            buckets[key] = {
                "start": start_dt,
                "end": start_dt + timedelta(minutes=10),
                "o": float(bar.get("o") or 0),
                "h": float(bar.get("h") or 0),
                "l": float(bar.get("l") or 0),
                "c": float(bar.get("c") or 0),
                "v": float(bar.get("v") or 0),
            }
        else:
            bucket = buckets[key]
            bucket["h"] = max(bucket["h"], float(bar.get("h") or 0))
            bucket["l"] = min(bucket["l"], float(bar.get("l") or 0))
            bucket["c"] = float(bar.get("c") or 0)
            bucket["v"] += float(bar.get("v") or 0)
    return [buckets[k] for k in sorted(buckets)]


def _ema(values: list[float], period: int = 8) -> float | None:
    if not values:
        return None
    alpha = 2 / (period + 1)
    result = float(values[0])
    for value in values[1:]:
        result = float(value) * alpha + result * (1 - alpha)
    return result


def _last_completed_1m_bar(bars: list[dict], when: datetime) -> dict | None:
    """Return only bars whose close is strictly before the execution timestamp."""
    eligible = [
        b for b in bars
        if _bar_dt(b) + timedelta(minutes=1) < when
    ]
    return max(eligible, key=_bar_dt) if eligible else None


def _session_vwap_snapshot(bars: list[dict], entry_dt: datetime) -> dict:
    """Regular-session HLC3 VWAP using only one-minute bars closed before entry."""
    completed = []
    for bar in bars:
        dt = _bar_dt(bar)
        if (
            dt.date() == entry_dt.date()
            and _is_rth(dt)
            and dt + timedelta(minutes=1) < entry_dt
        ):
            completed.append(bar)

    if not completed:
        return {
            "price": None,
            "vwap": None,
            "position_vs_vwap": "unknown",
            "distance_from_vwap_pct": None,
        }

    last = max(completed, key=_bar_dt)
    price = float(last.get("c") or 0)
    pv = 0.0
    volume = 0.0
    for bar in completed:
        v = float(bar.get("v") or 0)
        if v <= 0:
            continue
        typical = (
            float(bar.get("h") or 0)
            + float(bar.get("l") or 0)
            + float(bar.get("c") or 0)
        ) / 3.0
        pv += typical * v
        volume += v

    vwap = pv / volume if volume > 0 else None
    if vwap is None:
        position = "unknown"
        distance = None
    else:
        distance = abs(price - vwap) / vwap * 100 if vwap else None
        if price > vwap:
            position = "above"
        elif price < vwap:
            position = "below"
        else:
            position = "at"

    return {
        "price": price,
        "vwap": vwap,
        "position_vs_vwap": position,
        "distance_from_vwap_pct": distance,
    }


def _market_sign_status(direction: str, spy: dict, qqq: dict) -> str:
    positions = [spy.get("position_vs_vwap"), qqq.get("position_vs_vwap")]
    if any(p in (None, "unknown") for p in positions):
        return "unknown"

    if direction == "bullish":
        confirms = [p == "above" for p in positions]
        opposes = [p == "below" for p in positions]
    else:
        confirms = [p == "below" for p in positions]
        opposes = [p == "above" for p in positions]

    if all(confirms):
        return "confirmed"
    if all(opposes):
        return "failed"
    return "mixed"


def _first_completed_break(
    bars_10m: list[dict],
    trade_day: date,
    entry_dt: datetime,
    level: float | None,
    direction: str,
) -> datetime | None:
    if level is None:
        return None
    for bar in bars_10m:
        if bar["start"].date() != trade_day or bar["end"] >= entry_dt:
            continue
        close = float(bar["c"])
        if direction == "up" and close > level:
            return bar["end"]
        if direction == "down" and close < level:
            return bar["end"]
    return None


def _session_window(entry_dt: datetime) -> str:
    t = entry_dt.time()
    if time(9, 30) <= t < time(9, 40):
        return "scan_only"
    if time(9, 40) <= t < time(11, 30):
        return "prime"
    if time(11, 30) <= t < time(13, 30):
        return "chop_hour"
    if time(13, 30) <= t < time(15, 0):
        return "cautious"
    if time(15, 0) <= t < time(15, 45):
        return "close_window"
    return "outside_primary_window"


def _parse_hhmm(value: str | None, fallback: time) -> time:
    if not value:
        return fallback
    raw = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%H%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return fallback


def _session_record(row: dict | None) -> dict | None:
    if not row or not row.get("date"):
        return None
    try:
        day = date.fromisoformat(str(row["date"])[:10])
    except ValueError:
        return None
    open_t = _parse_hhmm(row.get("open"), time(9, 30))
    close_t = _parse_hhmm(row.get("close"), time(16, 0))
    return {
        "date": day,
        "open": datetime.combine(day, open_t, tzinfo=ET),
        "close": datetime.combine(day, close_t, tzinfo=ET),
    }


def _calendar_context(rows: list[dict], trade_day: date) -> dict:
    sessions = [s for s in (_session_record(row) for row in rows) if s]
    sessions.sort(key=lambda s: s["date"])
    current = next((s for s in sessions if s["date"] == trade_day), None)
    previous_candidates = [s for s in sessions if s["date"] < trade_day]
    previous = previous_candidates[-1] if previous_candidates else None
    return {
        "verified": current is not None and previous is not None,
        "current": current,
        "previous": previous,
    }


def _within(dt: datetime, start_dt: datetime, end_dt: datetime) -> bool:
    return dt.date() == start_dt.date() and start_dt <= dt < end_dt


def _level_status(
    feed: str | None,
    calendar_verified: bool,
    required_window_end: datetime | None,
) -> str:
    if not calendar_verified or required_window_end is None:
        return "PARTIAL"
    feed = (feed or "").lower()
    if feed == "sip":
        return "VERIFIED"
    if feed == "delayed_sip":
        if required_window_end <= datetime.now(ET) - timedelta(minutes=15):
            return "VERIFIED_HISTORICAL"
        return "DELAYED"
    if feed == "iex":
        return "LIMITED"
    return "PARTIAL"


def _status_is_verified(status: str | None) -> bool:
    return status in {"VERIFIED", "VERIFIED_HISTORICAL"}


def analyze_context(
    trade: dict,
    underlying_bars: list[dict],
    spy_bars: list[dict],
    qqq_bars: list[dict],
    market_calendar: dict | None = None,
    underlying_feed: str | None = None,
    spy_feed: str | None = None,
    qqq_feed: str | None = None,
) -> dict:
    entry_dt = entry_datetime(trade)
    if entry_dt is None:
        return {
            "ruleset_version": LE_RULESET_VERSION,
            "available": False,
            "reason": "Exact entry timestamp is missing.",
            "auto_tags": [],
            "evidence": {},
            "data_warnings": ["Entry timestamp unavailable; LE timing cannot be proven."],
        }

    trade_day = entry_dt.date()
    direction = market_direction(trade)
    dated = [(b, _bar_dt(b)) for b in underlying_bars]

    calendar_verified = bool((market_calendar or {}).get("verified"))
    current_session = (market_calendar or {}).get("current")
    previous_session = (market_calendar or {}).get("previous")

    if previous_session:
        previous_day = previous_session["date"]
        previous_open = previous_session["open"]
        previous_close = previous_session["close"]
    else:
        prior_rth_dates = sorted(
            {
                dt.date()
                for _, dt in dated
                if dt.date() < trade_day and _is_rth(dt)
            }
        )
        previous_day = prior_rth_dates[-1] if prior_rth_dates else None
        previous_open = (
            datetime.combine(previous_day, time(9, 30), tzinfo=ET)
            if previous_day else None
        )
        previous_close = (
            datetime.combine(previous_day, time(16, 0), tzinfo=ET)
            if previous_day else None
        )

    current_open = (
        current_session["open"]
        if current_session
        else datetime.combine(trade_day, time(9, 30), tzinfo=ET)
    )
    current_close = (
        current_session["close"]
        if current_session
        else datetime.combine(trade_day, time(16, 0), tzinfo=ET)
    )

    previous_rth = [
        b for b, dt in dated
        if previous_open is not None and previous_close is not None
        and _within(dt, previous_open, previous_close)
    ]
    premarket_start = datetime.combine(trade_day, time(4, 0), tzinfo=ET)
    premarket_end = datetime.combine(trade_day, time(9, 30), tzinfo=ET)
    premarket = [
        b for b, dt in dated
        if _within(dt, premarket_start, premarket_end)
    ]
    current_to_entry = [
        b for b, dt in dated
        if (
            current_open <= dt < min(current_close, entry_dt)
            and dt + timedelta(minutes=1) < entry_dt
        )
    ]

    pdh = max((float(b["h"]) for b in previous_rth), default=None)
    pdl = min((float(b["l"]) for b in previous_rth), default=None)
    pmh = max((float(b["h"]) for b in premarket), default=None)
    pml = min((float(b["l"]) for b in premarket), default=None)

    pd_status = _level_status(underlying_feed, calendar_verified, previous_close)
    pm_status = _level_status(underlying_feed, calendar_verified, premarket_end)
    level_meta = {
        "PDH": {
            "status": pd_status,
            "feed": underlying_feed,
            "session_date": previous_day.isoformat() if previous_day else None,
            "session_start_et": previous_open.isoformat() if previous_open else None,
            "session_end_et": previous_close.isoformat() if previous_close else None,
        },
        "PDL": {
            "status": pd_status,
            "feed": underlying_feed,
            "session_date": previous_day.isoformat() if previous_day else None,
            "session_start_et": previous_open.isoformat() if previous_open else None,
            "session_end_et": previous_close.isoformat() if previous_close else None,
        },
        "PMH": {
            "status": pm_status,
            "feed": underlying_feed,
            "session_date": trade_day.isoformat(),
            "session_start_et": premarket_start.isoformat(),
            "session_end_et": premarket_end.isoformat(),
        },
        "PML": {
            "status": pm_status,
            "feed": underlying_feed,
            "session_date": trade_day.isoformat(),
            "session_start_et": premarket_start.isoformat(),
            "session_end_et": premarket_end.isoformat(),
        },
    }
    verified_level = {name: _status_is_verified(meta["status"]) for name, meta in level_meta.items()}

    bars_10m = _aggregate_10m(underlying_bars)
    completed_before_entry = [b for b in bars_10m if b["end"] < entry_dt]
    ema8 = _ema([b["c"] for b in completed_before_entry])
    entry_bar = _last_completed_1m_bar(current_to_entry, entry_dt)
    underlying_price = float(entry_bar["c"]) if entry_bar else None
    ema_distance_pct = (
        abs(underlying_price - ema8) / ema8 * 100
        if underlying_price is not None and ema8 not in (None, 0)
        else None
    )

    break_times = {
        "PDH": _first_completed_break(bars_10m, trade_day, entry_dt, pdh, "up") if verified_level["PDH"] else None,
        "PDL": _first_completed_break(bars_10m, trade_day, entry_dt, pdl, "down") if verified_level["PDL"] else None,
        "PMH": _first_completed_break(bars_10m, trade_day, entry_dt, pmh, "up") if verified_level["PMH"] else None,
        "PML": _first_completed_break(bars_10m, trade_day, entry_dt, pml, "down") if verified_level["PML"] else None,
    }
    breaks = {key: value is not None for key, value in break_times.items()}

    if direction == "bullish":
        outside_day = breaks["PDH"] and breaks["PMH"]
        inside_day = breaks["PMH"] and not breaks["PDH"]
        directional_breaks = [name for name in ("PDH", "PMH") if breaks[name]]
    else:
        outside_day = breaks["PDL"] and breaks["PML"]
        inside_day = breaks["PML"] and not breaks["PDL"]
        directional_breaks = [name for name in ("PDL", "PML") if breaks[name]]

    inside_premarket_range = (
        underlying_price is not None
        and verified_level["PML"]
        and verified_level["PMH"]
        and pml is not None
        and pmh is not None
        and pml <= underlying_price <= pmh
    )

    nearest_broken_level = None
    if underlying_price is not None and directional_breaks:
        candidates = []
        values = {"PDH": pdh, "PDL": pdl, "PMH": pmh, "PML": pml}
        for name in directional_breaks:
            value = values[name]
            if value not in (None, 0):
                candidates.append((abs(underlying_price - value) / value * 100, name, value))
        if candidates:
            distance_pct, name, value = min(candidates)
            nearest_broken_level = {
                "name": name,
                "price": value,
                "distance_pct": distance_pct,
            }

    auto_tags: list[dict] = []

    def add_rule_tag(tag_type: str, value: str, reason: str):
        if (tag_type, value) not in _RULE_TAGS:
            return
        auto_tags.append({
            "tag_type": tag_type,
            "tag_value": value,
            "confidence": 100,
            "classification": "proven",
            "source": "rule",
            "reason": reason,
        })

    if outside_day:
        add_rule_tag(
            "setup", "Outside Day",
            "Both required directional reference levels had completed 10-minute closes beyond them before entry.",
        )
    elif inside_day:
        add_rule_tag(
            "setup", "Inside Day",
            "The premarket directional level broke before entry while the corresponding previous-day level had not.",
        )

    for name in ("PDH", "PDL", "PMH", "PML"):
        if breaks[name]:
            add_rule_tag(
                "setup", f"{name} Break",
                f"A completed 10-minute candle closed beyond {name} before entry.",
            )

    if _session_window(entry_dt) == "scan_only":
        add_rule_tag(
            "mistake", "Entered First 10m",
            "Entry occurred during the 9:30–9:40 ET scan-only window.",
        )

    ema_status = _level_status(
        underlying_feed,
        True,
        entry_dt - timedelta(minutes=1),
    )
    if _status_is_verified(ema_status) and ema_distance_pct is not None and ema_distance_pct > 1.0:
        add_rule_tag(
            "mistake", "Airgapped from 8 EMA",
            f"Underlying was {ema_distance_pct:.2f}% from the last completed 10-minute 8 EMA at entry.",
        )

    directional_level_names = ("PDH", "PMH") if direction == "bullish" else ("PDL", "PML")
    can_evaluate_directional_levels = any(verified_level[name] for name in directional_level_names)
    if can_evaluate_directional_levels and not directional_breaks:
        add_rule_tag(
            "mistake", "No Level Break",
            "No verified directional PDH/PMH or PDL/PML completed 10-minute close was confirmed before entry.",
        )

    if inside_premarket_range:
        add_rule_tag(
            "mistake", "Traded Chop",
            "Underlying was inside the PMH–PML range at entry.",
        )

    pnl = trade.get("net_pnl")
    if pnl is not None and abs(float(pnl)) < 0.01:
        add_rule_tag("outcome", "Break-Even", "Realized net P&L was approximately flat.")

    data_warnings = []
    if not calendar_verified:
        data_warnings.append(
            "Official market-calendar verification was unavailable; level-dependent auto-tags are disabled unless provenance is verified."
        )
    if previous_day is None or pdh is None or pdl is None:
        data_warnings.append("Previous regular-session high/low could not be established.")
    elif not (verified_level["PDH"] and verified_level["PDL"]):
        data_warnings.append(
            f"PDH/PDL are {pd_status}; they are not eligible for automatic LE level classification."
        )
    if pmh is None or pml is None:
        data_warnings.append("Premarket high/low could not be established.")
    elif not (verified_level["PMH"] and verified_level["PML"]):
        data_warnings.append(
            f"PMH/PML are {pm_status}; they are not eligible for automatic LE level classification."
        )
    if underlying_price is None:
        data_warnings.append("No underlying 1-minute bar was available at or before the entry time.")
    if ema8 is None:
        data_warnings.append("10-minute 8 EMA could not be calculated.")
    elif not _status_is_verified(ema_status):
        data_warnings.append(
            f"10-minute 8 EMA is {ema_status}; EMA-dependent automatic mistake tags are disabled."
        )

    spy = _session_vwap_snapshot(spy_bars, entry_dt)
    qqq = _session_vwap_snapshot(qqq_bars, entry_dt)
    observed_market_sign = _market_sign_status(direction, spy, qqq)
    spy_status = _level_status(spy_feed, True, entry_dt - timedelta(minutes=1))
    qqq_status = _level_status(qqq_feed, True, entry_dt - timedelta(minutes=1))
    market_sign_verified = (
        _status_is_verified(spy_status)
        and _status_is_verified(qqq_status)
        and spy["vwap"] is not None
        and qqq["vwap"] is not None
    )
    market_sign = observed_market_sign if market_sign_verified else "unverified"

    if spy["vwap"] is None or qqq["vwap"] is None:
        data_warnings.append("SPY/QQQ VWAP market-confirmation evidence is incomplete.")
    elif not market_sign_verified:
        data_warnings.append(
            f"SPY/QQQ Market Sign is unverified (SPY {spy_status}, QQQ {qqq_status}); "
            "it cannot create an automatic No Market Sign tag."
        )

    if market_sign_verified and market_sign == "failed":
        add_rule_tag(
            "mistake", "No Market Sign",
            "Both verified SPY and QQQ VWAP signals opposed the trade direction before entry.",
        )

    evidence = {
        "underlying": trade.get("ticker"),
        "direction": direction,
        "entry_time_et": entry_dt.isoformat(),
        "execution_time_zone": EXECUTION_TIMEZONE_NAME,
        "session_window": _session_window(entry_dt),
        "previous_rth_date": previous_day.isoformat() if previous_day else None,
        "levels": {"PDH": pdh, "PDL": pdl, "PMH": pmh, "PML": pml},
        "level_meta": level_meta,
        "market_calendar_verified": calendar_verified,
        "current_session": {
            "date": current_session["date"].isoformat() if current_session else trade_day.isoformat(),
            "open_et": current_open.isoformat(),
            "close_et": current_close.isoformat(),
        },
        "level_breaks_before_entry": breaks,
        "level_break_times_et": {
            key: value.isoformat() if value else None for key, value in break_times.items()
        },
        "outside_day": outside_day,
        "inside_day": inside_day,
        "inside_premarket_range_at_entry": inside_premarket_range,
        "underlying_price_last_completed_1m": underlying_price,
        "ema8_10m_last_completed": ema8,
        "ema_distance_pct": ema_distance_pct,
        "ema_integrity_status": ema_status,
        "nearest_broken_level": nearest_broken_level,
        "spy": spy,
        "qqq": qqq,
        "market_sign": {
            "basis": "SPY/QQQ regular-session HLC3 VWAP",
            "status": market_sign,
            "observed_status": observed_market_sign,
            "integrity_status": "VERIFIED" if market_sign_verified else "UNVERIFIED",
            "spy_integrity_status": spy_status,
            "qqq_integrity_status": qqq_status,
            "rule": (
                "Bullish: both SPY and QQQ above VWAP = confirmed; both below = failed; otherwise mixed. "
                "Bearish: both below VWAP = confirmed; both above = failed; otherwise mixed."
            ),
        },
        "net_pnl": trade.get("net_pnl"),
        "instrument_type": trade.get("instrument_type"),
        "option_type": trade.get("option_type"),
    }

    return {
        "ruleset_version": LE_RULESET_VERSION,
        "available": True,
        "auto_tags": auto_tags,
        "evidence": evidence,
        "data_warnings": data_warnings,
    }


async def _fetch_market_calendar(entry_dt: datetime) -> dict:
    """Use Alpaca's official trading calendar for prior session and early-close boundaries."""
    key = (os.getenv("APCA_API_KEY_ID") or "").strip()
    secret = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
    if not key or not secret or key == "your_alpaca_api_key_here":
        return {
            "verified": False,
            "current": None,
            "previous": None,
            "source": None,
            "error": "Alpaca calendar credentials unavailable.",
        }

    start_day = entry_dt.date() - timedelta(days=14)
    end_day = entry_dt.date()
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    params = {
        "start": start_day.isoformat(),
        "end": end_day.isoformat(),
    }
    bases = []
    for base in (
        ALPACA_TRADING_BASE_URL,
        "https://paper-api.alpaca.markets",
        "https://api.alpaca.markets",
    ):
        base = base.rstrip("/")
        if base not in bases:
            bases.append(base)

    errors = []
    for base in bases:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{base}/v2/calendar", params=params, headers=headers)
                response.raise_for_status()
                rows = response.json()
            if not isinstance(rows, list):
                errors.append(f"{base}: unexpected calendar response")
                continue
            context = _calendar_context(rows, entry_dt.date())
            context["source"] = base
            context["error"] = (
                None
                if context["verified"]
                else "Trade day or previous trading session missing from market calendar."
            )
            if context["verified"]:
                return context
            errors.append(f"{base}: incomplete calendar context")
        except Exception as exc:
            errors.append(f"{base}: {exc}")

    return {
        "verified": False,
        "current": None,
        "previous": None,
        "source": None,
        "error": "; ".join(errors[-3:]),
    }


def _historical_feed_order() -> list[str]:
    """Prefer consolidated historical data, then degrade explicitly."""
    order = ["sip", "delayed_sip", ALPACA_FALLBACK_FEED, "iex"]
    unique: list[str] = []
    for feed in order:
        feed = (feed or "").strip().lower()
        if feed and feed not in unique:
            unique.append(feed)
    return unique


def _history_window(entry_dt: datetime) -> tuple[datetime, datetime]:
    """Fetch only through the execution timestamp to avoid unnecessary recent-data restrictions."""
    start_day = entry_dt.date() - timedelta(days=8)
    start_dt = datetime.combine(start_day, time(4, 0), tzinfo=ET)
    return start_dt, entry_dt


async def _request_alpaca_bars(
    symbol: str,
    start_dt: datetime,
    end_dt: datetime,
    feed: str,
    key: str,
    secret: str,
) -> list[dict]:
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }
    rows: list[dict] = []
    page_token = None

    async with httpx.AsyncClient(timeout=25.0) as client:
        while True:
            params = {
                "timeframe": "1Min",
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "limit": 10000,
                "feed": feed,
                "adjustment": "raw",
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
            rows.extend(payload.get("bars", []))
            page_token = payload.get("next_page_token")
            if not page_token or len(rows) >= 30000:
                break

    return rows


async def _fetch_alpaca_1m(symbol: str, entry_dt: datetime) -> tuple[list[dict], str]:
    """Fetch SIP-first historical bars; fall back without guessing missing levels."""
    key = (os.getenv("APCA_API_KEY_ID") or "").strip()
    secret = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
    if not key or not secret or key == "your_alpaca_api_key_here":
        raise RuntimeError("Alpaca market data is not configured.")

    start_dt, end_dt = _history_window(entry_dt)
    errors: list[str] = []

    for feed in _historical_feed_order():
        try:
            rows = await _request_alpaca_bars(
                symbol=symbol,
                start_dt=start_dt,
                end_dt=end_dt,
                feed=feed,
                key=key,
                secret=secret,
            )
            if rows:
                return rows, feed
            errors.append(f"{feed}: no bars returned")
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {403, 422}:
                errors.append(f"{feed}: unavailable ({status})")
                continue
            raise

    detail = "; ".join(errors[-4:]) or "no feed returned data"
    raise RuntimeError(f"Alpaca historical bars unavailable for {symbol}: {detail}")


def _le_json_schema() -> dict:
    tag_values = sorted({value for values in AI_TAGS.values() for value in values})
    return {
        "name": "le_trade_review",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "string",
                            "enum": ["NONE", *ALLOWED_STRATEGIES],
                        },
                        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                        "reason": {"type": "string"},
                        "evidence_keys": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["value", "confidence", "reason", "evidence_keys"],
                    "additionalProperties": False,
                },
                "suggested_tags": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tag_type": {
                                "type": "string",
                                "enum": sorted(AI_TAGS),
                            },
                            "tag_value": {
                                "type": "string",
                                "enum": tag_values,
                            },
                            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                            "reason": {"type": "string"},
                            "evidence_keys": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "tag_type", "tag_value", "confidence", "reason", "evidence_keys"
                        ],
                        "additionalProperties": False,
                    },
                },
                "insufficient_evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["strategy", "suggested_tags", "insufficient_evidence"],
            "additionalProperties": False,
        },
    }


LE_AI_SYSTEM_PROMPT = """You classify one completed trade against the trader's LE playbook.

The supplied evidence is source-of-truth. Do not recalculate or contradict objective fields.
Use only the allowed strategy/tag names in the response schema. Never invent names.
Never infer emotion.

Strategy rules:
- LE L-Entry — Level Retest: a key PDH/PDL/PMH/PML level broke first, then entry is a retest of that broken level.
- LE E-Entry — 10m 8 EMA Retest: a key level already broke and the trend is established, then price returns snug to the 10-minute 8 EMA. A random EMA touch without a prior level break is not valid.
- LE Purple Profits — 8 EMA Pullback: a strong, already-developed trend makes a controlled continuation pullback to the 10-minute 8 EMA.
- If the evidence cannot distinguish these reliably, choose NONE.

Tag rules:
- The deterministic rule engine already handles Outside/Inside Day, level breaks, first-10m, airgapped >1%, no-level-break, chop-range, VWAP-based No Market Sign, and break-even. Do not repeat those.
- Treat PDH/PDL/PMH/PML as usable only when the corresponding level_meta status is VERIFIED or VERIFIED_HISTORICAL.
- Treat 10-minute 8 EMA evidence as usable only when ema_integrity_status is VERIFIED or VERIFIED_HISTORICAL.
- Treat Market Sign as usable only when market_sign.integrity_status is VERIFIED. If status is unverified, do not infer confirmation from observed_status.
- Market Sign is a custom trader rule supplied in objective evidence: bullish requires both SPY and QQQ above regular-session VWAP; bearish requires both below. Mixed is not a failed sign.
- Suggest A++ Level + EMA only when broken-level and EMA confluence is genuinely supported.
- Suggest Flag-Line-Sign only when Flag and Line are supported and objective market_sign.status is confirmed.
- Suggest Clean/Early/Late Entry, Chased Entry, Forced Setup, or Sold Too Early only when the evidence supports the claim.
- Missing evidence means omit the tag and add a concise item to insufficient_evidence.

Be conservative. A false positive is worse than returning NONE.
"""


async def _groq_classify(context: dict) -> dict:
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not api_key:
        return {
            "available": False,
            "provider": None,
            "model": None,
            "strategy": {
                "value": "NONE",
                "confidence": 0,
                "reason": "Groq is not configured.",
                "evidence_keys": [],
            },
            "suggested_tags": [],
            "insufficient_evidence": ["GROQ_API_KEY is not configured."],
        }

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": LE_AI_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Classify this LE evidence packet:\n" + json.dumps(context, indent=2),
            },
        ],
        "max_completion_tokens": 2200,
        "reasoning_effort": "medium",
        "temperature": 0.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": _le_json_schema(),
        },
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()

    try:
        raw = body["choices"][0]["message"]["content"]
        parsed = json.loads(raw)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Groq returned an unexpected LE classification response.") from exc

    valid_tags = []
    for tag in parsed.get("suggested_tags", []):
        tag_type = tag.get("tag_type")
        tag_value = tag.get("tag_value")
        if tag_value in AI_TAGS.get(tag_type, set()):
            tag = dict(tag)
            tag["classification"] = "suggested"
            tag["source"] = "ai"
            valid_tags.append(tag)

    strategy = parsed.get("strategy") or {}
    if strategy.get("value") not in {"NONE", *ALLOWED_STRATEGIES}:
        strategy = {
            "value": "NONE",
            "confidence": 0,
            "reason": "Model returned an unsupported strategy.",
            "evidence_keys": [],
        }

    return {
        "available": True,
        "provider": "groq",
        "model": GROQ_MODEL,
        "strategy": strategy,
        "suggested_tags": valid_tags,
        "insufficient_evidence": parsed.get("insufficient_evidence", []),
    }


async def build_le_levels(trade: dict) -> dict:
    """Return deterministic PDH/PDL/PMH/PML for chart overlays without invoking Groq."""
    when = entry_datetime(trade)
    if when is None:
        return {
            "ruleset_version": LE_RULESET_VERSION,
            "available": False,
            "reason": "Exact entry timestamp is missing.",
            "levels": {},
            "feed": None,
            "warnings": ["Entry timestamp unavailable; LE levels cannot be established."],
        }

    ticker = str(trade.get("ticker") or "").upper().strip()
    if not ticker:
        return {
            "ruleset_version": LE_RULESET_VERSION,
            "available": False,
            "reason": "Ticker is missing.",
            "levels": {},
            "feed": None,
            "warnings": ["Ticker unavailable."],
        }

    try:
        underlying_result, market_calendar = await asyncio.gather(
            _fetch_alpaca_1m(ticker, when),
            _fetch_market_calendar(when),
        )
        underlying_bars, feed = underlying_result
    except Exception as exc:
        return {
            "ruleset_version": LE_RULESET_VERSION,
            "available": False,
            "reason": "Market data could not be loaded.",
            "levels": {},
            "feed": None,
            "warnings": [str(exc)],
        }

    context = analyze_context(
        trade,
        underlying_bars,
        [],
        [],
        market_calendar=market_calendar,
        underlying_feed=feed,
    )
    ev = context.get("evidence") or {}
    levels = ev.get("levels") or {}
    level_meta = ev.get("level_meta") or {}
    warnings: list[str] = []
    if levels.get("PDH") is None or levels.get("PDL") is None:
        warnings.append("Previous-day high/low could not be established.")
    if levels.get("PMH") is None or levels.get("PML") is None:
        warnings.append("Premarket high/low could not be established.")
    if feed == "iex":
        warnings.append(
            "Historical SIP was unavailable, so chart levels use IEX fallback and may differ from Schwab or TradingView."
        )
    elif feed == "delayed_sip":
        warnings.append(
            "Chart levels use delayed SIP; consolidated historical levels are valid, while newest bars may lag."
        )

    verified_levels = {
        name: value
        for name, value in levels.items()
        if value is not None and _status_is_verified((level_meta.get(name) or {}).get("status"))
    }
    if not market_calendar.get("verified"):
        warnings.append(
            "Official market calendar could not be verified; unverified reference levels are withheld from the chart."
        )

    return {
        "ruleset_version": LE_RULESET_VERSION,
        "available": bool(verified_levels),
        "levels": verified_levels,
        "level_meta": level_meta,
        "feed": feed,
        "calendar_verified": bool(market_calendar.get("verified")),
        "warnings": warnings,
    }


async def build_le_review(trade: dict) -> dict:
    """Build a read-only LE review. No strategy or tags are persisted automatically."""
    when = entry_datetime(trade)
    if when is None:
        context = analyze_context(trade, [], [], [])
        return {**context, "ai": await _groq_classify(context)}

    ticker = str(trade.get("ticker") or "").upper().strip()
    if not ticker:
        return {
            "ruleset_version": LE_RULESET_VERSION,
            "available": False,
            "reason": "Ticker is missing.",
            "auto_tags": [],
            "evidence": {},
            "data_warnings": ["Ticker unavailable."],
            "ai": None,
        }

    try:
        underlying_result, spy_result, qqq_result, market_calendar = await asyncio.gather(
            _fetch_alpaca_1m(ticker, when),
            _fetch_alpaca_1m("SPY", when),
            _fetch_alpaca_1m("QQQ", when),
            _fetch_market_calendar(when),
        )
        underlying_bars, underlying_feed = underlying_result
        spy_bars, spy_feed = spy_result
        qqq_bars, qqq_feed = qqq_result
    except Exception as exc:
        return {
            "ruleset_version": LE_RULESET_VERSION,
            "available": False,
            "reason": "Market data could not be loaded.",
            "auto_tags": [],
            "evidence": {},
            "data_warnings": [str(exc)],
            "ai": None,
        }

    context = analyze_context(
        trade,
        underlying_bars,
        spy_bars,
        qqq_bars,
        market_calendar=market_calendar,
        underlying_feed=underlying_feed,
        spy_feed=spy_feed,
        qqq_feed=qqq_feed,
    )
    if context.get("available"):
        context["evidence"]["market_data_feed"] = {
            "underlying": underlying_feed,
            "SPY": spy_feed,
            "QQQ": qqq_feed,
        }
        feeds = {underlying_feed, spy_feed, qqq_feed}
        if "iex" in feeds:
            context["data_warnings"].append(
                "Historical SIP was unavailable for at least one symbol, so LE Review fell back to IEX. "
                "IEX is exchange-limited; PDH/PDL/PMH/PML, VWAP, and benchmark bars can differ from "
                "Schwab or TradingView. Refresh Evidence later to retry SIP."
            )
        if "delayed_sip" in feeds:
            context["data_warnings"].append(
                "At least one symbol is using delayed SIP. Premarket and older bars are consolidated, "
                "but the newest market context may lag by about 15 minutes. Refresh Evidence later for full SIP."
            )

        ev = context["evidence"]
        level_meta = ev.get("level_meta") or {}
        verified_flags = [
            _status_is_verified((level_meta.get(name) or {}).get("status"))
            for name in ("PDH", "PDL", "PMH", "PML")
        ]
        verified_flags.extend([
            _status_is_verified(ev.get("ema_integrity_status")),
            (ev.get("market_sign") or {}).get("integrity_status") == "VERIFIED",
            ev.get("underlying_price_last_completed_1m") is not None,
        ])
        verified_count = sum(bool(flag) for flag in verified_flags)
        completeness_pct = round(verified_count / len(verified_flags) * 100)

        if completeness_pct == 100 and feeds == {"sip"} and ev.get("market_calendar_verified"):
            quality = "High"
            quality_reason = (
                "All core LE evidence is verified from consolidated SIP data with official session boundaries."
            )
        elif completeness_pct >= 75:
            quality = "Moderate"
            quality_reason = (
                "Most core LE evidence is verified, but one or more inputs are limited, delayed, or unavailable."
            )
        else:
            quality = "Low"
            quality_reason = (
                "Material LE evidence is not verified; automatic conclusions are intentionally restricted."
            )
        ev["evidence_quality"] = {
            "level": quality,
            "completeness_pct": completeness_pct,
            "reason": quality_reason,
        }

    ai = await _groq_classify(context) if context.get("available") else None
    return {**context, "ai": ai}
