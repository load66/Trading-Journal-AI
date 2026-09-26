import asyncio

import httpx

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from le_analysis import (
    _calendar_context,
    _fetch_alpaca_1m,
    _historical_feed_order,
    _history_window,
    _le_json_schema,
    _market_sign_status,
    _session_vwap_snapshot,
    analyze_context,
    build_le_levels,
    entry_datetime,
    market_direction,
)


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


def base_trade(option_type="CALL", entry="08:47:04", pnl=100.0):
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
                "time": "09:00:00",
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
        bar(datetime(2026, 9, 25, 4, 1, tzinfo=ET), 98.1, pmh, pml, 98.6),
        bar(datetime(2026, 9, 25, 9, 29, tzinfo=ET), 98.5, 100.0, 97.0, 99.0),
    ]
    rth = minute_run((2026, 9, 25), 9, 30, 30, current)
    return prev + pre + rth


def tag_names(review):
    return {(t["tag_type"], t["tag_value"]) for t in review["auto_tags"]}


def verified_calendar(previous_close=time(16, 0), current_close=time(16, 0)):
    return {
        "verified": True,
        "previous": {
            "date": datetime(2026, 9, 24, tzinfo=ET).date(),
            "open": datetime(2026, 9, 24, 9, 30, tzinfo=ET),
            "close": datetime.combine(datetime(2026, 9, 24).date(), previous_close, tzinfo=ET),
        },
        "current": {
            "date": datetime(2026, 9, 25, tzinfo=ET).date(),
            "open": datetime(2026, 9, 25, 9, 30, tzinfo=ET),
            "close": datetime.combine(datetime(2026, 9, 25).date(), current_close, tzinfo=ET),
        },
    }


def review_context(trade, underlying, spy=None, qqq=None, feed="sip", calendar=None):
    return analyze_context(
        trade,
        underlying,
        underlying if spy is None else spy,
        underlying if qqq is None else qqq,
        market_calendar=calendar or verified_calendar(),
        underlying_feed=feed,
        spy_feed=feed,
        qqq_feed=feed,
    )


def test_historical_feed_order_prefers_consolidated_data(monkeypatch):
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    # The module-level fallback remains IEX, but SIP must still be tried first.
    order = _historical_feed_order()
    assert order[0] == "sip"
    assert order[1] == "delayed_sip"
    assert order[-1] == "iex"


def test_history_window_ends_at_entry_not_market_close():
    entry = datetime(2026, 9, 25, 10, 47, 4, tzinfo=ET)
    start, end = _history_window(entry)
    assert end == entry
    assert end.hour == 10
    assert end.minute == 47
    assert start.date() == (entry.date() - timedelta(days=8))


def test_fetch_alpaca_uses_sip_first_and_stops_after_success(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    calls = []

    async def fake_request(symbol, start_dt, end_dt, feed, key, secret):
        calls.append((feed, end_dt))
        return [{"t": "2026-09-25T13:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]

    import le_analysis
    monkeypatch.setattr(le_analysis, "_request_alpaca_bars", fake_request)

    entry = datetime(2026, 9, 25, 10, 47, 4, tzinfo=ET)
    rows, feed = asyncio.run(_fetch_alpaca_1m("QCOM", entry))

    assert rows
    assert feed == "sip"
    assert calls == [("sip", entry)]


def test_fetch_alpaca_falls_back_when_recent_sip_is_restricted(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    calls = []

    async def fake_request(symbol, start_dt, end_dt, feed, key, secret):
        calls.append(feed)
        if feed == "sip":
            request = httpx.Request("GET", "https://data.alpaca.markets/test")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("restricted", request=request, response=response)
        if feed == "delayed_sip":
            return [{"t": "2026-09-25T13:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]
        return []

    import le_analysis
    monkeypatch.setattr(le_analysis, "_request_alpaca_bars", fake_request)

    entry = datetime(2026, 9, 25, 10, 47, 4, tzinfo=ET)
    rows, feed = asyncio.run(_fetch_alpaca_1m("QCOM", entry))

    assert rows
    assert feed == "delayed_sip"
    assert calls == ["sip", "delayed_sip"]


def test_build_le_levels_returns_same_reference_levels_without_ai(monkeypatch):
    async def fake_fetch(symbol, when):
        return market_bars(pdh=100.0, pdl=95.0, pmh=101.0, pml=96.0, current=102.0), "sip"

    async def fake_calendar(when):
        return verified_calendar()

    import le_analysis
    monkeypatch.setattr(le_analysis, "_fetch_alpaca_1m", fake_fetch)
    monkeypatch.setattr(le_analysis, "_fetch_market_calendar", fake_calendar)

    result = asyncio.run(build_le_levels(base_trade("CALL")))
    assert result["available"] is True
    assert result["feed"] == "sip"
    assert result["levels"] == {
        "PDH": 100.0,
        "PDL": 95.0,
        "PMH": 101.0,
        "PML": 96.0,
    }


def test_option_direction_uses_contract_type_not_long_ownership():
    assert market_direction(base_trade("CALL")) == "bullish"
    assert market_direction(base_trade("PUT")) == "bearish"


def test_execution_time_is_converted_from_central_to_eastern():
    dt = entry_datetime(base_trade("CALL", entry="08:47:04"))
    assert dt is not None
    assert dt.hour == 9
    assert dt.minute == 47
    assert dt.tzinfo == ET


def test_entry_snapshot_uses_only_completed_one_minute_bar():
    bars = market_bars(pdh=100.0, pmh=101.0, current=102.0)
    # 08:47:04 CT == 09:47:04 ET. Add a 09:47 ET bar with an impossible future close.
    bars.append(bar(datetime(2026, 9, 25, 9, 47, tzinfo=ET), 102.0, 150.0, 90.0, 149.0))
    review = review_context(base_trade("CALL", entry="08:47:04"), bars)
    assert review["evidence"]["underlying_price_last_completed_1m"] == 102.0


def test_exact_10m_boundary_is_not_used_as_pre_entry_confirmation():
    prev = minute_run(
        (2026, 9, 24), 9, 30, 90, 98.0,
        high=lambda i: 100.0 if i == 10 else 99.0,
        low=lambda i: 95.0 if i == 20 else 97.0,
    )
    pre = [
        bar(datetime(2026, 9, 25, 4, 0, tzinfo=ET), 98.0, 101.0, 96.0, 98.5),
        bar(datetime(2026, 9, 25, 9, 29, tzinfo=ET), 98.5, 100.0, 97.0, 99.0),
    ]
    # No break through 09:49. The 09:50–10:00 candle breaks both levels,
    # but an entry exactly at 10:00:00 ET must not use that candle.
    rth = minute_run(
        (2026, 9, 25), 9, 30, 30,
        lambda i: 99.0 if i < 20 else 102.0,
    )
    trade = base_trade("CALL", entry="09:00:00")  # CT -> 10:00:00 ET
    review = review_context(trade, prev + pre + rth, rth, rth)
    names = tag_names(review)

    assert review["evidence"]["level_breaks_before_entry"]["PDH"] is False
    assert review["evidence"]["level_breaks_before_entry"]["PMH"] is False
    assert ("setup", "Outside Day") not in names
    assert ("mistake", "No Level Break") in names


def test_vwap_snapshot_uses_only_completed_regular_session_bars():
    bars = minute_run(
        (2026, 9, 25), 9, 30, 20,
        lambda i: 100.0 + i * 0.1,
    )
    entry = datetime(2026, 9, 25, 9, 47, 4, tzinfo=ET)
    snap = _session_vwap_snapshot(bars, entry)

    assert snap["vwap"] is not None
    assert snap["price"] is not None
    assert snap["position_vs_vwap"] == "above"


def test_vwap_market_sign_confirmed_failed_and_mixed():
    rising = minute_run(
        (2026, 9, 25), 9, 30, 20,
        lambda i: 100.0 + i * 0.1,
    )
    falling = minute_run(
        (2026, 9, 25), 9, 30, 20,
        lambda i: 102.0 - i * 0.1,
    )
    entry = datetime(2026, 9, 25, 9, 47, 4, tzinfo=ET)
    above = _session_vwap_snapshot(rising, entry)
    below = _session_vwap_snapshot(falling, entry)

    assert _market_sign_status("bullish", above, above) == "confirmed"
    assert _market_sign_status("bullish", below, below) == "failed"
    assert _market_sign_status("bullish", above, below) == "mixed"
    assert _market_sign_status("bearish", below, below) == "confirmed"
    assert _market_sign_status("bearish", above, above) == "failed"


def test_failed_vwap_market_sign_adds_deterministic_mistake_tag():
    underlying = market_bars(pdh=100.0, pmh=101.0, current=102.0)
    falling = minute_run(
        (2026, 9, 25), 9, 30, 20,
        lambda i: 102.0 - i * 0.1,
    )
    review = review_context(base_trade("CALL"), underlying, falling, falling)

    assert review["evidence"]["market_sign"]["status"] == "failed"
    assert ("mistake", "No Market Sign") in tag_names(review)


def test_mixed_vwap_market_sign_does_not_add_no_market_sign_tag():
    underlying = market_bars(pdh=100.0, pmh=101.0, current=102.0)
    rising = minute_run(
        (2026, 9, 25), 9, 30, 20,
        lambda i: 100.0 + i * 0.1,
    )
    falling = minute_run(
        (2026, 9, 25), 9, 30, 20,
        lambda i: 102.0 - i * 0.1,
    )
    review = review_context(base_trade("CALL"), underlying, rising, falling)

    assert review["evidence"]["market_sign"]["status"] == "mixed"
    assert ("mistake", "No Market Sign") not in tag_names(review)


def test_outside_day_requires_both_directional_levels_before_entry():
    bars = market_bars(pdh=100.0, pmh=101.0, current=102.0)
    review = review_context(base_trade("CALL"), bars)
    names = tag_names(review)

    assert review["evidence"]["outside_day"] is True
    assert review["evidence"]["inside_day"] is False
    assert ("setup", "Outside Day") in names
    assert ("setup", "PDH Break") in names
    assert ("setup", "PMH Break") in names
    assert ("mistake", "No Level Break") not in names


def test_inside_day_breaks_premarket_level_but_not_previous_day_level():
    bars = market_bars(pdh=105.0, pmh=101.0, current=102.0)
    review = review_context(base_trade("CALL"), bars)
    names = tag_names(review)

    assert review["evidence"]["outside_day"] is False
    assert review["evidence"]["inside_day"] is True
    assert ("setup", "Inside Day") in names
    assert ("setup", "PMH Break") in names
    assert ("setup", "PDH Break") not in names


def test_chop_and_no_level_break_are_proven_without_ai():
    bars = market_bars(pdh=105.0, pdl=95.0, pmh=101.0, pml=96.0, current=99.0)
    review = review_context(base_trade("CALL"), bars)
    names = tag_names(review)

    assert review["evidence"]["inside_premarket_range_at_entry"] is True
    assert ("mistake", "No Level Break") in names
    assert ("mistake", "Traded Chop") in names


def test_isolated_premarket_low_requires_review_and_is_not_trusted_for_rules():
    prev = minute_run(
        (2026, 9, 24), 9, 30, 90, 98.0,
        high=lambda i: 100.0 if i in {10, 11} else 99.0,
        low=lambda i: 95.0 if i in {20, 21} else 97.0,
    )
    pre = [
        bar(datetime(2026, 9, 25, 4, 0, tzinfo=ET), 98.0, 101.0, 194.75, 98.5),
        bar(datetime(2026, 9, 25, 4, 1, tzinfo=ET), 98.1, 101.0, 194.85, 98.6),
    ]
    rth = minute_run((2026, 9, 25), 9, 30, 20, 202.0)

    review = review_context(base_trade("CALL"), prev + pre + rth)
    meta = review["evidence"]["level_meta"]["PML"]

    assert meta["status"] == "VERIFIED"
    assert meta["review_required"] is True
    assert meta["trusted_for_rules"] is False
    assert meta["source_bar_count"] == 1
    assert meta["next_distinct_extreme"] == 194.85
    assert round(meta["gap_to_next"], 2) == 0.10
    assert any("PML" in warning and "isolated" in warning for warning in review["data_warnings"])


def test_first_ten_minutes_is_a_deterministic_violation_tag():
    bars = market_bars(current=102.0)
    review = review_context(base_trade("CALL", entry="08:35:00"), bars)
    assert ("mistake", "Entered First 10m") in tag_names(review)


def test_calendar_context_honors_early_close():
    rows = [
        {"date": "2026-09-24", "open": "09:30", "close": "13:00"},
        {"date": "2026-09-25", "open": "09:30", "close": "16:00"},
    ]
    ctx = _calendar_context(rows, datetime(2026, 9, 25, tzinfo=ET).date())
    assert ctx["verified"] is True
    assert ctx["previous"]["close"].hour == 13


def test_early_close_excludes_post_close_spike_from_pdh():
    prev = minute_run(
        (2026, 9, 24), 9, 30, 210, 100.0,
        high=lambda i: 110.0 if i == 30 else 105.0,
        low=95.0,
    )
    prev.append(bar(datetime(2026, 9, 24, 15, 0, tzinfo=ET), 100, 200, 90, 150))
    pre = [
        bar(datetime(2026, 9, 25, 4, 0, tzinfo=ET), 100, 101, 96, 99),
        bar(datetime(2026, 9, 25, 9, 29, tzinfo=ET), 99, 100, 97, 99),
    ]
    rth = minute_run((2026, 9, 25), 9, 30, 20, 102.0)
    review = review_context(
        base_trade("CALL"),
        prev + pre + rth,
        calendar=verified_calendar(previous_close=time(13, 0)),
    )
    assert review["evidence"]["levels"]["PDH"] == 110.0
    assert review["evidence"]["level_meta"]["PDH"]["status"] == "VERIFIED"


def test_iex_levels_are_limited_and_cannot_drive_auto_tags():
    bars = market_bars(pdh=100.0, pmh=101.0, current=102.0)
    review = review_context(base_trade("CALL"), bars, feed="iex")
    names = tag_names(review)

    assert review["evidence"]["level_meta"]["PDH"]["status"] == "LIMITED"
    assert review["evidence"]["level_meta"]["PMH"]["status"] == "LIMITED"
    assert review["evidence"]["outside_day"] is False
    assert ("setup", "PDH Break") not in names
    assert ("setup", "PMH Break") not in names
    assert ("setup", "Outside Day") not in names


def test_missing_calendar_disables_level_dependent_auto_tags():
    bars = market_bars(pdh=100.0, pmh=101.0, current=102.0)
    review = analyze_context(
        base_trade("CALL"),
        bars, bars, bars,
        market_calendar={"verified": False, "current": None, "previous": None},
        underlying_feed="sip",
    )
    names = tag_names(review)
    assert ("setup", "Outside Day") not in names
    assert any("calendar" in warning.lower() for warning in review["data_warnings"])


def test_strict_groq_schema_is_closed_and_versioned():
    schema = _le_json_schema()
    assert schema["strict"] is True
    assert schema["schema"]["additionalProperties"] is False
    assert "LE L-Entry — Level Retest" in schema["schema"]["properties"]["strategy"]["properties"]["value"]["enum"]
