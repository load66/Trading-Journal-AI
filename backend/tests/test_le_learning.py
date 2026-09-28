from le_learning import build_le_learning_core


def _snapshot(
    i,
    *,
    setup="Outside Day",
    pnl=100,
    date=None,
    manual_rule=None,
    manual_status=None,
    system_status=None,
):
    checks = []
    recognized = []
    override_count = 0
    conflict_count = 0

    if manual_rule:
        check = {
            "id": manual_rule,
            "label": manual_rule.replace("_", " ").title(),
            "status": manual_status,
            "manual_override": True,
            "conflict_with_system": (
                system_status not in (None, "", "unknown")
                and system_status != manual_status
            ),
            "system_result": {
                "status": system_status or "unknown",
                "detail": "system",
                "evidence": {},
            },
        }
        checks.append(check)
        override_count = 1
        conflict_count = 1 if check["conflict_with_system"] else 0
        recognized.append({
            "id": i,
            "tag_type": "mistake" if manual_status == "fail" else "setup",
            "tag_value": manual_rule,
            "source": "manual",
        })

    return {
        "trade_group": f"g{i}",
        "date": date or f"2026-09-{(i % 28) + 1:02d}",
        "ticker": "SPY",
        "net_pnl": pnl,
        "checks": checks,
        "manual_le_evidence": {
            "recognized_tags": recognized + (
                [{
                    "id": 1000 + i,
                    "tag_type": "setup",
                    "tag_value": setup,
                    "source": "manual",
                }]
                if setup else []
            ),
            "setup_tags": [setup] if setup else [],
            "override_count": override_count,
            "conflict_count": conflict_count,
        },
    }


def test_small_winning_setup_stays_discovery():
    snapshots = [
        _snapshot(i, pnl=150, date=f"2026-09-{i+1:02d}")
        for i in range(5)
    ]

    result = build_le_learning_core(snapshots)
    edge = result["setup_edges"][0]

    assert edge["stage"] == "DISCOVERY"
    assert edge["promotion_ready"] is False
    assert edge["overall"]["net_pnl"] == 750.0


def test_developing_and_candidate_stages_require_more_history():
    developing = build_le_learning_core([
        _snapshot(i, pnl=50, date=f"2026-09-{(i % 15)+1:02d}")
        for i in range(12)
    ])["setup_edges"][0]
    candidate = build_le_learning_core([
        _snapshot(i, pnl=50, date=f"2026-09-{(i % 20)+1:02d}")
        for i in range(24)
    ])["setup_edges"][0]

    assert developing["stage"] == "DEVELOPING"
    assert candidate["stage"] == "CANDIDATE"


def test_stable_setup_can_be_validated_after_chronological_holdout():
    snapshots = []
    for i in range(30):
        pnl = 100 if i % 3 else -40
        snapshots.append(
            _snapshot(
                i,
                pnl=pnl,
                date=f"2026-09-{(i % 15)+1:02d}",
            )
        )

    result = build_le_learning_core(snapshots)
    edge = result["setup_edges"][0]

    assert edge["stage"] == "VALIDATED"
    assert edge["promotion_ready"] is True
    assert edge["overall"]["trades"] == 30
    assert edge["overall"]["trading_days"] == 15
    assert edge["early_sample"]["net_pnl"] > 0
    assert edge["recent_sample"]["net_pnl"] > 0


def test_large_setup_fails_validation_when_recent_sample_breaks_down():
    snapshots = []
    for i in range(30):
        pnl = 120 if i < 18 else -140
        snapshots.append(
            _snapshot(
                i,
                pnl=pnl,
                date=f"2026-09-{(i % 15)+1:02d}",
            )
        )

    result = build_le_learning_core(snapshots)
    edge = result["setup_edges"][0]

    assert edge["stage"] == "NOT_VALIDATED"
    assert edge["promotion_ready"] is False
    assert edge["early_sample"]["net_pnl"] > 0
    assert edge["recent_sample"]["net_pnl"] < 0


def test_detector_conflicts_become_high_priority_only_after_repeated_labels():
    snapshots = [
        _snapshot(
            i,
            setup=None,
            pnl=-100,
            manual_rule="market_sign",
            manual_status="fail",
            system_status="pass",
        )
        for i in range(5)
    ]

    result = build_le_learning_core(snapshots)
    calibration = result["detector_calibration"][0]

    assert calibration["id"] == "market_sign"
    assert calibration["labeled"] == 5
    assert calibration["system_conflicts"] == 5
    assert calibration["priority"] == "HIGH"
    assert calibration["agreement_rate_pct"] == 0.0


def test_manual_resolution_of_unknown_is_calibrated_without_rewriting_rules():
    snapshots = [
        _snapshot(
            i,
            setup=None,
            pnl=25,
            manual_rule="flag_forming",
            manual_status="pass",
            system_status="unknown",
        )
        for i in range(4)
    ]

    result = build_le_learning_core(snapshots)
    calibration = result["detector_calibration"][0]

    assert calibration["system_unknown_resolved"] == 4
    assert calibration["priority"] == "WATCH"
    assert result["promotion_policy"]["automatic_rule_mutation"] is False
