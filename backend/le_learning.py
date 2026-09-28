from __future__ import annotations

from collections import defaultdict
from typing import Any


LE_LEARNING_VERSION = "LE_LEARNING_2026_09_v1"

DISCOVERY_MIN = 8
DEVELOPING_MIN = 20
VALIDATION_MIN = 30
VALIDATION_MIN_DAYS = 10
OVERALL_PF_MIN = 1.25
RECENT_PF_MIN = 1.10


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _stats(rows: list[dict]) -> dict:
    count = len(rows)
    pnls = [_money(row.get("net_pnl")) for row in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)
    dates = sorted({str(row.get("date") or "") for row in rows if row.get("date")})
    return {
        "trades": count,
        "trading_days": len(dates),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / count * 100, 1) if count else 0.0,
        "net_pnl": round(net, 2),
        "avg_pnl": round(net / count, 2) if count else 0.0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else None,
        "date_from": dates[0] if dates else None,
        "date_to": dates[-1] if dates else None,
    }


def _chronological(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("trade_group") or ""),
        ),
    )


def _split_validation(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    ordered = _chronological(rows)
    if len(ordered) < 2:
        return ordered, []
    split = max(1, min(len(ordered) - 1, int(round(len(ordered) * 0.6))))
    return ordered[:split], ordered[split:]


def _pf_at_least(value: Any, floor: float) -> bool:
    if value is None:
        return True
    try:
        return float(value) >= floor
    except (TypeError, ValueError):
        return False


def _edge_stage(rows: list[dict]) -> dict:
    overall = _stats(rows)
    early_rows, recent_rows = _split_validation(rows)
    early = _stats(early_rows)
    recent = _stats(recent_rows)

    count = overall["trades"]
    if count < DISCOVERY_MIN:
        stage = "DISCOVERY"
        reason = f"Need at least {DISCOVERY_MIN} trades before the setup leaves discovery."
    elif count < DEVELOPING_MIN:
        stage = "DEVELOPING"
        reason = f"Developing sample. Need at least {DEVELOPING_MIN} trades before candidate review."
    elif count < VALIDATION_MIN:
        stage = "CANDIDATE"
        reason = f"Candidate edge. Need at least {VALIDATION_MIN} trades for validation."
    else:
        enough_days = overall["trading_days"] >= VALIDATION_MIN_DAYS
        overall_positive = (
            overall["net_pnl"] > 0
            and overall["avg_pnl"] > 0
            and _pf_at_least(overall["profit_factor"], OVERALL_PF_MIN)
        )
        recent_positive = (
            recent["trades"] > 0
            and recent["net_pnl"] > 0
            and recent["avg_pnl"] > 0
            and _pf_at_least(recent["profit_factor"], RECENT_PF_MIN)
        )
        early_positive = (
            early["trades"] > 0
            and early["net_pnl"] > 0
            and early["avg_pnl"] > 0
        )

        if enough_days and overall_positive and early_positive and recent_positive:
            stage = "VALIDATED"
            reason = (
                "Passed sample-size, distinct-day, overall expectancy, and chronological "
                "early/recent stability gates."
            )
        else:
            stage = "NOT_VALIDATED"
            failed = []
            if not enough_days:
                failed.append(f"fewer than {VALIDATION_MIN_DAYS} trading days")
            if not overall_positive:
                failed.append("overall expectancy/profit-factor gate")
            if not early_positive:
                failed.append("early-sample profitability")
            if not recent_positive:
                failed.append("recent out-of-sample profitability")
            reason = "Validation failed: " + ", ".join(failed) + "."

    return {
        "stage": stage,
        "reason": reason,
        "overall": overall,
        "early_sample": early,
        "recent_sample": recent,
        "promotion_ready": stage == "VALIDATED",
    }


def _manual_setup_groups(snapshots: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for snapshot in snapshots:
        manual = snapshot.get("manual_le_evidence") or {}
        for setup in manual.get("setup_tags") or []:
            name = str(setup or "").strip()
            if name:
                groups[name].append(snapshot)
    return groups


def _calibration_rows(snapshots: list[dict]) -> list[dict]:
    rows: dict[str, dict] = {}

    for snapshot in snapshots:
        pnl = _money(snapshot.get("net_pnl"))
        for check in snapshot.get("checks") or []:
            if not check.get("manual_override"):
                continue
            rule_id = str(check.get("id") or "")
            if not rule_id:
                continue

            row = rows.setdefault(rule_id, {
                "id": rule_id,
                "label": str(check.get("label") or rule_id),
                "labeled": 0,
                "system_agreements": 0,
                "system_conflicts": 0,
                "system_unknown_resolved": 0,
                "manual_pass": 0,
                "manual_fail": 0,
                "conflict_net_pnl": 0.0,
                "examples": [],
            })

            final_status = str(check.get("status") or "unknown")
            system_status = str((check.get("system_result") or {}).get("status") or "unknown")
            row["labeled"] += 1
            if final_status == "pass":
                row["manual_pass"] += 1
            elif final_status == "fail":
                row["manual_fail"] += 1

            if system_status == "unknown":
                row["system_unknown_resolved"] += 1
            elif system_status == final_status:
                row["system_agreements"] += 1
            else:
                row["system_conflicts"] += 1
                row["conflict_net_pnl"] += pnl

            if len(row["examples"]) < 5:
                row["examples"].append({
                    "trade_group": snapshot.get("trade_group"),
                    "date": snapshot.get("date"),
                    "ticker": snapshot.get("ticker"),
                    "net_pnl": pnl,
                    "manual_status": final_status,
                    "system_status": system_status,
                })

    output = []
    for row in rows.values():
        labeled = row["labeled"]
        known_system = row["system_agreements"] + row["system_conflicts"]
        item = dict(row)
        item["agreement_rate_pct"] = (
            round(row["system_agreements"] / known_system * 100, 1)
            if known_system else None
        )
        item["resolution_rate_pct"] = (
            round(row["system_unknown_resolved"] / labeled * 100, 1)
            if labeled else 0.0
        )
        item["conflict_net_pnl"] = round(row["conflict_net_pnl"], 2)
        if labeled < 5:
            item["priority"] = "WATCH"
            item["priority_reason"] = "Too few user-labeled examples to tune the detector safely."
        elif row["system_conflicts"] >= 3:
            item["priority"] = "HIGH"
            item["priority_reason"] = "Repeated user-vs-system disagreements indicate a detector gap."
        elif row["system_unknown_resolved"] >= 3:
            item["priority"] = "MEDIUM"
            item["priority_reason"] = "Manual labels repeatedly resolve Unknown system evidence."
        else:
            item["priority"] = "LOW"
            item["priority_reason"] = "Current labeled evidence does not show a repeated detector problem."
        output.append(item)

    rank = {"HIGH": 0, "MEDIUM": 1, "WATCH": 2, "LOW": 3}
    output.sort(
        key=lambda item: (
            rank.get(item["priority"], 9),
            -item["system_conflicts"],
            -item["system_unknown_resolved"],
            -item["labeled"],
            item["label"],
        )
    )
    return output


def build_le_learning_core(snapshots: list[dict]) -> dict:
    """Use authoritative manual LE labels to improve logic without overfitting.

    This module never rewrites compliance rules automatically. It identifies
    detector-calibration gaps and validates user-confirmed setup edges through
    chronological holdout-style checks before they are eligible for promotion.
    """
    valid = [
        snapshot for snapshot in snapshots
        if isinstance(snapshot, dict) and snapshot.get("trade_group")
    ]

    setup_edges = []
    for setup_name, rows in _manual_setup_groups(valid).items():
        validation = _edge_stage(rows)
        setup_edges.append({
            "id": setup_name.lower().replace(" ", "_"),
            "label": setup_name,
            "evidence_source": "USER_MANUAL",
            **validation,
        })

    stage_rank = {
        "VALIDATED": 0,
        "CANDIDATE": 1,
        "DEVELOPING": 2,
        "DISCOVERY": 3,
        "NOT_VALIDATED": 4,
    }
    setup_edges.sort(
        key=lambda item: (
            stage_rank.get(item["stage"], 9),
            -item["overall"]["net_pnl"],
            -item["overall"]["trades"],
            item["label"],
        )
    )

    calibration = _calibration_rows(valid)
    validated = [item for item in setup_edges if item["promotion_ready"]]
    high_priority = [item for item in calibration if item["priority"] == "HIGH"]

    if validated:
        headline = (
            str(len(validated))
            + " user-confirmed setup(s) passed the LE validation gate. "
            + "They are eligible for deliberate playbook review, not automatic rule mutation."
        )
    elif setup_edges:
        headline = (
            "Manual setup evidence is being learned, but no setup has enough stable "
            "out-of-sample evidence for promotion yet."
        )
    else:
        headline = (
            "No user-confirmed LE setup has enough labeled history to evaluate yet. "
            "Continue journaling exact LE setup tags."
        )

    next_actions = []
    if high_priority:
        top = high_priority[0]
        next_actions.append({
            "kind": "detector",
            "title": "Highest-priority detector improvement",
            "text": (
                top["label"] + " has " + str(top["system_conflicts"])
                + " user-vs-system conflict(s) across " + str(top["labeled"])
                + " labeled trade(s). Improve this detector before expanding automation."
            ),
        })
    if validated:
        top = validated[0]
        next_actions.append({
            "kind": "edge",
            "title": "Validated setup candidate",
            "text": (
                top["label"] + " passed chronological stability gates across "
                + str(top["overall"]["trades"]) + " trades and "
                + str(top["overall"]["trading_days"]) + " trading days."
            ),
        })
    elif setup_edges:
        top = setup_edges[0]
        next_actions.append({
            "kind": "sample",
            "title": "Keep collecting this setup",
            "text": (
                top["label"] + " is currently " + top["stage"].lower().replace("_", " ")
                + " with " + str(top["overall"]["trades"]) + " user-confirmed trade(s)."
            ),
        })

    return {
        "learning_version": LE_LEARNING_VERSION,
        "headline": headline,
        "promotion_policy": {
            "discovery_min_trades": DISCOVERY_MIN,
            "candidate_min_trades": DEVELOPING_MIN,
            "validation_min_trades": VALIDATION_MIN,
            "validation_min_trading_days": VALIDATION_MIN_DAYS,
            "overall_profit_factor_min": OVERALL_PF_MIN,
            "recent_profit_factor_min": RECENT_PF_MIN,
            "requires_positive_overall_expectancy": True,
            "requires_positive_early_sample": True,
            "requires_positive_recent_sample": True,
            "automatic_rule_mutation": False,
        },
        "setup_edges": setup_edges,
        "detector_calibration": calibration,
        "validated_setup_count": len(validated),
        "high_priority_detector_count": len(high_priority),
        "next_actions": next_actions,
        "note": (
            "Manual LE tags are treated as ground truth labels. Profitability is used to "
            "measure edge, not to redefine whether a tagged rule fact was true. "
            "Validation is descriptive and cannot guarantee future profitability."
        ),
    }
