from __future__ import annotations

import hashlib
import json
from typing import Any


REPORT_SCHEMA_VERSION = "1"
ANALYTICS_ENGINE_VERSION = "2026.09.26.1"
BEHAVIOR_VERSION = "2026.09.26.1"

_FINGERPRINT_FIELDS = (
    "id",
    "account_id",
    "trade_group",
    "date",
    "ticker",
    "instrument_type",
    "side",
    "gross_pnl",
    "net_pnl",
    "commissions",
    "executions",
    "option_expiry",
    "option_strike",
    "option_type",
    "source",
)


def _parse_executions(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value if value is not None else []


def _canonical_filter_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical_filter_value(value[key])
            for key in sorted(value)
        }
    if isinstance(value, list):
        normalized = [_canonical_filter_value(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        )
    return value


def build_source_fingerprint(
    trades: list[dict],
    account_id: int | None,
    date_from: str | None,
    date_to: str | None,
    filters: dict | None = None,
) -> str:
    canonical_trades = []
    for trade in trades:
        row = {field: trade.get(field) for field in _FINGERPRINT_FIELDS}
        row["executions"] = _parse_executions(row.get("executions"))
        canonical_trades.append(row)

    canonical_trades.sort(
        key=lambda row: (
            row.get("account_id") if row.get("account_id") is not None else -1,
            str(row.get("date") or ""),
            str(row.get("trade_group") or ""),
            row.get("id") if row.get("id") is not None else -1,
        )
    )

    payload = {
        "account_id": account_id,
        "date_from": date_from,
        "date_to": date_to,
        "filters": _canonical_filter_value(filters or {}),
        "trades": canonical_trades,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
