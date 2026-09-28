from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


LE_COMPLIANCE_VERSION = "LE_PLAYBOOK_2026_09_v1"

LE_PLAYBOOK_REFERENCE = {
    "name": "The LE Trading System — Guide Series",
    "version": "Complete collection, September 2026",
    "methodology": "Ellis Dillinger / LE Trading Academy",
    "principles": [
        "No Flag, no Line, no Sign -> not worth your time.",
        "Levels first, then EMAs. Level break = trend. No break = no trade.",
        "Stop at three or broke you'll be. Two reds = done for the day.",
        "Do not diddle in the middle. No trades in the chop zone.",
        "Hold the line when stars align. Let runners work until the 8 EMA breaks.",
        "Risk before reward. System over profits.",
    ],
    "source_pages": {
        "three_pillars": [15, 18, 52],
        "three_trade_rule": [17, 18],
        "trend_establishment": [24, 29, 30],
        "entry_exclusions": [31],
        "stops": [32, 33],
        "exits": [34, 35],
        "risk_sizing": [38, 39],
        "time_windows": [40],
        "journal": [41],
        "psychology": [44, 45, 46],
        "pre_trade_checklist": [50, 51],
        "golden_rules": [52],
    },
}


CHECK_DEFINITIONS = (
    ("level_broken", "Level Broken?", [50, 51]),
    ("trend_established", "Trend Established?", [24, 29, 30, 50]),
    ("ema_aligned", "EMA Aligned?", [15, 21, 24, 50]),
    ("ema_snug", "EMA Snug?", [30, 31, 50]),
    ("market_sign", "Market Sign?", [15, 50]),
    ("flag_forming", "Flag Forming?", [15, 30, 50]),
    ("hard_stop_set", "Hard Stop Set?", [32, 50]),
    ("size_correct", "Size Correct?", [38, 39, 50]),
    ("risk_reward", "R:R >= 2:1?", [38, 50]),
    ("trade_count_ok", "Trade Count OK?", [17, 38, 50]),
    ("not_chasing", "Not Chasing?", [15, 31, 44, 50]),
    ("not_chop_hour", "Not in Chop Hour?", [40, 50]),
    ("vix_checked", "VIX Checked?", [22, 40, 50]),
)


def _normalize_status(value: Any) -> str:
    value = str(value or "").strip().lower()
    if value == "pass":
        return "pass"
    if value == "fail":
        return "fail"
    return "unknown"


def _check(
    check_id: str,
    label: str,
    status: str,
    detail: str,
    *,
    evidence: dict | None = None,
    source_pages: list[int] | None = None,
) -> dict:
    return {
        "id": check_id,
        "label": label,
        "status": _normalize_status(status),
        "detail": detail,
        "evidence": evidence or {},
        "source_pages": source_pages or [],
    }


def _executions(trade: dict) -> list[dict]:
    value = trade.get("executions") or []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _initial_exposure(trade: dict) -> float | None:
    side = str(trade.get("side") or "LONG").upper()
    entry_action = "BOT" if side == "LONG" else "SOLD"
    entries = [
        fill for fill in _executions(trade)
        if str(fill.get("action") or "").upper() == entry_action
    ]
    if not entries:
        return None

    multiplier = 100.0 if str(trade.get("instrument_type") or "").upper() == "OPTION" else 1.0
    total = 0.0
    for fill in entries:
        try:
            qty = abs(float(fill.get("qty") or 0))
            price = abs(float(fill.get("price") or 0))
        except (TypeError, ValueError):
            continue
        total += qty * price * multiplier
    return round(total, 2) if total > 0 else None


def _day_trade_check(day_context: dict | None) -> tuple[str, str, dict]:
    if not day_context:
        return "unknown", "Daily trade sequence is unavailable.", {}

    sequence = day_context.get("sequence")
    prior = [str(v or "").lower() for v in (day_context.get("prior_results") or [])]
    evidence = {
        "sequence": sequence,
        "prior_results": prior,
        "day_trade_count": day_context.get("day_trade_count"),
    }
    if not isinstance(sequence, int):
        return "unknown", "Trade sequence could not be established.", evidence

    if sequence > 3:
        return "fail", f"This was trade #{sequence}; the LE system caps the day at three trades.", evidence

    if sequence >= 3 and len(prior) >= 2:
        first_two = prior[:2]
        if first_two == ["red", "red"]:
            return "fail", "Two red trades occurred before this entry; LE says the day is over.", evidence
        if first_two == ["red", "green"]:
            return "fail", "The first two outcomes were red then green; LE says stop after the recovery.", evidence
        if first_two == ["green", "red"]:
            return (
                "unknown",
                "The first two outcomes were green then red. LE allows another trade only with an A+ setup, "
                "and that A+ condition is not deterministically proven here.",
                evidence,
            )

    return "pass", f"Trade #{sequence} did not violate the deterministic LE daily trade-count gate.", evidence


def _size_check(trade: dict, analysis: dict, risk_plan: dict | None) -> tuple[str, str, dict]:
    capital = None
    if risk_plan:
        try:
            capital = float(risk_plan.get("capital")) if risk_plan.get("capital") is not None else None
        except (TypeError, ValueError):
            capital = None

    exposure = _initial_exposure(trade)
    risk_amount = analysis.get("risk_per_trade")
    try:
        risk_amount = abs(float(risk_amount)) if risk_amount is not None else None
    except (TypeError, ValueError):
        risk_amount = None

    evidence = {
        "capital": capital,
        "initial_exposure": exposure,
        "risk_per_trade": risk_amount,
        "exposure_pct": None,
        "risk_pct": None,
    }

    if not capital or capital <= 0:
        return "unknown", "Account capital for this trading day was not recorded, so LE sizing cannot be verified.", evidence

    exposure_pct = exposure / capital * 100 if exposure is not None else None
    risk_pct = risk_amount / capital * 100 if risk_amount is not None else None
    evidence["exposure_pct"] = round(exposure_pct, 2) if exposure_pct is not None else None
    evidence["risk_pct"] = round(risk_pct, 2) if risk_pct is not None else None

    explicit_failures = []
    if exposure_pct is not None and not (20 <= exposure_pct <= 30):
        explicit_failures.append(f"actual exposure was {exposure_pct:.1f}% (LE target 20-30%)")
    if risk_pct is not None and risk_pct > 5:
        explicit_failures.append(f"recorded risk was {risk_pct:.1f}% (LE maximum 5%)")
    if explicit_failures:
        return "fail", "; ".join(explicit_failures) + ".", evidence

    if exposure_pct is None or risk_pct is None:
        return (
            "unknown",
            "Capital is known, but both actual exposure and actual risk are required to verify the LE sizing rule.",
            evidence,
        )

    return (
        "pass",
        f"Actual exposure was {exposure_pct:.1f}% and recorded risk was {risk_pct:.1f}% of capital.",
        evidence,
    )


def _rr_check(analysis: dict) -> tuple[str, str, dict]:
    value = analysis.get("risk_reward")
    try:
        rr = float(value) if value is not None else None
    except (TypeError, ValueError):
        rr = None
    evidence = {"risk_reward": rr}
    if rr is None:
        return "unknown", "Planned risk/reward was not recorded for this trade.", evidence
    if rr >= 2:
        return "pass", f"Recorded planned R:R was {rr:.2f}:1.", evidence
    return "fail", f"Recorded planned R:R was {rr:.2f}:1, below the LE minimum of 2:1.", evidence


def _hard_stop_check(analysis: dict) -> tuple[str, str, dict]:
    stop = analysis.get("stop_loss")
    evidence = {"recorded_stop_loss": stop}
    if stop is None:
        return (
            "unknown",
            "No stop value is recorded. Absence in the journal does not prove a brokerage hard stop was missing.",
            evidence,
        )
    return (
        "unknown",
        "A stop value is recorded, but the journal cannot prove the hard order was placed in the brokerage before entry.",
        evidence,
    )


def _fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_le_compliance(
    trade: dict,
    review: dict,
    *,
    analysis: dict | None = None,
    day_context: dict | None = None,
    risk_plan: dict | None = None,
) -> dict:
    """Grade one trade against the PDF's 13-point LE checklist without guessing.

    Only deterministic journal/market evidence can produce pass/fail. Anything that
    cannot be proven from the available records is Unknown and does not count against
    the evaluated-check percentage.
    """
    analysis = analysis or {}
    ev = (review or {}).get("evidence") or {}
    entry_checks = ev.get("entry_checks") or {}
    management = ev.get("management_10m8ema") or {}

    checks: list[dict] = []

    item = entry_checks.get("level_break") or {}
    checks.append(_check(
        "level_broken", "Level Broken?", item.get("status"), item.get("detail") or "Directional level-break evidence unavailable.",
        evidence={"entry_check": item}, source_pages=[50, 51],
    ))

    level_status = _normalize_status((entry_checks.get("level_break") or {}).get("status"))
    align_status = _normalize_status((entry_checks.get("ema_alignment") or {}).get("status"))
    snug_status = _normalize_status((entry_checks.get("ema_extension") or {}).get("status"))
    beyond_status = _normalize_status((entry_checks.get("ema_beyond_broken_level") or {}).get("status"))
    bars_since = ev.get("bars_since_level_break")
    trend_evidence = {
        "level_break": level_status,
        "ema_alignment": align_status,
        "ema_snug": snug_status,
        "ema_beyond_broken_level": beyond_status,
        "bars_since_level_break": bars_since,
    }
    if "fail" in {level_status, align_status, snug_status, beyond_status}:
        trend_status = "fail"
        trend_detail = "FORM -> ESTABLISH failed one or more deterministic prerequisites."
    elif all(v == "pass" for v in (level_status, align_status, snug_status, beyond_status)) and isinstance(bars_since, int) and bars_since >= 1:
        trend_status = "pass"
        trend_detail = (
            "A verified level break occurred first, then the 10m 8 EMA caught up beyond the broken level "
            "with price aligned and snug after at least one completed 10-minute bar."
        )
    else:
        trend_status = "unknown"
        trend_detail = (
            "The available data does not fully prove the PDF's FORM -> ESTABLISH sequence without inference."
        )
    checks.append(_check(
        "trend_established", "Trend Established?", trend_status, trend_detail,
        evidence=trend_evidence, source_pages=[24, 29, 30, 50],
    ))

    item = entry_checks.get("ema_alignment") or {}
    checks.append(_check(
        "ema_aligned", "EMA Aligned?", item.get("status"), item.get("detail") or "10m 8 EMA alignment unavailable.",
        evidence={"entry_check": item}, source_pages=[15, 21, 24, 50],
    ))

    item = entry_checks.get("ema_extension") or {}
    checks.append(_check(
        "ema_snug", "EMA Snug?", item.get("status"), item.get("detail") or "EMA distance unavailable.",
        evidence={"entry_check": item}, source_pages=[30, 31, 50],
    ))

    item = entry_checks.get("market_sign") or {}
    checks.append(_check(
        "market_sign", "Market Sign?", item.get("status"), item.get("detail") or "SPY/QQQ confirmation unavailable.",
        evidence={"entry_check": item, "market_sign": ev.get("market_sign")}, source_pages=[15, 50],
    ))

    checks.append(_check(
        "flag_forming", "Flag Forming?", "unknown",
        "The LE PDF requires a controlled consolidation/pullback. The current structured evidence does not "
        "deterministically prove the visual flag shape, so Brain must not guess.",
        evidence={"ema_distance_pct": ev.get("ema_distance_pct"), "bars_since_level_break": bars_since},
        source_pages=[15, 30, 50],
    ))

    status, detail, evidence = _hard_stop_check(analysis)
    checks.append(_check("hard_stop_set", "Hard Stop Set?", status, detail, evidence=evidence, source_pages=[32, 50]))

    status, detail, evidence = _size_check(trade, analysis, risk_plan)
    checks.append(_check("size_correct", "Size Correct?", status, detail, evidence=evidence, source_pages=[38, 39, 50]))

    status, detail, evidence = _rr_check(analysis)
    checks.append(_check("risk_reward", "R:R >= 2:1?", status, detail, evidence=evidence, source_pages=[38, 50]))

    status, detail, evidence = _day_trade_check(day_context)
    checks.append(_check("trade_count_ok", "Trade Count OK?", status, detail, evidence=evidence, source_pages=[17, 38, 50]))

    extension_status = _normalize_status((entry_checks.get("ema_extension") or {}).get("status"))
    if extension_status == "fail":
        chase_status = "fail"
        chase_detail = "Price was airgapped from the 10m 8 EMA; the LE guide defines this as a chase/no-entry condition."
    else:
        chase_status = "unknown"
        chase_detail = (
            "No deterministic airgap violation was proven, but the journal does not yet prove the entry was not at HOD/LOD "
            "or after a vertical move."
        )
    checks.append(_check(
        "not_chasing", "Not Chasing?", chase_status, chase_detail,
        evidence={"ema_extension": entry_checks.get("ema_extension"), "session_window": ev.get("session_window")},
        source_pages=[15, 31, 44, 50],
    ))

    window = str(ev.get("session_window") or "")
    if window == "chop_hour":
        chop_hour_status = "fail"
        chop_hour_detail = "Entry occurred during 11:30 AM-1:30 PM ET, the LE Chop Hour."
    elif window:
        chop_hour_status = "pass"
        chop_hour_detail = f"Entry occurred in the {window.replace('_', ' ')} window, outside LE Chop Hour."
    else:
        chop_hour_status = "unknown"
        chop_hour_detail = "Entry time window could not be established."
    checks.append(_check(
        "not_chop_hour", "Not in Chop Hour?", chop_hour_status, chop_hour_detail,
        evidence={"session_window": window}, source_pages=[40, 50],
    ))

    checks.append(_check(
        "vix_checked", "VIX Checked?", "unknown",
        "VIX-at-entry and any size reduction are not currently stored in the journal evidence packet.",
        evidence={}, source_pages=[22, 40, 50],
    ))

    # Extra non-negotiable findings from the broader LE guide. These are kept
    # separate from the 13 checklist so the score denominator stays faithful to Vol. 6.
    chop_range = entry_checks.get("chop_range") or {}
    first_10m = window == "scan_only"
    hold_relation = management.get("exit_relation_to_ema_break")
    extra_findings = [
        _check(
            "scan_only_entry",
            "No entry during 9:30-9:40 ET scan-only window",
            "fail" if first_10m else "pass" if window else "unknown",
            "Entry occurred during the first 10 minutes." if first_10m
            else "Entry was outside the scan-only window." if window
            else "Entry time unavailable.",
            evidence={"session_window": window},
            source_pages=[31, 40],
        ),
        _check(
            "premarket_chop_zone",
            "Do Not Diddle in the Middle",
            "fail" if _normalize_status(chop_range.get("status")) == "fail"
            else "pass" if _normalize_status(chop_range.get("status")) == "pass"
            else "unknown",
            chop_range.get("detail") or "PMH-PML range evidence unavailable.",
            evidence={"entry_check": chop_range},
            source_pages=[17, 24, 52],
        ),
        _check(
            "hold_the_line",
            "Hold the Line When Stars Align",
            "pass" if hold_relation == "after_confirmed_break" else "unknown",
            "Final exit followed the first confirmed opposing 10m 8 EMA break."
            if hold_relation == "after_confirmed_break"
            else "The journal cannot prove a runner-management violation from the final exit alone."
            if hold_relation
            else "Exit/EMA relationship unavailable.",
            evidence={
                "exit_relation_to_ema_break": hold_relation,
                "post_exit_favorable_move_pct_30m": management.get("post_exit_favorable_move_pct_30m"),
            },
            source_pages=[17, 34, 52],
        ),
    ]

    passed = sum(c["status"] == "pass" for c in checks)
    failed = sum(c["status"] == "fail" for c in checks)
    unknown = sum(c["status"] == "unknown" for c in checks)
    evaluated = passed + failed
    evaluated_pct = round(passed / evaluated * 100, 1) if evaluated else None
    coverage_pct = round(evaluated / len(checks) * 100, 1) if checks else 0.0

    extra_failed = [f for f in extra_findings if f["status"] == "fail"]
    if failed or extra_failed:
        classification = "LE_VIOLATION"
        classification_label = "LE violation found"
    elif unknown:
        classification = "INCOMPLETE_EVIDENCE"
        classification_label = "Incomplete evidence"
    else:
        classification = "LE_COMPLIANT"
        classification_label = "LE compliant"

    snapshot_basis = {
        "trade_group": trade.get("trade_group"),
        "net_pnl": trade.get("net_pnl"),
        "ruleset_version": (review or {}).get("ruleset_version"),
        "evidence": ev,
        "analysis": {
            "stop_loss": analysis.get("stop_loss"),
            "risk_per_trade": analysis.get("risk_per_trade"),
            "risk_reward": analysis.get("risk_reward"),
        },
        "day_context": day_context,
        "risk_plan": risk_plan,
        "compliance_version": LE_COMPLIANCE_VERSION,
    }

    return {
        "compliance_version": LE_COMPLIANCE_VERSION,
        "playbook": {
            "name": LE_PLAYBOOK_REFERENCE["name"],
            "version": LE_PLAYBOOK_REFERENCE["version"],
            "checklist_source_pages": [50, 51],
        },
        "trade_group": trade.get("trade_group"),
        "ticker": trade.get("ticker"),
        "date": trade.get("date"),
        "net_pnl": round(float(trade.get("net_pnl") or 0), 2),
        "classification": classification,
        "classification_label": classification_label,
        "score": {
            "passed": passed,
            "failed": failed,
            "unknown": unknown,
            "evaluated": evaluated,
            "total": len(checks),
            "evaluated_pass_pct": evaluated_pct,
            "coverage_pct": coverage_pct,
        },
        "checks": checks,
        "extra_findings": extra_findings,
        "failed_rule_ids": [c["id"] for c in checks if c["status"] == "fail"]
        + [f["id"] for f in extra_findings if f["status"] == "fail"],
        "unknown_rule_ids": [c["id"] for c in checks if c["status"] == "unknown"],
        "evidence_quality": ev.get("evidence_quality"),
        "evidence_fingerprint": _fingerprint(snapshot_basis),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def summarize_le_compliance_snapshots(snapshots: list[dict]) -> dict:
    valid = [s for s in snapshots if isinstance(s, dict) and s.get("checks")]
    rule_stats: dict[str, dict] = {}
    classifications: dict[str, int] = {}

    for snapshot in valid:
        classification = str(snapshot.get("classification") or "UNKNOWN")
        classifications[classification] = classifications.get(classification, 0) + 1
        pnl = float(snapshot.get("net_pnl") or 0)

        for check in snapshot.get("checks") or []:
            rule_id = str(check.get("id") or "")
            if not rule_id:
                continue
            row = rule_stats.setdefault(rule_id, {
                "id": rule_id,
                "label": check.get("label") or rule_id,
                "pass": 0,
                "fail": 0,
                "unknown": 0,
                "evaluated": 0,
                "fail_net_pnl": 0.0,
                "pass_net_pnl": 0.0,
            })
            status = _normalize_status(check.get("status"))
            row[status] += 1
            if status in {"pass", "fail"}:
                row["evaluated"] += 1
            if status == "fail":
                row["fail_net_pnl"] += pnl
            elif status == "pass":
                row["pass_net_pnl"] += pnl

    rows = []
    for row in rule_stats.values():
        row = dict(row)
        evaluated = row["evaluated"]
        row["pass_rate_pct"] = round(row["pass"] / evaluated * 100, 1) if evaluated else None
        row["fail_net_pnl"] = round(row["fail_net_pnl"], 2)
        row["pass_net_pnl"] = round(row["pass_net_pnl"], 2)
        rows.append(row)

    rows.sort(key=lambda r: (-r["fail"], r["fail_net_pnl"], r["label"]))
    return {
        "compliance_version": LE_COMPLIANCE_VERSION,
        "audited_trades": len(valid),
        "classification_counts": classifications,
        "rule_stats": rows,
        "note": (
            "P&L grouped by rule status is descriptive association only. It does not prove that a rule failure caused the P&L."
        ),
    }
