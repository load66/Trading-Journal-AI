from le_compliance import build_le_compliance
from le_manual_evidence import is_manual_le_tag


def _trade(side="LONG", net_pnl=100.0):
    return {
        "trade_group": "g1",
        "ticker": "SPY",
        "date": "2026-09-25",
        "side": side,
        "instrument_type": "OPTION",
        "net_pnl": net_pnl,
        "executions": [
            {"action": "BOT" if side == "LONG" else "SOLD", "qty": 1, "price": 2.0},
            {"action": "SOLD" if side == "LONG" else "BOT", "qty": 1, "price": 2.5},
        ],
    }


def _review():
    return {
        "ruleset_version": "test",
        "evidence": {
            "entry_checks": {
                "level_break": {"status": "fail", "detail": "No directional break"},
                "ema_alignment": {"status": "pass", "detail": "aligned"},
                "ema_extension": {"status": "pass", "detail": "snug"},
                "ema_beyond_broken_level": {"status": "unknown", "detail": "unknown"},
                "market_sign": {"status": "pass", "detail": "SPY/QQQ confirmed"},
                "chop_range": {"status": "pass", "detail": "outside"},
            },
            "bars_since_level_break": 0,
            "session_window": "prime",
            "management_10m8ema": {
                "exit_relation_to_ema_break": "after_confirmed_break",
            },
        },
    }


def _by_id(result):
    return {item["id"]: item for item in result["checks"]}


def _extra_by_id(result):
    return {item["id"]: item for item in result["extra_findings"]}


def test_manual_no_market_sign_overrides_automated_pass_and_preserves_conflict():
    result = build_le_compliance(
        _trade(),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {
                "id": 11,
                "trade_group": "g1",
                "tag_type": "mistake",
                "tag_value": "No Market Sign",
                "source": "manual",
            }
        ],
    )

    check = _by_id(result)["market_sign"]
    assert check["status"] == "fail"
    assert check["manual_override"] is True
    assert check["authoritative_source"] == "USER_MANUAL"
    assert check["system_result"]["status"] == "pass"
    assert check["conflict_with_system"] is True
    assert result["manual_le_evidence"]["override_count"] == 1
    assert result["manual_le_evidence"]["conflict_count"] == 1
    assert result["classification"] == "LE_VIOLATION"


def test_manual_level_tags_combine_into_authoritative_outside_day_evidence():
    result = build_le_compliance(
        _trade(),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {"id": 21, "tag_type": "setup", "tag_value": "PDH Break", "source": "manual"},
            {"id": 22, "tag_type": "setup", "tag_value": "PMH Break", "source": "manual"},
        ],
    )

    check = _by_id(result)["level_broken"]
    assert check["status"] == "pass"
    assert check["detail"] == "PDH, PMH"
    assert check["system_result"]["status"] == "fail"
    assert check["conflict_with_system"] is True
    assert result["manual_le_evidence"]["override_count"] == 1


def test_manual_outside_day_uses_trade_direction_when_specific_levels_are_not_tagged():
    long_result = build_le_compliance(
        _trade("LONG"),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {"id": 31, "tag_type": "setup", "tag_value": "Outside Day", "source": "manual"},
        ],
    )
    short_result = build_le_compliance(
        _trade("SHORT"),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {"id": 32, "tag_type": "setup", "tag_value": "Outside Day", "source": "manual"},
        ],
    )

    assert _by_id(long_result)["level_broken"]["detail"] == "PDH, PMH"
    assert _by_id(short_result)["level_broken"]["detail"] == "PDL, PML"
    assert "Outside Day" in long_result["manual_le_evidence"]["setup_tags"]


def test_manual_early_exit_tag_overrides_runner_management_finding():
    result = build_le_compliance(
        _trade(),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {
                "id": 41,
                "tag_type": "mistake",
                "tag_value": "Sell All too Early and didn’t wait until 8 Ema Break",
                "source": "manual",
            },
        ],
    )

    finding = _extra_by_id(result)["hold_the_line"]
    assert finding["status"] == "fail"
    assert finding["manual_override"] is True
    assert finding["system_result"]["status"] == "pass"
    assert finding["conflict_with_system"] is True
    assert "hold_the_line" in result["failed_rule_ids"]


def test_unrelated_manual_tag_never_changes_compliance():
    result = build_le_compliance(
        _trade(),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {
                "id": 51,
                "tag_type": "emotion",
                "tag_value": "Calm / Neutral",
                "source": "manual",
            }
        ],
    )

    assert result["manual_le_evidence"]["override_count"] == 0
    assert result["manual_le_evidence"]["recognized_tags"] == []
    assert _by_id(result)["market_sign"]["status"] == "pass"


def test_non_manual_tag_never_gets_user_authority():
    result = build_le_compliance(
        _trade(),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {
                "id": 61,
                "tag_type": "mistake",
                "tag_value": "No Market Sign",
                "source": "ai",
            }
        ],
    )

    assert result["manual_le_evidence"]["override_count"] == 0
    assert _by_id(result)["market_sign"]["status"] == "pass"
    assert is_manual_le_tag("mistake", "No Market Sign", "manual") is True
    assert is_manual_le_tag("mistake", "No Market Sign", "ai") is False


def test_removing_manual_tag_restores_system_result_on_rebuild():
    tagged = build_le_compliance(
        _trade(),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[
            {"id": 71, "tag_type": "mistake", "tag_value": "No Market Sign", "source": "manual"}
        ],
    )
    rebuilt_without_tag = build_le_compliance(
        _trade(),
        _review(),
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        manual_tags=[],
    )

    assert _by_id(tagged)["market_sign"]["status"] == "fail"
    assert _by_id(rebuilt_without_tag)["market_sign"]["status"] == "pass"
    assert rebuilt_without_tag["manual_le_evidence"]["override_count"] == 0
