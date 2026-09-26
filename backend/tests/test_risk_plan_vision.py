import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from risk_plan_vision import validate_extraction


def extraction(**overrides):
    base = {
        "image_kind": "tradingview_position_tool",
        "direction": "LONG",
        "entry_price": 742.20,
        "stop_price": None,
        "target_price": None,
        "risk_distance": 1.06,
        "reward_distance": 3.47,
        "risk_reward_ratio": None,
        "cash_risk": None,
        "cash_risk_currency": "UNKNOWN",
        "symbol": None,
        "timeframe": None,
        "confidence": 0.92,
        "evidence": ["Green target box above red stop box."],
        "warnings": [],
    }
    base.update(overrides)
    return base


def test_distance_only_position_tool_derives_levels_and_rr():
    result = validate_extraction(extraction())

    assert result["stop_price"] == 741.14
    assert result["target_price"] == 745.67
    assert result["risk_distance"] == 1.06
    assert result["reward_distance"] == 3.47
    assert result["calculated_risk_reward"] == 3.2736
    assert result["math_verified"] is True
    assert result["safe_to_apply"] is True


def test_price_geometry_overrides_conflicting_model_math():
    result = validate_extraction(extraction(
        entry_price=100,
        stop_price=98,
        target_price=106,
        risk_distance=1,
        reward_distance=9,
        risk_reward_ratio=9,
    ))

    assert result["risk_distance"] == 2
    assert result["reward_distance"] == 6
    assert result["calculated_risk_reward"] == 3
    assert any("conflicts" in w.lower() for w in result["warnings"])


def test_invalid_direction_geometry_is_not_safe_to_apply():
    result = validate_extraction(extraction(
        direction="LONG",
        entry_price=100,
        stop_price=102,
        target_price=110,
        risk_distance=None,
        reward_distance=None,
    ))

    assert result["geometry_valid"] is False
    assert result["safe_to_apply"] is False


def test_price_distance_never_becomes_cash_risk():
    result = validate_extraction(extraction(
        risk_distance=1.06,
        reward_distance=3.47,
        cash_risk=None,
        cash_risk_currency="UNKNOWN",
    ))
    assert result["cash_risk"] is None


def fresh_main(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("DATABASE_MODE", "sqlite")
    for name in ("auth", "database", "main"):
        sys.modules.pop(name, None)
    import main
    main.init_db()
    return main


def test_long_put_expects_short_underlying_direction(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    assert main._expected_underlying_direction({
        "instrument_type": "OPTION",
        "side": "LONG",
        "option_type": "PUT",
    }) == "SHORT"
    assert main._expected_underlying_direction({
        "instrument_type": "OPTION",
        "side": "LONG",
        "option_type": "CALL",
    }) == "LONG"


def test_option_underlying_box_does_not_invent_cash_risk(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    plan = validate_extraction(extraction(
        direction="SHORT",
        entry_price=742.20,
        stop_price=743.26,
        target_price=738.73,
        risk_distance=1.06,
        reward_distance=3.47,
    ))
    trade = {
        "ticker": "SPY",
        "instrument_type": "OPTION",
        "side": "LONG",
        "option_type": "PUT",
        "net_pnl": -100,
        "executions": [
            {"action": "BOT", "qty": 2, "price": 2.50},
            {"action": "SOLD", "qty": 2, "price": 2.00},
        ],
    }

    preview = main._risk_plan_preview(trade, plan)
    assert preview["direction_match"] is True
    assert preview["cash_risk"] is None
    assert preview["realized_r"] is None
    assert preview["evidence_badges"]["cash_risk"] == "INSUFFICIENT DATA"


def test_stock_plan_derives_cash_risk_from_stop_and_actual_shares(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    plan = validate_extraction(extraction(
        direction="LONG",
        entry_price=100,
        stop_price=98,
        target_price=106,
        risk_distance=2,
        reward_distance=6,
    ))
    trade = {
        "ticker": "AMD",
        "instrument_type": "STOCK",
        "side": "LONG",
        "net_pnl": 300,
        "executions": [
            {"action": "BOT", "qty": 50, "price": 100},
            {"action": "SOLD", "qty": 50, "price": 106},
        ],
    }

    preview = main._risk_plan_preview(trade, plan)
    assert preview["cash_risk"] == 100
    assert preview["realized_r"] == 3
    assert preview["planned_risk_reward"] == 3
    assert preview["evidence_badges"]["cash_risk"] == "VERIFIED"
