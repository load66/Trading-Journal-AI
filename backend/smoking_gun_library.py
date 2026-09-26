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
        for field in ("gross_pnl", "net_pnl", "commissions", "option_strike"):
            if row.get(field) is not None:
                row[field] = float(row[field])
        for field in ("id", "account_id"):
            if row.get(field) is not None:
                row[field] = int(row[field])
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


_REPORT_SUMMARY_COLUMNS = (
    "id", "account_id", "title", "date_from", "date_to", "generated_at",
    "report_version", "analytics_engine_version", "behavior_version",
    "analysis_provider", "analysis_model", "trade_count", "gross_pnl",
    "net_pnl", "primary_edge", "primary_leak", "data_fingerprint",
    "filters_json", "export_manifest_json", "status",
)

_JSON_FIELD_MAP = {
    "filters_json": "filters",
    "source_metrics_json": "source_metrics",
    "diagnosis_json": "diagnosis",
    "action_plan_json": "action_plan",
    "export_manifest_json": "export_manifest",
}

_SUPPORTED_FILTERS = {"tickers", "instrument_types"}


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _decoded_report(row, *, duplicate: bool = False) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for stored_name, public_name in _JSON_FIELD_MAP.items():
        if stored_name in result:
            result[public_name] = _json_loads(result.pop(stored_name))
    result["duplicate"] = duplicate
    return result


def _report_json_placeholder(conn) -> str:
    return "?::jsonb" if getattr(conn, "dialect", None) == "postgres" else "?"


def _select_existing_report(conn, payload: dict):
    return conn.execute(
        """SELECT * FROM smoking_gun_reports
           WHERE account_id=? AND date_from=? AND date_to=?
             AND data_fingerprint=? AND report_version=?
           ORDER BY id DESC LIMIT 1""",
        (
            payload["account_id"],
            payload["date_from"],
            payload["date_to"],
            payload["data_fingerprint"],
            payload["report_version"],
        ),
    ).fetchone()


def create_saved_report(conn, payload: dict) -> dict:
    from database import insert_and_get_id, is_integrity_error

    existing = _select_existing_report(conn, payload)
    if existing is not None:
        return _decoded_report(existing, duplicate=True)

    json_placeholder = _report_json_placeholder(conn)
    sql = f"""INSERT INTO smoking_gun_reports (
        account_id, title, date_from, date_to, report_version,
        analytics_engine_version, behavior_version, analysis_provider,
        analysis_model, trade_count, gross_pnl, net_pnl, primary_edge,
        primary_leak, data_fingerprint, filters_json, source_metrics_json,
        diagnosis_json, action_plan_json, export_manifest_json, status
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
        {json_placeholder}, {json_placeholder}, {json_placeholder},
        {json_placeholder}, {json_placeholder}, ?
    )"""
    params = (
        payload["account_id"],
        payload["title"],
        payload["date_from"],
        payload["date_to"],
        payload["report_version"],
        payload["analytics_engine_version"],
        payload["behavior_version"],
        payload.get("analysis_provider"),
        payload.get("analysis_model"),
        payload["trade_count"],
        payload.get("gross_pnl"),
        payload.get("net_pnl"),
        payload.get("primary_edge"),
        payload.get("primary_leak"),
        payload["data_fingerprint"],
        _json_dumps(payload.get("filters") or {}),
        _json_dumps(payload.get("source_metrics") or {}),
        _json_dumps(payload.get("diagnosis")) if payload.get("diagnosis") is not None else None,
        _json_dumps(payload.get("action_plan")) if payload.get("action_plan") is not None else None,
        _json_dumps(payload.get("export_manifest") or {}),
        payload.get("status") or "complete",
    )
    try:
        report_id = insert_and_get_id(conn, sql, params)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if not is_integrity_error(exc):
            raise
        existing = _select_existing_report(conn, payload)
        if existing is None:
            raise
        return _decoded_report(existing, duplicate=True)

    created = get_saved_report(conn, report_id)
    if created is None:
        raise RuntimeError("Saved Smoking Gun report could not be reloaded")
    return created


def list_saved_reports(conn, account_id: int | None = None) -> list[dict]:
    columns = ", ".join(_REPORT_SUMMARY_COLUMNS)
    sql = f"SELECT {columns} FROM smoking_gun_reports WHERE 1=1"
    params: list[Any] = []
    if account_id is not None:
        sql += " AND account_id=?"
        params.append(account_id)
    sql += " ORDER BY generated_at DESC, id DESC"
    return [
        _decoded_report(row, duplicate=False)
        for row in conn.execute(sql, params).fetchall()
    ]


def get_saved_report(conn, report_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM smoking_gun_reports WHERE id=?",
        (report_id,),
    ).fetchone()
    return _decoded_report(row, duplicate=False)


def delete_saved_report(conn, report_id: int) -> bool:
    cursor = conn.execute(
        "DELETE FROM smoking_gun_reports WHERE id=?",
        (report_id,),
    )
    deleted = cursor.rowcount > 0
    conn.commit()
    return deleted


def _normalize_scope_filters(filters: dict | None) -> dict:
    filters = filters or {}
    unknown = set(filters) - _SUPPORTED_FILTERS
    if unknown:
        raise ValueError(
            "Unsupported Smoking Gun filters: " + ", ".join(sorted(unknown))
        )

    normalized: dict[str, list[str]] = {}
    tickers = filters.get("tickers") or []
    instruments = filters.get("instrument_types") or []
    if tickers:
        normalized["tickers"] = sorted({
            str(value).strip().upper() for value in tickers if str(value).strip()
        })
    if instruments:
        normalized["instrument_types"] = sorted({
            str(value).strip().upper() for value in instruments if str(value).strip()
        })
    return normalized


def _source_trades_for_range(
    conn,
    account_id: int,
    date_from: str,
    date_to: str,
    filters: dict | None = None,
) -> tuple[list[dict], dict]:
    normalized = _normalize_scope_filters(filters)
    sql = """SELECT id, account_id, trade_group, date, ticker, instrument_type,
                    side, gross_pnl, net_pnl, commissions, executions,
                    option_expiry, option_strike, option_type, source
             FROM trades
             WHERE account_id=? AND date>=? AND date<=?"""
    params: list[Any] = [account_id, date_from, date_to]

    tickers = normalized.get("tickers") or []
    if tickers:
        sql += " AND ticker IN (" + ",".join("?" for _ in tickers) + ")"
        params.extend(tickers)

    instruments = normalized.get("instrument_types") or []
    if instruments:
        sql += " AND instrument_type IN (" + ",".join("?" for _ in instruments) + ")"
        params.extend(instruments)

    sql += " ORDER BY date, trade_group, id"
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    return rows, normalized


def current_fingerprint_for_range(
    conn,
    account_id: int,
    date_from: str,
    date_to: str,
    filters: dict | None = None,
) -> str:
    rows, normalized = _source_trades_for_range(
        conn, account_id, date_from, date_to, filters
    )
    return build_source_fingerprint(
        rows, account_id, date_from, date_to, normalized
    )


def decorate_stale_status(conn, reports: list[dict]) -> list[dict]:
    cache: dict[tuple[Any, ...], str] = {}
    decorated: list[dict] = []

    for report in reports:
        filters = _normalize_scope_filters(report.get("filters") or {})
        filter_key = _json_dumps(filters)
        scope = (
            report["account_id"],
            report["date_from"],
            report["date_to"],
            filter_key,
        )
        if scope not in cache:
            cache[scope] = current_fingerprint_for_range(
                conn,
                report["account_id"],
                report["date_from"],
                report["date_to"],
                filters,
            )

        current = cache[scope]
        empty_fingerprint = build_source_fingerprint(
            [],
            report["account_id"],
            report["date_from"],
            report["date_to"],
            filters,
        )
        row = dict(report)
        if current == report.get("data_fingerprint"):
            row["is_stale"] = False
            row["stale_reason"] = None
        elif current == empty_fingerprint:
            row["is_stale"] = True
            row["stale_reason"] = "source-data-missing"
        else:
            row["is_stale"] = True
            row["stale_reason"] = "source-data-changed"
        decorated.append(row)

    return decorated
