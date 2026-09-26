from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx


LE_RULESET_VERSION = "LE_2026_09_v1"
ET = ZoneInfo("America/New_York")
EXECUTION_TIMEZONE_NAME = os.getenv("TRADE_EXECUTION_TIMEZONE", "America/Chicago")
EXECUTION_TZ = ZoneInfo(EXECUTION_TIMEZONE_NAME)
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "iex")

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
        "No Market Sign",
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
    """Return only a fully closed one-minute bar to avoid intraminute lookahead."""
    eligible = [
        b for b in bars
        if _bar_dt(b) + timedelta(minutes=1) <= when
    ]
    return max(eligible, key=_bar_dt) if eligible else None


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
        if bar["start"].date() != trade_day or bar["end"] > entry_dt:
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


def _benchmark_snapshot(bars: list[dict], entry_dt: datetime) -> dict:
    bars_10m = _aggregate_10m(bars)
    completed = [b for b in bars_10m if b["end"] <= entry_dt]
    ema8 = _ema([b["c"] for b in completed])
    last_bar = _last_completed_1m_bar(bars, entry_dt)
    price = float(last_bar["c"]) if last_bar else None
    return {
        "price": price,
        "ema8_10m": ema8,
        "above_ema8": None if price is None or ema8 is None else price > ema8,
        "below_ema8": None if price is None or ema8 is None else price < ema8,
    }


def analyze_context(
    trade: dict,
    underlying_bars: list[dict],
    spy_bars: list[dict],
    qqq_bars: list[dict],
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
    prior_rth_dates = sorted(
        {
            dt.date()
            for _, dt in dated
            if dt.date() < trade_day and _is_rth(dt)
        }
    )
    previous_day = prior_rth_dates[-1] if prior_rth_dates else None

    previous_rth = [
        b for b, dt in dated
        if previous_day is not None and dt.date() == previous_day and _is_rth(dt)
    ]
    premarket = [
        b for b, dt in dated
        if dt.date() == trade_day and _is_premarket(dt)
    ]
    current_to_entry = [
        b for b, dt in dated
        if dt.date() == trade_day and time(9, 30) <= dt.time() < time(16, 0) and dt <= entry_dt
    ]

    pdh = max((float(b["h"]) for b in previous_rth), default=None)
    pdl = min((float(b["l"]) for b in previous_rth), default=None)
    pmh = max((float(b["h"]) for b in premarket), default=None)
    pml = min((float(b["l"]) for b in premarket), default=None)

    bars_10m = _aggregate_10m(underlying_bars)
    completed_before_entry = [b for b in bars_10m if b["end"] <= entry_dt]
    ema8 = _ema([b["c"] for b in completed_before_entry])
    entry_bar = _last_completed_1m_bar(current_to_entry, entry_dt)
    underlying_price = float(entry_bar["c"]) if entry_bar else None
    ema_distance_pct = (
        abs(underlying_price - ema8) / ema8 * 100
        if underlying_price is not None and ema8 not in (None, 0)
        else None
    )

    break_times = {
        "PDH": _first_completed_break(bars_10m, trade_day, entry_dt, pdh, "up"),
        "PDL": _first_completed_break(bars_10m, trade_day, entry_dt, pdl, "down"),
        "PMH": _first_completed_break(bars_10m, trade_day, entry_dt, pmh, "up"),
        "PML": _first_completed_break(bars_10m, trade_day, entry_dt, pml, "down"),
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

    if ema_distance_pct is not None and ema_distance_pct > 1.0:
        add_rule_tag(
            "mistake", "Airgapped from 8 EMA",
            f"Underlying was {ema_distance_pct:.2f}% from the last completed 10-minute 8 EMA at entry.",
        )

    if not directional_breaks:
        add_rule_tag(
            "mistake", "No Level Break",
            "No directional PDH/PMH or PDL/PML completed 10-minute close was confirmed before entry.",
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
    if previous_day is None or pdh is None or pdl is None:
        data_warnings.append("Previous regular-session high/low could not be established.")
    if pmh is None or pml is None:
        data_warnings.append("Premarket high/low could not be established.")
    if underlying_price is None:
        data_warnings.append("No underlying 1-minute bar was available at or before the entry time.")
    if ema8 is None:
        data_warnings.append("10-minute 8 EMA could not be calculated.")

    spy = _benchmark_snapshot(spy_bars, entry_dt)
    qqq = _benchmark_snapshot(qqq_bars, entry_dt)
    if spy["price"] is None or qqq["price"] is None:
        data_warnings.append("SPY/QQQ market-confirmation evidence is incomplete.")

    evidence = {
        "underlying": trade.get("ticker"),
        "direction": direction,
        "entry_time_et": entry_dt.isoformat(),
        "execution_time_zone": EXECUTION_TIMEZONE_NAME,
        "session_window": _session_window(entry_dt),
        "previous_rth_date": previous_day.isoformat() if previous_day else None,
        "levels": {"PDH": pdh, "PDL": pdl, "PMH": pmh, "PML": pml},
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
        "nearest_broken_level": nearest_broken_level,
        "spy": spy,
        "qqq": qqq,
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


async def _fetch_alpaca_1m(symbol: str, trade_day: date) -> tuple[list[dict], str]:
    key = (os.getenv("APCA_API_KEY_ID") or "").strip()
    secret = (os.getenv("APCA_API_SECRET_KEY") or "").strip()
    if not key or not secret or key == "your_alpaca_api_key_here":
        raise RuntimeError("Alpaca market data is not configured.")

    start_day = trade_day - timedelta(days=8)
    start_dt = datetime.combine(start_day, time(4, 0), tzinfo=ET)
    end_dt = datetime.combine(trade_day, time(16, 1), tzinfo=ET)
    url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }

    async def request(feed: str) -> list[dict]:
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

    try:
        return await request(ALPACA_DATA_FEED), ALPACA_DATA_FEED
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403 and ALPACA_DATA_FEED != "iex":
            return await request("iex"), "iex"
        raise


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
- The deterministic rule engine already handles Outside/Inside Day, level breaks, first-10m, airgapped >1%, no-level-break, chop-range and break-even. Do not repeat those.
- Suggest A++ Level + EMA only when broken-level and EMA confluence is genuinely supported.
- Suggest Flag-Line-Sign only when all three elements are supported; SPY/QQQ evidence alone is not enough.
- Suggest Clean/Early/Late Entry, Chased Entry, Forced Setup, No Market Sign, or Sold Too Early only when the evidence supports the claim.
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
        underlying_result, spy_result, qqq_result = await asyncio.gather(
            _fetch_alpaca_1m(ticker, when.date()),
            _fetch_alpaca_1m("SPY", when.date()),
            _fetch_alpaca_1m("QQQ", when.date()),
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

    context = analyze_context(trade, underlying_bars, spy_bars, qqq_bars)
    if context.get("available"):
        context["evidence"]["market_data_feed"] = {
            "underlying": underlying_feed,
            "SPY": spy_feed,
            "QQQ": qqq_feed,
        }
        if "iex" in {underlying_feed, spy_feed, qqq_feed}:
            context["data_warnings"].append(
                "Alpaca IEX is an exchange-limited feed, not consolidated SIP data. "
                "PDH/PDL/PMH/PML and benchmark bars can differ from Schwab or TradingView; "
                "review level-based tags before applying them."
            )
    ai = await _groq_classify(context) if context.get("available") else None
    return {**context, "ai": ai}
