from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from database import get_db
from smoking_gun_exports import render_saved_report_html, render_trade_ledger_csv
from smoking_gun_library import (
    build_source_fingerprint,
    create_saved_report,
    decorate_stale_status,
    delete_saved_report,
    get_saved_report,
    list_saved_reports,
    load_source_trades_for_range,
)


router = APIRouter(prefix="/api/smoking-gun-reports", tags=["smoking-gun-reports"])


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


class SmokingGunReportCreate(BaseModel):
    account_id: int
    title: str = Field(min_length=1, max_length=160)
    date_from: str
    date_to: str
    report_version: str
    analytics_engine_version: str
    behavior_version: str
    analysis_provider: str | None = None
    analysis_model: str | None = None
    data_fingerprint: str = Field(min_length=16, max_length=128)
    filters: dict[str, Any] = Field(default_factory=dict)
    source_metrics: dict[str, Any]
    diagnosis: dict[str, Any] | None = None
    action_plan: list[dict[str, Any]] | dict[str, Any] | None = None
    export_manifest: dict[str, Any] = Field(default_factory=dict)
    primary_edge: str | None = None
    primary_leak: str | None = None
    status: str = "complete"


def _current_scope(conn, body: SmokingGunReportCreate):
    rows, normalized_filters = load_source_trades_for_range(
        conn,
        body.account_id,
        body.date_from,
        body.date_to,
        body.filters,
    )
    if not rows:
        raise HTTPException(
            status_code=400,
            detail="Cannot save a Smoking Gun report with an empty source population.",
        )
    current_fingerprint = build_source_fingerprint(
        rows,
        body.account_id,
        body.date_from,
        body.date_to,
        normalized_filters,
    )
    if current_fingerprint != body.data_fingerprint:
        raise HTTPException(
            status_code=409,
            detail=(
                "Source fingerprint no longer matches current journal data. "
                "Regenerate the report before saving."
            ),
        )
    return rows, normalized_filters


def _report_payload(body: SmokingGunReportCreate, rows, normalized_filters):
    gross_pnl = 0.0
    net_pnl = 0.0
    for row in rows:
        net = float(row.get("net_pnl") or 0)
        commissions = float(row.get("commissions") or 0)
        gross = row.get("gross_pnl")
        gross_pnl += float(gross) if gross is not None else net + commissions
        net_pnl += net

    return {
        "account_id": body.account_id,
        "title": body.title.strip(),
        "date_from": body.date_from,
        "date_to": body.date_to,
        "report_version": body.report_version,
        "analytics_engine_version": body.analytics_engine_version,
        "behavior_version": body.behavior_version,
        "analysis_provider": body.analysis_provider,
        "analysis_model": body.analysis_model,
        "trade_count": len(rows),
        "gross_pnl": round(gross_pnl, 2),
        "net_pnl": round(net_pnl, 2),
        "primary_edge": body.primary_edge,
        "primary_leak": body.primary_leak,
        "data_fingerprint": body.data_fingerprint,
        "filters": normalized_filters,
        "source_metrics": body.source_metrics,
        "diagnosis": body.diagnosis,
        "action_plan": body.action_plan,
        "export_manifest": body.export_manifest or {"format_version": 1},
        "status": body.status,
    }


@router.post("")
def save_smoking_gun_report(
    body: SmokingGunReportCreate,
    conn=Depends(get_connection),
):
    rows, normalized_filters = _current_scope(conn, body)
    saved = create_saved_report(
        conn,
        _report_payload(body, rows, normalized_filters),
    )
    return Response(
        content=__import__("json").dumps(saved),
        media_type="application/json",
        status_code=200 if saved.get("duplicate") else 201,
    )


@router.get("")
def get_smoking_gun_reports(
    account_id: int | None = Query(None),
    conn=Depends(get_connection),
):
    reports = list_saved_reports(conn, account_id=account_id)
    return decorate_stale_status(conn, reports)


@router.get("/{report_id}")
def get_smoking_gun_report_snapshot(
    report_id: int,
    conn=Depends(get_connection),
):
    report = get_saved_report(conn, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Smoking Gun report not found.")
    return decorate_stale_status(conn, [report])[0]


@router.delete("/{report_id}", status_code=204)
def remove_smoking_gun_report(
    report_id: int,
    conn=Depends(get_connection),
):
    if not delete_saved_report(conn, report_id):
        raise HTTPException(status_code=404, detail="Smoking Gun report not found.")
    return Response(status_code=204)


@router.get("/{report_id}/export")
def export_smoking_gun_report(
    report_id: int,
    format: str = Query("html", pattern="^(html|csv)$"),
    conn=Depends(get_connection),
):
    report = get_saved_report(conn, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Smoking Gun report not found.")
    report = decorate_stale_status(conn, [report])[0]

    if format == "csv":
        return PlainTextResponse(
            render_trade_ledger_csv(report),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="smoking-gun-report-{report_id}-ledger.csv"'
                )
            },
        )

    return HTMLResponse(
        render_saved_report_html(report),
        headers={
            "Content-Disposition": (
                f'attachment; filename="smoking-gun-report-{report_id}.html"'
            )
        },
    )
