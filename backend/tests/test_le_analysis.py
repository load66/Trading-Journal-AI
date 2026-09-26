from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from le_analysis import analyze_context, market_direction, _le_json_schema


ET = ZoneInfo("America/New_York")


def bar(dt, o, h, l, c, v=1000):
    return {
        "t": dt.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z"),
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "v": v,
    }


def minute_run(day, start_h, start_m, minutes, price, high=None, low=None):
    start = datetime(day[0], day[1], day[2], start_h, start_m, tzinfo=ET)
    rows = []
    for i in range(minutes):
        p = price(i) if callable(price) else price
        h = high(i) if callable(high) else (high if high is not None else p + 0.05)
        l = low(i) if callable(low) else (low if low is not None else p - 0.05)
        rows.append(bar(start + timedelta(minutes=i), p, h, l, p))
    return rows


def base_trade(option_type="CALL", entry="09:47:04", pnl=100.0):
    return {
        "ticker": "TEST",
        "date": "2026-09-25",
        "instrument_type": "OPTION",
        "side": "LONG",
        "option_type": option_type,
        "net_pnl": pnl,
        "executions": [
            {
                "date": "2026-09-25",
                "time": entry,
                "action": "BOT",
                "qty": 1,
                "price": 1.0,
            },
            {
                "date": "2026-09-25",
                "time": "10:00:00",
                "action": "SOLD",
                "qty": 1,
                "price": 1.1,
            },
        ],
    }


def market_bars(*, pdh=100.0, pdl=95.0, pmh=101.0, pml=96.0, current=102.0):
    prev = minute_run(
        (2026, 9, 24), 9, 30, 90,
        lambda i: 98.0 + (i % 5) * 0.02,
        high=lambda i: pdh if i == 10 else 99.0,
        low=lambda i: pdl if i == 20 else 97.0,
    )
    pre = [
        bar(datetime(2026, 9, 25, 4, 0, tzinfo=ET), 98.0, pmh, pml, 98.5),
        bar(datetime(2026, 9, 25, 9, 29, tzinfo=ET), 98.5, 100.0, 97.0, 99.0),
    ]
    rth = minute_run((2026, 9, 25), 9, 30, 30, current)
    return prev + pre + rth


def tag_names(review):
    return {(t["tag_type"], t["tag_value"]) for t in review["auto_tags"]}


def test_option_direction_uses_contract_type_not_long_ownership():
    assert market_direction(base_trade("CALL")) == "bullish"
    assert market_direction(base_trade("PUT")) == "bearish"


def test_outside_day_requires_both_directional_levels_before_entry():
    bars = market_bars(pdh=100.0, pmh=101.0, current=102.0)
    review = analyze_context(base_trade("CALL"), bars, bars, bars)
    names = tag_names(review)

    assert review["evidence"]["outside_day"] is True
    assert review["evidence"]["inside_day"] is False
    assert ("setup", "Outside Day") in names
    assert ("setup", "PDH Break") in names
    assert ("setup", "PMH Break") in names
    assert ("mistake", "No Level Break") not in names


def test_inside_day_breaks_premarket_level_but_not_previous_day_level():
    bars = market_bars(pdh=105.0, pmh=101.0, current=102.0)
    review = analyze_context(base_trade("CALL"), bars, bars, bars)
    names = tag_names(review)

    assert review["evidence"]["outside_day"] is False
    assert review["evidence"]["inside_day"] is True
    assert ("setup", "Inside Day") in names
    assert ("setup", "PMH Break") in names
    assert ("setup", "PDH Break") not in names


def test_chop_and_no_level_break_are_proven_without_ai():
    bars = market_bars(pdh=105.0, pdl=95.0, pmh=101.0, pml=96.0, current=99.0)
    review = analyze_context(base_trade("CALL"), bars, bars, bars)
    names = tag_names(review)

    assert review["evidence"]["inside_premarket_range_at_entry"] is True
    assert ("mistake", "No Level Break") in names
    assert ("mistake", "Traded Chop") in names


def test_first_ten_minutes_is_a_deterministic_violation_tag():
    bars = market_bars(current=102.0)
    review = analyze_context(base_trade("CALL", entry="09:35:00"), bars, bars, bars)
    assert ("mistake", "Entered First 10m") in tag_names(review)


def test_strict_groq_schema_is_closed_and_versioned():
    schema = _le_json_schema()
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert "LE L-Entry — Level Retest" in schema["schema"]["properties"]["strategy"]["properties"]["value"]["enum"]
