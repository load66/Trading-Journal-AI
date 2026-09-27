from __future__ import annotations

import hashlib
import json

import httpx

from ai_analysis import (
    GROQ_API_URL,
    GROQ_MODEL,
    MODEL,
    _strip_json_fence,
    _valid_api_key,
    get_client,
    response_text,
)
from smoking_gun_library import ANALYTICS_ENGINE_VERSION


MANAGEMENT_AI_EVIDENCE_VERSION = 1

MANAGEMENT_AI_PROMPT = """You are a professional trading coach analyzing TRADE MANAGEMENT only.

You receive verified, deterministic metrics produced by the trading journal. The metrics are the source of truth.
Your job is to interpret them clearly for a day trader without changing, recomputing, or inventing evidence.

STRICT RULES:
- Discuss only exit efficiency/profit capture, holding behavior, MFE/MAE risk behavior, data coverage, and explicitly supplied overnight exclusions.
- Never infer psychology, emotion, confidence, fear, tilt, intent, setup quality, discipline, or rule-breaking from P&L or management metrics.
- Never claim a stop rule was violated or recommend one universal stop from MFE/MAE alone.
- If winner/loser average and median hold-time signals disagree, say there is NO firm holding-time diagnosis.
- Overnight trades listed as excluded must NOT be used to diagnose day-trading holding behavior.
- If a metric has LOW/insufficient coverage, do not promote it to a firm conclusion.
- The supplied deterministic_priority and deterministic signals are locked. Do not contradict or replace them.
- Reference only numbers present in the supplied evidence.
- Keep the response concise, useful, and specific.
- Return ONLY valid JSON, no markdown.

Required JSON schema:
{
  "diagnosis": "2-4 sentences explaining the management picture from the verified evidence",
  "strongest_behavior": "one concise evidence-backed strength, or 'Insufficient evidence'",
  "primary_improvement": "one concise evidence-backed improvement, or 'No dominant management leak is confirmed'",
  "next_focus": "one concrete thing to monitor or test next"
}
"""


def _number(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _capture_signal(kpis: dict, goal: float) -> str:
    capture = _number(kpis.get("exit_efficiency"))
    confidence = str(kpis.get("capture_confidence") or "LOW").upper()
    if capture is None or confidence == "LOW":
        return "insufficient"
    if confidence == "DEVELOPING":
        return "developing"
    return "above_goal" if capture >= goal else "below_goal"


def _holding_signal(hold: dict) -> dict:
    winner_avg = _number(hold.get("winners_avg_min"))
    loser_avg = _number(hold.get("losers_avg_min"))
    winner_median = _number(hold.get("winners_median_min"))
    loser_median = _number(hold.get("losers_median_min"))
    winner_count = int(hold.get("winner_count") or 0)
    loser_count = int(hold.get("loser_count") or 0)

    reliable = (
        winner_count >= 5
        and loser_count >= 5
        and winner_avg is not None
        and loser_avg is not None
    )
    if not reliable:
        return {"signal": "insufficient", "reliable": False, "mixed": False}

    avg_leak = loser_avg > winner_avg * 1.10
    median_available = winner_median is not None and loser_median is not None
    median_leak = median_available and loser_median > winner_median * 1.10
    mixed = median_available and avg_leak != median_leak

    if mixed:
        signal = "mixed"
    elif avg_leak and (not median_available or median_leak):
        signal = "loser_hold_leak"
    else:
        signal = "no_loser_hold_leak"

    return {"signal": signal, "reliable": True, "mixed": mixed}


def _risk_signal(kpis: dict) -> dict:
    raw_mfe = _number(kpis.get("avg_mfe"))
    raw_mae = _number(kpis.get("avg_mae"))
    median_mfe = _number(kpis.get("median_mfe"))
    median_mae = _number(kpis.get("median_mae"))
    confidence = str(kpis.get("excursion_confidence") or "LOW").upper()

    if raw_mfe is None or raw_mae is None or confidence == "LOW":
        return {"signal": "insufficient", "usable": False, "mixed": False}

    if confidence == "DEVELOPING":
        return {"signal": "developing", "usable": True, "mixed": False}

    favorable = abs(raw_mfe)
    adverse = abs(raw_mae)
    avg_positive = favorable > adverse * 1.10
    avg_leak = adverse > favorable * 1.10

    median_available = median_mfe is not None and median_mae is not None
    median_positive = median_available and abs(median_mfe) > abs(median_mae) * 1.10
    median_leak = median_available and abs(median_mae) > abs(median_mfe) * 1.10

    mixed = median_available and (
        (avg_positive and median_leak)
        or (avg_leak and median_positive)
        or ((not avg_positive and not avg_leak) != (not median_positive and not median_leak))
    )

    winner_median_mae = _number(kpis.get("winner_median_mae"))
    loser_median_mae = _number(kpis.get("loser_median_mae"))
    loser_mfe_le_5 = _number(kpis.get("loser_mfe_le_5_pct"))
    early_failure = (
        winner_median_mae is not None
        and loser_median_mae is not None
        and loser_mfe_le_5 is not None
        and loser_median_mae >= winner_median_mae * 1.5
        and loser_mfe_le_5 >= 50
    )

    if early_failure:
        signal = "early_failure_pattern"
    elif mixed:
        signal = "mixed"
    elif avg_leak and (not median_available or median_leak):
        signal = "adverse_dominant"
    elif avg_positive and (not median_available or median_positive):
        signal = "favorable_dominant"
    else:
        signal = "balanced"

    return {
        "signal": signal,
        "usable": True,
        "mixed": mixed,
        "early_failure_pattern": early_failure,
    }


def build_management_evidence(
    *,
    range_key: str,
    date_from: str | None,
    date_to: str | None,
    account_type: str | None,
    kpis: dict,
    edge: dict,
    goals: dict,
) -> dict:
    hold = (edge or {}).get("hold_time") or {}
    goal = _number((goals or {}).get("exit_efficiency"))
    if goal is None:
        goal = 60.0

    capture_signal = _capture_signal(kpis or {}, goal)
    hold_state = _holding_signal(hold)
    risk_state = _risk_signal(kpis or {})

    if hold_state["signal"] == "loser_hold_leak":
        priority = "holding_losers_too_long"
    elif capture_signal == "below_goal":
        priority = "profit_capture"
    elif risk_state["signal"] == "early_failure_pattern":
        priority = "entry_quality_early_invalidation"
    elif risk_state["signal"] == "adverse_dominant":
        priority = "risk_containment"
    elif hold_state["signal"] == "mixed" or risk_state["signal"] == "mixed":
        priority = "mixed_evidence"
    elif (
        capture_signal in {"insufficient", "developing"}
        and hold_state["signal"] == "insufficient"
        and risk_state["signal"] in {"insufficient", "developing"}
    ):
        priority = "insufficient_evidence"
    else:
        priority = "no_dominant_management_leak"

    winner_mae_le_20 = _number((kpis or {}).get("winner_mae_le_20_pct"))
    winners_beyond_20 = None if winner_mae_le_20 is None else max(0.0, 100.0 - winner_mae_le_20)

    return {
        "range": str(range_key or "30D").upper(),
        "date_from": date_from,
        "date_to": date_to,
        "account_type": account_type or "unknown",
        "total_trades": int((kpis or {}).get("total_trades") or (edge or {}).get("total_trades") or 0),
        "total_net_pnl": _number((kpis or {}).get("total_net_pnl")),
        "capture": {
            "exit_efficiency_pct": _number((kpis or {}).get("exit_efficiency")),
            "goal_pct": goal,
            "covered_winners": int((kpis or {}).get("capture_n") or 0),
            "winner_total": int((kpis or {}).get("capture_winner_total") or 0),
            "coverage_pct": _number((kpis or {}).get("capture_coverage_pct")),
            "confidence": str((kpis or {}).get("capture_confidence") or "LOW").upper(),
            "signal": capture_signal,
        },
        "holding": {
            "winner_avg_min": _number(hold.get("winners_avg_min")),
            "loser_avg_min": _number(hold.get("losers_avg_min")),
            "winner_median_min": _number(hold.get("winners_median_min")),
            "loser_median_min": _number(hold.get("losers_median_min")),
            "winner_count": int(hold.get("winner_count") or 0),
            "loser_count": int(hold.get("loser_count") or 0),
            "sample_count": int(hold.get("sample_count") or 0),
            "coverage_pct": _number(hold.get("coverage_pct")),
            "signal": hold_state["signal"],
            "overnight_excluded_count": int(hold.get("overnight_excluded_count") or 0),
            "overnight_excluded": hold.get("overnight_excluded") or [],
        },
        "risk": {
            "avg_mfe_pct": _number((kpis or {}).get("avg_mfe")),
            "avg_mae_pct": _number((kpis or {}).get("avg_mae")),
            "median_mfe_pct": _number((kpis or {}).get("median_mfe")),
            "median_mae_pct": _number((kpis or {}).get("median_mae")),
            "winner_median_mfe_pct": _number((kpis or {}).get("winner_median_mfe")),
            "loser_median_mfe_pct": _number((kpis or {}).get("loser_median_mfe")),
            "winner_median_mae_pct": _number((kpis or {}).get("winner_median_mae")),
            "loser_median_mae_pct": _number((kpis or {}).get("loser_median_mae")),
            "loser_mfe_le_5_pct": _number((kpis or {}).get("loser_mfe_le_5_pct")),
            "loser_mfe_le_10_pct": _number((kpis or {}).get("loser_mfe_le_10_pct")),
            "loser_mae_ge_25_pct": _number((kpis or {}).get("loser_mae_ge_25_pct")),
            "winners_beyond_20_mae_pct": winners_beyond_20,
            "covered_trades": int((kpis or {}).get("excursion_n") or 0),
            "coverage_pct": _number((kpis or {}).get("management_coverage_pct")),
            "confidence": str((kpis or {}).get("excursion_confidence") or "LOW").upper(),
            "signal": risk_state["signal"],
        },
        "deterministic_priority": priority,
        "analytics_engine_version": ANALYTICS_ENGINE_VERSION,
        "evidence_version": MANAGEMENT_AI_EVIDENCE_VERSION,
    }


def management_context_signature(evidence: dict) -> str:
    canonical = json.dumps(
        evidence,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _groq_management_analysis(user_content: str, api_key: str) -> dict:
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": MANAGEMENT_AI_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_completion_tokens": 1800,
        "reasoning_effort": "medium",
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    response = httpx.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90.0,
    )
    response.raise_for_status()
    body = response.json()
    try:
        raw = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Groq returned an unexpected response shape.") from exc
    return json.loads(_strip_json_fence(raw))


def _anthropic_management_analysis(user_content: str) -> dict:
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1800,
        system=MANAGEMENT_AI_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return json.loads(_strip_json_fence(response_text(response)))


def _locked_headline(priority: str) -> str:
    return {
        "holding_losers_too_long": "Holding losers too long is the clearest management leak.",
        "profit_capture": "Profit capture is the clearest management leak.",
        "entry_quality_early_invalidation": "Early invalidation is the clearest improvement candidate.",
        "risk_containment": "Risk containment is the clearest management concern.",
        "mixed_evidence": "The evidence is mixed, so no single management leak is confirmed.",
        "insufficient_evidence": "More verified management evidence is needed.",
        "no_dominant_management_leak": "No dominant management leak is confirmed.",
    }.get(priority, "No dominant management leak is confirmed.")


def generate_trade_management_analysis(evidence: dict) -> dict:
    user_content = (
        "VERIFIED TRADE MANAGEMENT EVIDENCE:\n"
        + json.dumps(evidence, indent=2, sort_keys=True, default=str)
        + "\n\nInterpret only this evidence and follow the locked deterministic signals."
    )

    groq_key = _valid_api_key("GROQ_API_KEY")
    anthropic_key = _valid_api_key("ANTHROPIC_API_KEY", "your_anthropic_api_key_here")
    errors = []
    result = None

    if groq_key:
        try:
            result = _groq_management_analysis(user_content, groq_key)
            result["ai_provider"] = "groq"
            result["ai_model"] = GROQ_MODEL
        except Exception as exc:
            errors.append(f"Groq: {exc}")
            if not anthropic_key:
                raise RuntimeError("Groq trade-management analysis failed. " + errors[-1]) from exc

    if result is None and anthropic_key:
        try:
            result = _anthropic_management_analysis(user_content)
            result["ai_provider"] = "anthropic"
            result["ai_model"] = MODEL
        except Exception as exc:
            errors.append(f"Anthropic: {exc}")
            raise RuntimeError("Trade-management AI failed. " + " | ".join(errors)) from exc

    if result is None:
        raise ValueError(
            "Trade-management AI is not configured. Add GROQ_API_KEY to the server "
            "environment, or ANTHROPIC_API_KEY as an optional fallback."
        )

    priority = str(evidence.get("deterministic_priority") or "no_dominant_management_leak")
    result["headline"] = _locked_headline(priority)
    result["focus_area"] = priority
    result["diagnosis"] = str(result.get("diagnosis") or "").strip()
    result["strongest_behavior"] = str(result.get("strongest_behavior") or "Insufficient evidence").strip()
    result["primary_improvement"] = str(
        result.get("primary_improvement") or "No dominant management leak is confirmed"
    ).strip()
    result["next_focus"] = str(result.get("next_focus") or "Keep collecting verified management evidence.").strip()
    result["evidence_locked"] = True
    result["evidence_version"] = MANAGEMENT_AI_EVIDENCE_VERSION
    result["analytics_engine_version"] = ANALYTICS_ENGINE_VERSION
    return result
