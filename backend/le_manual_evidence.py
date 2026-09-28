from __future__ import annotations

import re
from typing import Any


AUTHORITATIVE_SOURCE = "USER_MANUAL"


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text


def _tag_key(tag_type: Any, tag_value: Any) -> tuple[str, str]:
    return _norm(tag_type), _norm(tag_value)


CHECK_TAG_ASSERTIONS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("mistake", "no market sign"): (
        "market_sign", "fail",
        "User manually confirmed that SPY/QQQ Market Sign was missing.",
    ),
    ("setup", "market sign"): (
        "market_sign", "pass",
        "User manually confirmed SPY/QQQ Market Sign.",
    ),
    ("mistake", "traded chop"): (
        "not_chop_hour", "fail",
        "User manually confirmed this was a Chop Hour / chop trade.",
    ),
    ("setup", "outside chop hour"): (
        "not_chop_hour", "pass",
        "User manually confirmed the trade was outside Chop Hour.",
    ),
    ("mistake", "chased entry"): (
        "not_chasing", "fail",
        "User manually confirmed the entry was chased.",
    ),
    ("mistake", "chasing entry"): (
        "not_chasing", "fail",
        "User manually confirmed the entry was chased.",
    ),
    ("setup", "not chasing"): (
        "not_chasing", "pass",
        "User manually confirmed the entry was not chased.",
    ),
    ("setup", "flag forming"): (
        "flag_forming", "pass",
        "User manually confirmed a valid LE flag / controlled pullback.",
    ),
    ("setup", "valid flag"): (
        "flag_forming", "pass",
        "User manually confirmed a valid LE flag / controlled pullback.",
    ),
    ("mistake", "no flag"): (
        "flag_forming", "fail",
        "User manually confirmed there was no valid LE flag.",
    ),
    ("setup", "ema snug"): (
        "ema_snug", "pass",
        "User manually confirmed price was snug to the 10-minute 8 EMA.",
    ),
    ("mistake", "airgapped"): (
        "ema_snug", "fail",
        "User manually confirmed price was airgapped from the 10-minute 8 EMA.",
    ),
    ("mistake", "ema extended"): (
        "ema_snug", "fail",
        "User manually confirmed price was extended from the 10-minute 8 EMA.",
    ),
    ("execution", "hard stop set"): (
        "hard_stop_set", "pass",
        "User manually confirmed a hard stop was set before entry.",
    ),
    ("mistake", "no hard stop"): (
        "hard_stop_set", "fail",
        "User manually confirmed a hard stop was not set.",
    ),
    ("execution", "vix checked"): (
        "vix_checked", "pass",
        "User manually confirmed VIX was checked.",
    ),
    ("mistake", "vix not checked"): (
        "vix_checked", "fail",
        "User manually confirmed VIX was not checked.",
    ),
    ("execution", "r:r >= 2:1"): (
        "risk_reward", "pass",
        "User manually confirmed planned reward/risk was at least 2:1.",
    ),
    ("execution", "rr >= 2:1"): (
        "risk_reward", "pass",
        "User manually confirmed planned reward/risk was at least 2:1.",
    ),
    ("mistake", "r:r < 2:1"): (
        "risk_reward", "fail",
        "User manually confirmed planned reward/risk was below 2:1.",
    ),
    ("mistake", "rr < 2:1"): (
        "risk_reward", "fail",
        "User manually confirmed planned reward/risk was below 2:1.",
    ),
}


EXTRA_TAG_ASSERTIONS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("mistake", "sold too early"): (
        "hold_the_line", "fail",
        "User manually confirmed the trade was exited too early.",
    ),
    ("mistake", "sell all too early and didn't wait until 8 ema break"): (
        "hold_the_line", "fail",
        "User manually confirmed the position was fully exited before the 10-minute 8 EMA break.",
    ),
    ("mistake", "sell all too early and didn’t wait until 8 ema break"): (
        "hold_the_line", "fail",
        "User manually confirmed the position was fully exited before the 10-minute 8 EMA break.",
    ),
    ("execution", "held until 8 ema break"): (
        "hold_the_line", "pass",
        "User manually confirmed the runner was held until the 10-minute 8 EMA break.",
    ),
}


LEVEL_TAGS = {
    ("setup", "pdh break"): "PDH",
    ("setup", "pmh break"): "PMH",
    ("setup", "pdl break"): "PDL",
    ("setup", "pml break"): "PML",
}


MANUAL_SETUP_TAGS = {
    ("setup", "outside day"),
    ("setup", "a+ pivot level + 8 ema 10m bounce + vwap reclaim"),
}


EMA_BOUNCE_SETUP = ("setup", "a+ pivot level + 8 ema 10m bounce + vwap reclaim")


def is_manual_le_tag(tag_type: Any, tag_value: Any, source: Any = "manual") -> bool:
    if _norm(source) != "manual":
        return False
    key = _tag_key(tag_type, tag_value)
    return (
        key in CHECK_TAG_ASSERTIONS
        or key in EXTRA_TAG_ASSERTIONS
        or key in LEVEL_TAGS
        or key in MANUAL_SETUP_TAGS
    )


def _tag_payload(tag: dict) -> dict:
    return {
        "id": tag.get("id"),
        "tag_type": tag.get("tag_type"),
        "tag_value": tag.get("tag_value"),
        "source": tag.get("source"),
    }


def build_manual_le_evidence(tags: list[dict] | None, trade: dict) -> dict:
    """Translate recognized manual LE tags into authoritative user assertions.

    Only exact, registered tag meanings are applied. Unrecognized tags remain normal
    journal metadata and never change compliance.
    """
    manual = [
        tag for tag in (tags or [])
        if _norm(tag.get("source")) == "manual"
    ]

    recognized: list[dict] = []
    check_overrides: dict[str, dict] = {}
    extra_overrides: dict[str, dict] = {}
    setup_tags: list[str] = []
    level_names: set[str] = set()
    level_tag_payloads: list[dict] = []

    for tag in manual:
        key = _tag_key(tag.get("tag_type"), tag.get("tag_value"))
        payload = _tag_payload(tag)

        assertion = CHECK_TAG_ASSERTIONS.get(key)
        if assertion:
            check_id, status, detail = assertion
            check_overrides[check_id] = {
                "status": status,
                "detail": detail,
                "tag": payload,
            }
            recognized.append(payload)

        assertion = EXTRA_TAG_ASSERTIONS.get(key)
        if assertion:
            finding_id, status, detail = assertion
            extra_overrides[finding_id] = {
                "status": status,
                "detail": detail,
                "tag": payload,
            }
            recognized.append(payload)

        level = LEVEL_TAGS.get(key)
        if level:
            level_names.add(level)
            level_tag_payloads.append(payload)
            recognized.append(payload)

        if key in MANUAL_SETUP_TAGS:
            setup_tags.append(str(tag.get("tag_value") or "").strip())
            recognized.append(payload)

        if key == EMA_BOUNCE_SETUP:
            check_overrides["ema_snug"] = {
                "status": "pass",
                "detail": (
                    "User manually confirmed an A+ setup with a 10-minute 8 EMA bounce "
                    "and VWAP reclaim."
                ),
                "tag": payload,
            }

        if key == ("setup", "outside day"):
            side = _norm(trade.get("side"))
            if side == "short":
                level_names.update({"PDL", "PML"})
            else:
                level_names.update({"PDH", "PMH"})

    if level_names:
        order = ["PDH", "PMH", "PDL", "PML"]
        detail = ", ".join(level for level in order if level in level_names)
        check_overrides["level_broken"] = {
            "status": "pass",
            "detail": f"User manually confirmed directional level break(s): {detail}.",
            "display_detail": detail,
            "tags": level_tag_payloads,
        }

    # De-duplicate recognized tags by ID when available, otherwise by semantic key.
    deduped = []
    seen = set()
    for tag in recognized:
        marker = tag.get("id")
        if marker is None:
            marker = (_norm(tag.get("tag_type")), _norm(tag.get("tag_value")))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(tag)

    return {
        "source": AUTHORITATIVE_SOURCE,
        "authoritative": True,
        "recognized_tags": deduped,
        "setup_tags": sorted(set(setup_tags), key=str.lower),
        "check_overrides": check_overrides,
        "extra_overrides": extra_overrides,
    }


def _apply_override(item: dict, override: dict) -> dict:
    previous = {
        "status": item.get("status"),
        "detail": item.get("detail"),
        "evidence": item.get("evidence") or {},
    }
    final = dict(item)
    final["status"] = override["status"]
    final["detail"] = override.get("display_detail") or override.get("detail") or item.get("detail")
    final["system_result"] = previous
    final["manual_override"] = True
    final["authoritative_source"] = AUTHORITATIVE_SOURCE
    final["conflict_with_system"] = (
        previous.get("status") not in (None, "", "unknown")
        and previous.get("status") != override.get("status")
    )
    final["evidence"] = {
        **(item.get("evidence") or {}),
        "manual_user_evidence": {
            "authoritative": True,
            "source": AUTHORITATIVE_SOURCE,
            "detail": override.get("detail"),
            "tag": override.get("tag"),
            "tags": override.get("tags"),
        },
    }
    return final


def apply_manual_le_evidence(
    checks: list[dict],
    extra_findings: list[dict],
    manual_evidence: dict | None,
) -> tuple[list[dict], list[dict], dict]:
    evidence = manual_evidence or {}
    check_overrides = evidence.get("check_overrides") or {}
    extra_overrides = evidence.get("extra_overrides") or {}

    final_checks = [
        _apply_override(item, check_overrides[item.get("id")])
        if item.get("id") in check_overrides
        else item
        for item in checks
    ]
    final_extras = [
        _apply_override(item, extra_overrides[item.get("id")])
        if item.get("id") in extra_overrides
        else item
        for item in extra_findings
    ]

    overridden = [
        item for item in [*final_checks, *final_extras]
        if item.get("manual_override")
    ]
    summary = {
        "source": AUTHORITATIVE_SOURCE,
        "authoritative": True,
        "recognized_tags": evidence.get("recognized_tags") or [],
        "setup_tags": evidence.get("setup_tags") or [],
        "override_count": len(overridden),
        "conflict_count": sum(bool(item.get("conflict_with_system")) for item in overridden),
        "overridden_ids": [item.get("id") for item in overridden],
    }
    return final_checks, final_extras, summary
