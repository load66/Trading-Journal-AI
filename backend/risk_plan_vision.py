"""Groq Vision extraction for TradingView Long/Short Position screenshots.

The vision model is used only to READ what is visible in the screenshot.
All arithmetic, consistency checks, trade matching, cash-risk calculations and
writes are deterministic and happen outside the model.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

import httpx

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
RISK_PLAN_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")

RISK_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "image_kind": {
            "type": "string",
            "enum": ["tradingview_position_tool", "chart_without_position_tool", "other"],
        },
        "direction": {"type": "string", "enum": ["LONG", "SHORT", "UNKNOWN"]},
        "entry_price": {"type": ["number", "null"]},
        "stop_price": {"type": ["number", "null"]},
        "target_price": {"type": ["number", "null"]},
        "risk_distance": {"type": ["number", "null"]},
        "reward_distance": {"type": ["number", "null"]},
        "risk_reward_ratio": {"type": ["number", "null"]},
        "cash_risk": {"type": ["number", "null"]},
        "cash_risk_currency": {"type": "string", "enum": ["USD", "UNKNOWN"]},
        "symbol": {"type": ["string", "null"]},
        "timeframe": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "image_kind",
        "direction",
        "entry_price",
        "stop_price",
        "target_price",
        "risk_distance",
        "reward_distance",
        "risk_reward_ratio",
        "cash_risk",
        "cash_risk_currency",
        "symbol",
        "timeframe",
        "confidence",
        "evidence",
        "warnings",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You extract a TradingView Long Position or Short Position risk plan from a screenshot.

This is an evidence-extraction task, not trading advice.

Rules:
1. Read only what is visibly supported by the screenshot.
2. Detect the TradingView position tool by its green target area and red stop area.
3. LONG means target is above entry and stop is below entry. SHORT means target is below entry and stop is above entry.
4. entry_price, stop_price and target_price must be exact visible price levels or levels that can be read unambiguously from the tool boundaries against the visible price axis. If exact enough reading is not possible, return null.
5. risk_distance and reward_distance are ABSOLUTE PRICE DISTANCES, not percentages and not dollars. Compact TradingView labels such as 1.06 and 3.47 may be price distances. Only return them when the image supports that interpretation.
6. cash_risk is a monetary amount only when an explicit currency/dollar risk is visibly shown. Never convert a price distance into cash risk.
7. Do not treat pivot labels, support/resistance labels, indicators, candle values, or unrelated annotations as entry/stop/target unless the position tool boundary clearly aligns with them.
8. If the screenshot does not contain a recognizable position tool, set image_kind accordingly and return null numeric fields.
9. Do not invent a symbol or timeframe that is not visible.
10. confidence is 0 to 1 and should reflect confidence in the extracted position-tool values, not confidence about the trade outcome.
11. evidence should briefly name the visible cues used. warnings should identify ambiguity or unreadable values.
Return only the structured JSON requested by the schema.
"""


def _groq_key() -> str:
    value = (os.getenv("GROQ_API_KEY") or "").strip()
    if not value:
        raise ValueError("GROQ_API_KEY is not configured on the server.")
    return value


def _data_url(image_bytes: bytes, content_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def extract_risk_plan_image(image_bytes: bytes, content_type: str) -> dict[str, Any]:
    """Read a risk-plan screenshot with Groq Vision and return validated extraction."""
    if not image_bytes:
        raise ValueError("The uploaded image is empty.")
    if len(image_bytes) > 20 * 1024 * 1024:
        raise ValueError("Risk-plan images must be 20 MB or smaller.")
    if content_type not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        raise ValueError("Risk-plan image must be PNG, JPEG, or WEBP.")

    payload = {
        "model": RISK_PLAN_VISION_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract the TradingView Long/Short Position tool from this screenshot. "
                            "Prefer null over guessing when a value is not clearly readable."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_url(image_bytes, content_type)},
                    },
                ],
            },
        ],
        "temperature": 1.0,
        "top_p": 0.95,
        "reasoning_effort": "medium",
        "reasoning_format": "hidden",
        "max_completion_tokens": 1600,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "tradingview_risk_plan",
                "strict": True,
                "schema": RISK_PLAN_SCHEMA,
            },
        },
    }

    response = httpx.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {_groq_key()}",
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
        raise ValueError("Groq Vision returned an unexpected response shape.") from exc

    result = json.loads(raw)
    return validate_extraction(result)


def _positive_number(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def validate_extraction(raw: dict[str, Any]) -> dict[str, Any]:
    """Deterministically validate/complete a model extraction without guessing."""
    result = dict(raw or {})
    warnings = list(result.get("warnings") or [])

    direction = str(result.get("direction") or "UNKNOWN").upper()
    if direction not in {"LONG", "SHORT", "UNKNOWN"}:
        direction = "UNKNOWN"
        warnings.append("Direction was invalid and was reset to UNKNOWN.")

    for field in (
        "entry_price",
        "stop_price",
        "target_price",
        "risk_distance",
        "reward_distance",
        "risk_reward_ratio",
        "cash_risk",
    ):
        result[field] = _positive_number(result.get(field))

    try:
        result["confidence"] = min(1.0, max(0.0, float(result.get("confidence") or 0)))
    except (TypeError, ValueError):
        result["confidence"] = 0.0

    entry = result.get("entry_price")
    stop = result.get("stop_price")
    target = result.get("target_price")
    risk_distance = result.get("risk_distance")
    reward_distance = result.get("reward_distance")

    # If absolute levels exist, those levels are the authoritative source for
    # distance/R:R math. The model is not allowed to perform the final math.
    geometry_valid = None
    if entry and stop and target and direction != "UNKNOWN":
        geometry_valid = (
            stop < entry < target if direction == "LONG"
            else target < entry < stop
        )
        if geometry_valid:
            derived_risk = abs(entry - stop)
            derived_reward = abs(target - entry)
            if risk_distance and abs(risk_distance - derived_risk) / max(derived_risk, 1e-9) > 0.10:
                warnings.append("Displayed risk distance conflicts with extracted price levels.")
            if reward_distance and abs(reward_distance - derived_reward) / max(derived_reward, 1e-9) > 0.10:
                warnings.append("Displayed reward distance conflicts with extracted price levels.")
            risk_distance = derived_risk
            reward_distance = derived_reward
        else:
            warnings.append("Extracted entry/stop/target geometry does not match the extracted direction.")

    # If exact prices are incomplete but entry + distances are legible, complete
    # the missing absolute levels deterministically.
    if entry and direction != "UNKNOWN" and risk_distance and reward_distance:
        if stop is None:
            stop = entry - risk_distance if direction == "LONG" else entry + risk_distance
        if target is None:
            target = entry + reward_distance if direction == "LONG" else entry - reward_distance
        result["stop_price"] = round(stop, 8)
        result["target_price"] = round(target, 8)
        geometry_valid = (
            stop < entry < target if direction == "LONG"
            else target < entry < stop
        )

    calculated_rr = None
    if risk_distance and reward_distance:
        calculated_rr = reward_distance / risk_distance if risk_distance else None
        if result.get("risk_reward_ratio") and calculated_rr:
            stated = result["risk_reward_ratio"]
            if abs(stated - calculated_rr) / max(calculated_rr, 1e-9) > 0.10:
                warnings.append("Displayed R:R conflicts with the extracted risk/reward distances.")

    result["direction"] = direction
    result["risk_distance"] = round(risk_distance, 8) if risk_distance else None
    result["reward_distance"] = round(reward_distance, 8) if reward_distance else None
    result["calculated_risk_reward"] = round(calculated_rr, 4) if calculated_rr else None
    result["geometry_valid"] = geometry_valid
    result["math_verified"] = calculated_rr is not None
    result["safe_to_apply"] = bool(
        result.get("image_kind") == "tradingview_position_tool"
        and direction != "UNKNOWN"
        and result["confidence"] >= 0.70
        and geometry_valid is not False
        and (
            (result.get("entry_price") and result.get("stop_price") and result.get("target_price"))
            or (risk_distance and reward_distance)
        )
    )
    result["warnings"] = warnings
    result["provider"] = "groq"
    result["model"] = RISK_PLAN_VISION_MODEL
    return result
