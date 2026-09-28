from le_compliance import build_le_compliance, summarize_le_compliance_snapshots


def _review(**overrides):
    checks = {
        "level_break": {"status": "pass", "detail": "PDH"},
        "ema_alignment": {"status": "pass", "detail": "aligned"},
        "ema_extension": {"status": "pass", "detail": "0.25% from EMA"},
        "ema_beyond_broken_level": {"status": "pass", "detail": "EMA above PDH"},
        "market_sign": {"status": "pass", "detail": "SPY/QQQ confirmed"},
        "chop_range": {"status": "pass", "detail": "Outside PM range"},
    }
    checks.update(overrides.pop("entry_checks", {}))
    evidence = {
        "entry_checks": checks,
        "bars_since_level_break": 1,
        "session_window": "prime",
        "management_10m8ema": {
            "exit_relation_to_ema_break": "after_confirmed_break",
            "post_exit_favorable_move_pct_30m": 0.2,
        },
        "evidence_quality": {"level": "High", "completeness_pct": 100},
    }
    evidence.update(overrides)
    return {"ruleset_version": "test", "evidence": evidence}


def _trade(net_pnl=100.0):
    return {
        "trade_group": "g1",
        "ticker": "SPY",
        "date": "2026-09-25",
        "side": "LONG",
        "instrument_type": "OPTION",
        "net_pnl": net_pnl,
        "executions": [
            {"action": "BOT", "qty": 5, "price": 4.00},
            {"action": "SOLD", "qty": 5, "price": 4.20},
        ],
    }


def _by_id(result):
    return {item["id"]: item for item in result["checks"]}


def test_unknown_evidence_does_not_count_as_failure_or_inflate_score():
    result = build_le_compliance(
        _trade(),
        _review(),
        analysis={},
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        risk_plan=None,
    )
    checks = _by_id(result)

    assert checks["flag_forming"]["status"] == "unknown"
    assert checks["hard_stop_set"]["status"] == "unknown"
    assert checks["size_correct"]["status"] == "unknown"
    assert checks["risk_reward"]["status"] == "unknown"
    assert checks["vix_checked"]["status"] == "unknown"

    assert result["score"]["failed"] == 0
    assert result["score"]["unknown"] >= 5
    assert result["classification"] == "INCOMPLETE_EVIDENCE"
    assert result["score"]["coverage_pct"] < 100


def test_two_reds_make_third_trade_a_verified_violation():
    result = build_le_compliance(
        _trade(-100),
        _review(),
        analysis={},
        day_context={
            "sequence": 3,
            "prior_results": ["red", "red"],
            "day_trade_count": 3,
        },
        risk_plan=None,
    )
    check = _by_id(result)["trade_count_ok"]

    assert check["status"] == "fail"
    assert "Two red trades" in check["detail"]
    assert result["classification"] == "LE_VIOLATION"
    assert "trade_count_ok" in result["failed_rule_ids"]


def test_red_then_green_third_trade_is_a_verified_stop_rule_violation():
    result = build_le_compliance(
        _trade(),
        _review(),
        analysis={},
        day_context={
            "sequence": 3,
            "prior_results": ["red", "green"],
            "day_trade_count": 3,
        },
        risk_plan=None,
    )
    check = _by_id(result)["trade_count_ok"]

    assert check["status"] == "fail"
    assert "red then green" in check["detail"]


def test_green_then_red_third_trade_stays_unknown_without_a_plus_proof():
    result = build_le_compliance(
        _trade(),
        _review(),
        analysis={},
        day_context={
            "sequence": 3,
            "prior_results": ["green", "red"],
            "day_trade_count": 3,
        },
        risk_plan=None,
    )
    check = _by_id(result)["trade_count_ok"]

    assert check["status"] == "unknown"
    assert "A+ setup" in check["detail"]


def test_sizing_and_rr_are_verified_from_recorded_capital_exposure_and_risk():
    result = build_le_compliance(
        _trade(),
        _review(),
        analysis={
            "risk_per_trade": 450.0,
            "risk_reward": 2.5,
            "stop_loss": 148.50,
        },
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        risk_plan={"capital": 10000.0},
    )
    checks = _by_id(result)

    assert checks["size_correct"]["status"] == "pass"
    assert checks["size_correct"]["evidence"]["exposure_pct"] == 20.0
    assert checks["size_correct"]["evidence"]["risk_pct"] == 4.5
    assert checks["risk_reward"]["status"] == "pass"

    # A journaled stop value is not proof that a hard broker order existed before entry.
    assert checks["hard_stop_set"]["status"] == "unknown"


def test_sizing_fails_when_exposure_or_risk_exceeds_pdf_limits():
    result = build_le_compliance(
        _trade(),
        _review(),
        analysis={"risk_per_trade": 650.0, "risk_reward": 1.5},
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        risk_plan={"capital": 5000.0},
    )
    checks = _by_id(result)

    assert checks["size_correct"]["status"] == "fail"
    assert checks["risk_reward"]["status"] == "fail"
    assert result["classification"] == "LE_VIOLATION"


def test_chop_hour_is_a_deterministic_failure():
    result = build_le_compliance(
        _trade(),
        _review(session_window="chop_hour"),
        analysis={},
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        risk_plan=None,
    )

    assert _by_id(result)["not_chop_hour"]["status"] == "fail"
    assert "not_chop_hour" in result["failed_rule_ids"]


def test_summary_keeps_rule_pnl_association_descriptive():
    passing = build_le_compliance(
        _trade(200),
        _review(),
        analysis={},
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        risk_plan=None,
    )
    failing = build_le_compliance(
        {**_trade(-125), "trade_group": "g2"},
        _review(session_window="chop_hour"),
        analysis={},
        day_context={"sequence": 1, "prior_results": [], "day_trade_count": 1},
        risk_plan=None,
    )

    summary = summarize_le_compliance_snapshots([passing, failing])
    chop = next(row for row in summary["rule_stats"] if row["id"] == "not_chop_hour")

    assert summary["audited_trades"] == 2
    assert chop["pass"] == 1
    assert chop["fail"] == 1
    assert chop["fail_net_pnl"] == -125.0
    assert "does not prove" in summary["note"]
