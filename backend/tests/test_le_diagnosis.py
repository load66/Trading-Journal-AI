from le_diagnosis import build_le_diagnosis


def _snapshot(group, classification, checks, pnl=0):
    return {
        "trade_group": group,
        "compliance_version": "LE_PLAYBOOK_2026_09_v1",
        "classification": classification,
        "classification_label": classification.replace("_", " ").title(),
        "score": {"passed": 3, "failed": 1, "unknown": 1},
        "checks": checks,
        "failed_rule_ids": [c["id"] for c in checks if c["status"] == "fail"],
        "unknown_rule_ids": [c["id"] for c in checks if c["status"] == "unknown"],
        "net_pnl": pnl,
        "generated_at": "2026-09-28T10:00:00+00:00",
    }


def _check(rule_id, label, status, detail=""):
    return {"id": rule_id, "label": label, "status": status, "detail": detail}


def test_le_diagnosis_preserves_unknown_and_finds_profitable_cohort_and_leak():
    trades = []
    snapshots = []

    for i in range(10):
        group = f"g{i}"
        pnl = 100 if i < 8 else -50
        trades.append({
            "id": i + 1,
            "trade_group": group,
            "date": f"2026-09-{10+i:02d}",
            "ticker": "SPY",
            "side": "LONG",
            "net_pnl": pnl,
        })
        snapshots.append(_snapshot(
            group,
            "LE_VIOLATION" if i >= 8 else "INCOMPLETE_EVIDENCE",
            [
                _check("level_broken", "Level Broken?", "pass", "PDH, PMH"),
                _check("ema_snug", "EMA Snug?", "pass"),
                _check("market_sign", "Market Sign?", "pass"),
                _check("not_chop_hour", "Not in Chop Hour?", "fail" if i >= 8 else "pass"),
                _check("vix_checked", "VIX Checked?", "unknown"),
            ],
            pnl,
        ))

    report = build_le_diagnosis(
        trades,
        snapshots,
        compliance_version="LE_PLAYBOOK_2026_09_v1",
    )

    assert report["coverage_pct"] == 100.0
    assert report["missing_trades"] == 0
    assert report["most_profitable_cohort"]["id"] == "outside_ema_sign_no_chop"
    assert report["most_profitable_cohort"]["trades"] == 8
    assert report["most_profitable_cohort"]["net_pnl"] == 800.0

    chop = next(row for row in report["rules"] if row["id"] == "not_chop_hour")
    assert chop["pass"]["trades"] == 8
    assert chop["fail"]["trades"] == 2
    assert chop["unknown"]["trades"] == 0

    vix = next(row for row in report["rules"] if row["id"] == "vix_checked")
    assert vix["unknown"]["trades"] == 10
    assert vix["pass"]["trades"] == 0
    assert vix["fail"]["trades"] == 0


def test_le_diagnosis_ignores_stale_snapshot_for_current_coverage():
    trades = [{
        "id": 1,
        "trade_group": "g1",
        "date": "2026-09-25",
        "ticker": "QQQ",
        "side": "SHORT",
        "net_pnl": -25,
    }]
    stale = _snapshot(
        "g1",
        "LE_VIOLATION",
        [_check("level_broken", "Level Broken?", "fail")],
        -25,
    )
    stale["compliance_version"] = "OLD_VERSION"

    report = build_le_diagnosis(
        trades,
        [stale],
        compliance_version="LE_PLAYBOOK_2026_09_v1",
    )

    assert report["audited_trades"] == 0
    assert report["missing_trades"] == 1
    assert report["stale_snapshots"] == 1
    assert report["coverage_pct"] == 0.0
