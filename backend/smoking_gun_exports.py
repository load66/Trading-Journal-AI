from __future__ import annotations

import csv
import html
import io
from typing import Any


LEDGER_COLUMNS = (
    "trade_group", "date", "ticker", "instrument_type", "side",
    "entry_time", "exit_time", "hold_sec", "entry_size", "gross_pnl",
    "commissions", "net_pnl", "hold_bucket", "size_bucket",
)


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _money(value: Any) -> str:
    if value is None:
        return "—"
    return f"${float(value):,.2f}"


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def _safe_csv_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_trade_ledger_csv(report: dict) -> str:
    ledger = (report.get("source_metrics") or {}).get("trade_ledger") or []
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=LEDGER_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in ledger:
        writer.writerow({key: _safe_csv_cell(row.get(key)) for key in LEDGER_COLUMNS})
    return stream.getvalue()


def _stats_table(rows: list[dict], first_label: str = "Bucket") -> str:
    if not rows:
        return '<p class="muted">No data.</p>'
    body = []
    for row in rows:
        label = row.get("bucket") or row.get("ticker") or row.get("name") or "—"
        body.append(
            "<tr>"
            f"<td>{_esc(label)}</td>"
            f"<td>{_esc(row.get('trade_count', 0))}</td>"
            f"<td>{_money(row.get('total_pnl'))}</td>"
            f"<td>{_pct(row.get('win_rate'))}</td>"
            f"<td>{_money(row.get('avg_pnl'))}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        f"<th>{_esc(first_label)}</th><th>Trades</th><th>Total P&amp;L</th>"
        "<th>Win rate</th><th>Avg P&amp;L</th></tr></thead><tbody>"
        + "".join(body) + "</tbody></table>"
    )


def _bullets(items: list[Any]) -> str:
    if not items:
        return '<p class="muted">No items.</p>'
    return "<ul>" + "".join(f"<li>{_esc(item)}</li>" for item in items) + "</ul>"


def render_saved_report_html(report: dict) -> str:
    metrics = report.get("source_metrics") or {}
    board = metrics.get("scoreboard") or {}
    two = metrics.get("two_traders") or {}
    diagnosis = report.get("diagnosis") or {}
    action_plan = report.get("action_plan") or diagnosis.get("action_plan") or []
    disciplined = two.get("disciplined") or {}
    destructive = two.get("destructive") or {}
    edge = diagnosis.get("edge") or {}

    plan_rows = []
    for item in action_plan:
        plan_rows.append(
            "<tr>"
            f"<td>{_esc(item.get('priority') or item.get('rank') or '')}</td>"
            f"<td>{_esc(item.get('rule') or item.get('mechanical_rule') or '')}</td>"
            f"<td>{_esc(item.get('why') or item.get('evidence') or '')}</td>"
            "</tr>"
        )

    title = _esc(report.get("title") or "Smoking Gun Report")
    stale = "STALE SNAPSHOT" if report.get("is_stale") else "SAVED SNAPSHOT"
    plan_html = "".join(plan_rows) if plan_rows else '<tr><td colspan="3">No saved action plan.</td></tr>'

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#090d14;--panel:#101722;--panel2:#151f2d;--text:#edf3fb;--muted:#91a0b5;--line:#243247;--accent:#7bb6ff}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:auto;padding:32px 22px 64px}}h1{{font-size:34px;margin:6px 0}}h2{{margin:34px 0 12px}}h3{{margin:18px 0 8px}}
.eyebrow{{color:var(--accent);font-weight:800;letter-spacing:.12em}}.muted{{color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}}.kpi b{{display:block;font-size:24px;margin-top:4px}}
.split{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}.good{{border-color:#285741}}.bad{{border-color:#6d3535}}
table{{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line)}}th,td{{padding:9px 10px;border-bottom:1px solid var(--line);text-align:right}}th:first-child,td:first-child{{text-align:left}}th{{color:var(--muted);font-size:12px;text-transform:uppercase}}
.note{{border-left:3px solid var(--accent);padding:10px 14px;background:var(--panel2)}}ul{{padding-left:20px}}
</style></head><body><main>
<div class="eyebrow">SMOKING GUN REPORT · {stale}</div>
<h1>{title}</h1>
<p class="muted">{_esc(report.get("date_from"))} → {_esc(report.get("date_to"))} · Generated {_esc(report.get("generated_at"))}</p>

<h2>DATA · Executive Scoreboard</h2>
<div class="grid">
<div class="card kpi">Net P&amp;L<b>{_money(board.get("net_pnl"))}</b></div>
<div class="card kpi">Gross P&amp;L<b>{_money(board.get("gross_pnl"))}</b></div>
<div class="card kpi">Fees<b>{_money(board.get("fees"))}</b></div>
<div class="card kpi">Win rate<b>{_pct(board.get("win_rate"))}</b></div>
<div class="card kpi">Profit factor<b>{_esc(board.get("profit_factor") if board.get("profit_factor") is not None else "—")}</b></div>
<div class="card kpi">Max drawdown<b>{_money(board.get("max_drawdown"))}</b></div>
</div>

<h2>Two Traders</h2>
<div class="split">
<div class="card good"><h3>Disciplined cohort</h3><b>{_money(disciplined.get("total_pnl"))}</b><p>{_esc(disciplined.get("trade_count",0))} trades · {_pct(disciplined.get("win_rate"))}</p></div>
<div class="card bad"><h3>Destructive cohort</h3><b>{_money(destructive.get("total_pnl"))}</b><p>{_esc(destructive.get("trade_count",0))} trades · {_pct(destructive.get("win_rate"))}</p></div>
</div>

<h2>Hold-Time Edge</h2>
{_stats_table(metrics.get("hold_time") or [])}

<h2>DIAGNOSIS</h2>
<div class="note"><strong>{_esc(diagnosis.get("headline") or "No AI diagnosis saved.")}</strong></div>
<h3>Where the edge lives</h3>{_bullets(edge.get("where_it_lives") or [])}
<h3>Where it dies</h3>{_bullets(edge.get("where_it_dies") or [])}

<h2>FIX · Mechanical Action Plan</h2>
<table><thead><tr><th>Priority</th><th>Rule</th><th>Why</th></tr></thead><tbody>{plan_html}</tbody></table>

<h2>Audit Metadata</h2>
<p class="muted">Report v{_esc(report.get("report_version"))} · Analytics {_esc(report.get("analytics_engine_version"))} · Behavior {_esc(report.get("behavior_version"))} · Fingerprint {_esc(report.get("data_fingerprint"))}</p>
</main></body></html>"""
