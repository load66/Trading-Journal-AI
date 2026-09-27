"""The generic CSV template: parsing, grouping into trades, and refusing bad rows.

    cd backend && python -m pytest tests -q
"""
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from csv_parser import detect_broker, parse_broker_csv, parse_generic_rows  # noqa: E402

TEMPLATES = BACKEND.parent / "frontend" / "public" / "templates"
HEADER = "date,time,symbol,side,quantity,price,commission,asset_type,expiry,strike,put_call,multiplier,timezone\n"


def example():
    return (TEMPLATES / "generic_trades_example.csv").read_text(encoding="utf-8")


def by_ticker(trades):
    return {t["ticker"]: t for t in trades}


def test_blank_template_is_detected_but_has_no_rows():
    blank = (TEMPLATES / "generic_trades_template.csv").read_text(encoding="utf-8")
    assert detect_broker(blank) == "generic"
    with pytest.raises(ValueError, match="no trade rows"):
        parse_generic_rows(blank)


def test_example_is_auto_detected_and_grouped_into_round_trips():
    content = example()
    assert detect_broker(content) == "generic"
    trades, skipped = parse_broker_csv(content, "auto", account_id=1)
    assert skipped == 0
    t = by_ticker(trades)
    assert set(t) == {"AAPL", "TSLA", "SPY", "/MESU26"}

    # AAPL: bought 100 at 310.20, sold 50 at 312.05 and 50 at 312.90
    assert t["AAPL"]["side"] == "LONG"
    assert t["AAPL"]["gross_pnl"] == pytest.approx(50 * (312.05 - 310.20) + 50 * (312.90 - 310.20))

    # TSLA: a short, covered lower
    assert t["TSLA"]["side"] == "SHORT"
    assert t["TSLA"]["gross_pnl"] == pytest.approx(40 * (349.80 - 346.10))

    # SPY option: 2 contracts, 100 shares each, AM/PM times
    assert t["SPY"]["instrument_type"] == "OPTION"
    assert t["SPY"]["option_type"] == "CALL"
    assert t["SPY"]["gross_pnl"] == pytest.approx(2 * 100 * (4.15 - 3.40))

    # /MES: US dates, and the known $5 per point multiplier
    assert t["/MESU26"]["instrument_type"] == "FUTURE"
    assert t["/MESU26"]["gross_pnl"] == pytest.approx(5 * (6420.50 - 6412.25))


def test_net_subtracts_every_commission():
    trades, _ = parse_broker_csv(example(), "generic", account_id=1)
    aapl = by_ticker(trades)["AAPL"]
    assert aapl["commissions"] == pytest.approx(0.65 + 0.33 + 0.33)
    assert aapl["net_pnl"] == pytest.approx(aapl["gross_pnl"] - aapl["commissions"])


def test_aliases_and_extra_columns_are_accepted():
    content = (
        "Ticker,Action,Qty,Fill Price,Fees,Date,Time,Notes,Time Zone\n"
        "NVDA,Buy,10,208.00,0.10,2026-08-24,09:40,first,America/Chicago\n"
        "NVDA,Sell,10,209.50,0.10,2026-08-24,09:55,second,America/Chicago\n"
    )
    assert detect_broker(content) == "generic"
    trades, _ = parse_broker_csv(content, "auto", account_id=1)
    assert trades[0]["gross_pnl"] == pytest.approx(15.0)


@pytest.mark.parametrize("row, message", [
    ("24/08/2026,09:30,AAPL,BUY,10,300,,,,,,", "not YYYY-MM-DD or MM/DD/YYYY"),
    ("2026-08-24,9.30,AAPL,BUY,10,300,,,,,,", "is not HH:MM"),
    ("2026-08-24,09:30,AAPL,HOLD,10,300,,,,,,", "is not BUY or SELL"),
    ("2026-08-24,09:30,AAPL,BUY,0,300,,,,,,", "above zero"),
    ("2026-08-24,09:30,SPY,BUY,1,3.40,,OPTION,,765,,", "options need expiry"),
    ("2026-08-24,09:30,/ZZU26,BUY,1,100,,FUTURE,,,,", "no known point value"),
])
def test_bad_rows_stop_the_import_and_name_the_line(row, message):
    with pytest.raises(ValueError) as err:
        parse_generic_rows(HEADER + row + "\n")
    assert "line 2" in str(err.value)
    assert message in str(err.value)


def test_one_bad_row_imports_nothing():
    content = HEADER + (
        "2026-08-24,09:30,AAPL,BUY,10,300,,,,,,\n"
        "2026-08-24,09:45,AAPL,SELL,ten,301,,,,,,\n"
    )
    with pytest.raises(ValueError, match="nothing was imported"):
        parse_generic_rows(content)


def test_unknown_future_imports_with_a_multiplier_column():
    content = HEADER + (
        "2026-08-24,09:30,/ZZU26,BUY,1,100,,FUTURE,,,,20,America/Chicago\n"
        "2026-08-24,09:45,/ZZU26,SELL,1,101,,FUTURE,,,,20,America/Chicago\n"
    )
    trades, _ = parse_broker_csv(content, "generic", account_id=1)
    assert trades[0]["gross_pnl"] == pytest.approx(20.0)


def test_generic_timezone_column_is_dst_aware_and_auditable():
    content = HEADER + (
        "2026-09-25,08:47:04,AAPL,BUY,1,200,,STOCK,,,,,America/Chicago\n"
    )
    execution = parse_generic_rows(content)[0]

    assert execution["source_timestamp"] == "2026-09-25 08:47:04"
    assert execution["source_timezone"] == "America/Chicago"
    assert execution["timezone_detection_method"] == "row_timezone"
    assert execution["timezone_detection_confidence"] == "authoritative"
    assert execution["timestamp_utc"] == "2026-09-25T13:47:04Z"


def test_generic_combined_iso_timestamp_with_offset_needs_no_timezone_column():
    content = (
        "timestamp,symbol,side,quantity,price,commission,asset_type\n"
        "2026-09-25T09:47:04-04:00,AAPL,BUY,1,200,0,STOCK\n"
        "2026-09-25T09:50:04-04:00,AAPL,SELL,1,201,0,STOCK\n"
    )
    assert detect_broker(content) == "generic"
    trades, skipped = parse_broker_csv(content, "auto", account_id=1)
    assert skipped == 0
    execs = json.loads(trades[0]["executions"])
    assert execs[0]["source_timestamp"] == "2026-09-25T09:47:04-04:00"
    assert execs[0]["source_timezone"] == "UTC-04:00"
    assert execs[0]["timezone_detection_method"] == "embedded_timestamp_timezone"
    assert execs[0]["timezone_detection_confidence"] == "authoritative"
    assert execs[0]["timestamp_utc"] == "2026-09-25T13:47:04Z"


def test_generic_combined_z_timestamp_is_utc_authoritative():
    content = (
        "timestamp,symbol,side,quantity,price\n"
        "2026-09-25T13:47:04Z,AAPL,BUY,1,200\n"
        "2026-09-25T13:50:04Z,AAPL,SELL,1,201\n"
    )
    trades, _ = parse_broker_csv(content, "generic", account_id=1)
    execs = json.loads(trades[0]["executions"])
    assert execs[0]["source_timezone"] == "UTC"
    assert execs[0]["timestamp_utc"] == "2026-09-25T13:47:04Z"


def test_generic_row_timezone_conflict_with_embedded_offset_is_rejected():
    content = (
        "timestamp,symbol,side,quantity,price,timezone\n"
        "2026-09-25T09:47:04-04:00,AAPL,BUY,1,200,America/Chicago\n"
    )
    with pytest.raises(ValueError, match="conflicts with the row timezone"):
        parse_generic_rows(content)


def test_generic_local_time_can_use_manual_fallback():
    content = (
        "date,time,symbol,side,quantity,price\n"
        "2026-09-25,09:47:04,AAPL,BUY,1,200\n"
        "2026-09-25,09:50:04,AAPL,SELL,1,201\n"
    )
    trades, _ = parse_broker_csv(
        content,
        "generic",
        account_id=1,
        timezone_override="America/New_York",
    )
    execs = json.loads(trades[0]["executions"])
    assert execs[0]["source_timezone"] == "America/New_York"
    assert execs[0]["timezone_detection_method"] == "manual_fallback"
    assert execs[0]["timestamp_utc"] == "2026-09-25T13:47:04Z"


def test_generic_import_refuses_unverified_timezone():
    content = (
        "date,time,symbol,side,quantity,price\n"
        "2026-09-25,08:47:04,AAPL,BUY,1,200\n"
    )
    with pytest.raises(ValueError, match="Timezone could not be verified"):
        parse_broker_csv(content, "generic", account_id=1)


def test_existing_broker_detection_is_unchanged():
    scripts = BACKEND.parent / "scripts"
    assert detect_broker((scripts / "sample_import.csv").read_text(encoding="utf-8")) == "thinkorswim"
    assert detect_broker((scripts / "sample_import_ibkr.csv").read_text(encoding="utf-8")) == "ibkr"
