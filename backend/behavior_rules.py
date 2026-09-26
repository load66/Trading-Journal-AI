"""Deterministic daily behavioral rules.

These rules describe observable execution patterns only. They intentionally do
not infer motive or psychology. A same-ticker entry after a loss is called a
"loss re-entry", not "revenge trading".
"""
from __future__ import annotations

import json
from datetime import datetime


def _execs(trade: dict) -> list[dict]:
    raw = trade.get("executions") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def _dt(e: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(f"{e.get('date')}T{e.get('time')}")
    except Exception:
        return None


def enrich_trade(trade: dict) -> dict:
    t = dict(trade)
    side = str(t.get("side") or "").upper()
    ea = "BOT" if side == "LONG" else "SOLD"
    xa = "SOLD" if side == "LONG" else "BOT"
    rows = _execs(t)
    entries = sorted(
        [e for e in rows if str(e.get("action") or "").upper() == ea and _dt(e)],
        key=_dt,
    )
    exits = sorted(
        [e for e in rows if str(e.get("action") or "").upper() == xa and _dt(e)],
        key=_dt,
    )
    t["entries"] = entries
    t["exits"] = exits
    t["entry_dt"] = _dt(entries[0]) if entries else None
    t["exit_dt"] = _dt(exits[-1]) if exits else None
    t["pnl"] = float(t.get("net_pnl") or 0)

    qty = sum(float(e.get("qty") or 0) for e in entries)
    weighted = sum(float(e.get("qty") or 0) * float(e.get("price") or 0) for e in entries)
    avg = weighted / qty if qty else None
    inst = str(t.get("instrument_type") or "STOCK").upper()
    if inst == "OPTION":
        t["size_family"] = "OPTION"
        t["size_value"] = qty
    elif inst == "FUTURE":
        t["size_family"] = "FUTURE"
        t["size_value"] = qty
    else:
        t["size_family"] = "STOCK"
        t["size_value"] = qty * avg if qty and avg else None
    return t


def averaging_down(t: dict) -> bool:
    entries = t.get("entries") or []
    if len(entries) < 2:
        return False
    side = str(t.get("side") or "").upper()
    qty = float(entries[0].get("qty") or 0)
    cost = qty * float(entries[0].get("price") or 0)
    if qty <= 0:
        return False
    for e in entries[1:]:
        price = float(e.get("price") or 0)
        avg = cost / qty
        if side == "LONG" and price < avg:
            return True
        if side == "SHORT" and price > avg:
            return True
        add_qty = float(e.get("qty") or 0)
        if add_qty > 0:
            cost += add_qty * price
            qty += add_qty
    return False


def detect_daily_flags(trades: list[dict], historical_daily_counts: list[int] | None = None) -> list[dict]:
    rows = [enrich_trade(t) for t in trades]
    rows = sorted(rows, key=lambda r: r.get("entry_dt") or datetime.max)
    flags = []

    def add(code, title, detail, row=None, severity="medium", metric=None):
        flags.append({
            "code": code,
            "title": title,
            "detail": detail,
            "evidence": "VERIFIED",
            "severity": severity,
            "trade_group": row.get("trade_group") if row else None,
            "ticker": row.get("ticker") if row else None,
            "observed_pnl": round(float(row.get("pnl") or 0), 2) if row else None,
            "metric": metric,
        })

    for r in rows:
        if averaging_down(r):
            add(
                "averaging_down",
                "Added against the position",
                f"{r.get('ticker')} added after price moved against the running average entry.",
                r,
                "high",
            )

    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r.get("ticker"), []).append(r)

    for ticker_rows in by_ticker.values():
        loss_seen = False
        loss_reentry_depth = 0
        prev = None
        for r in ticker_rows:
            if loss_seen:
                loss_reentry_depth += 1
                add(
                    "loss_reentry",
                    "Same-ticker re-entry after a loss",
                    f"{r.get('ticker')} was re-entered after a realized loss earlier that day (depth {loss_reentry_depth}).",
                    r,
                    "high" if loss_reentry_depth >= 2 else "medium",
                    {"depth": loss_reentry_depth},
                )
            if prev and prev.get("exit_dt") and r.get("entry_dt"):
                gap = (r["entry_dt"] - prev["exit_dt"]).total_seconds()
                if 0 <= gap <= 30:
                    add(
                        "rapid_reentry",
                        "Rapid same-ticker re-entry",
                        f"{r.get('ticker')} was re-entered {int(gap)} seconds after the previous exit.",
                        r,
                        "medium",
                        {"gap_seconds": int(gap)},
                    )
            if r.get("pnl", 0) < 0:
                loss_seen = True
            prev = r

    for prev, cur in zip(rows, rows[1:]):
        pv, cv = prev.get("size_value"), cur.get("size_value")
        if (
            prev.get("pnl", 0) < 0
            and pv and cv
            and prev.get("size_family") == cur.get("size_family")
            and cv >= pv * 1.5
        ):
            multiple = cv / pv
            add(
                "size_escalation_after_loss",
                "Size increased after a loss",
                f"Position size increased to {multiple:.2f}× the prior losing trade's size.",
                cur,
                "high",
                {"multiple": round(multiple, 2)},
            )

    streak = 0
    for r in rows:
        if streak >= 3:
            add(
                "continued_after_3_losses",
                "Continued after three consecutive losses",
                f"{r.get('ticker')} was entered after a three-loss streak was already established.",
                r,
                "high",
                {"prior_loss_streak": streak},
            )
        streak = streak + 1 if r.get("pnl", 0) < 0 else 0

    # Personalized high-trade-count day: only activate with at least 10 prior
    # sessions so the threshold is based on the trader's own history.
    counts = sorted(int(x) for x in (historical_daily_counts or []) if int(x) >= 0)
    if len(counts) >= 10 and rows:
        idx = max(0, min(len(counts) - 1, int(round(0.90 * (len(counts) - 1)))))
        p90 = counts[idx]
        if len(rows) > p90:
            add(
                "high_trade_count",
                "Trade count exceeded your historical 90th percentile",
                f"{len(rows)} trades were taken; your prior-session 90th percentile is {p90}.",
                None,
                "medium",
                {"trade_count": len(rows), "historical_p90": p90},
            )

    seen = set()
    out = []
    for f in flags:
        key = (f["code"], f.get("trade_group"))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def deterministic_strengths(trades: list[dict], day_kpis: dict) -> list[dict]:
    rows = [enrich_trade(t) for t in trades]
    observations = []
    total = float(day_kpis.get("total_net_pnl") or 0)
    pf = day_kpis.get("profit_factor")
    avg_win = float(day_kpis.get("avg_win") or 0)
    avg_loss = abs(float(day_kpis.get("avg_loss") or 0))

    if total > 0:
        observations.append({
            "text": f"Finished the day USD {total:,.2f} net after commissions.",
            "evidence": "VERIFIED",
        })
    if pf is not None and float(pf) >= 1.5:
        observations.append({
            "text": f"Net profit factor was {float(pf):.2f}, so winning dollars materially exceeded losing dollars.",
            "evidence": "VERIFIED",
        })
    if avg_loss > 0 and avg_win > avg_loss:
        ratio = avg_win / avg_loss
        observations.append({
            "text": f"Average winner (USD {avg_win:,.2f}) was {ratio:.2f}× the average loss (USD {avg_loss:,.2f}).",
            "evidence": "VERIFIED",
        })
    effs = [
        float(t.get("exit_efficiency"))
        for t in trades
        if t.get("exit_efficiency") is not None and float(t.get("net_pnl") or 0) > 0
    ]
    if effs:
        avg_eff = sum(effs) / len(effs)
        if avg_eff >= 50:
            observations.append({
                "text": f"Average winner directional exit efficiency was {avg_eff:.0f}%.",
                "evidence": "VERIFIED",
            })

    if rows:
        best = max(rows, key=lambda r: r.get("pnl", 0))
        if best.get("pnl", 0) > 0:
            observations.append({
                "text": f"Largest realized winner was {best.get('ticker')} at USD {best.get('pnl'):,.2f}.",
                "evidence": "VERIFIED",
            })
    return observations[:4]


def recorded_observations(trades: list[dict], diary_summary: dict | None) -> list[dict]:
    out = []
    if diary_summary:
        text = diary_summary.get("overall_summary")
        if text:
            out.append({"text": text, "evidence": "RECORDED"})
    emotions = sorted({
        str(t.get("emotional_state")).strip()
        for t in trades
        if (t.get("emotional_state") or "").strip()
    })
    if emotions:
        out.append({
            "text": "Recorded emotional state: " + ", ".join(emotions),
            "evidence": "RECORDED",
        })
    return out
