from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from math import inf
from statistics import mean, median


HOLD_BUCKETS = [
    ("Under 30 sec", 0, 30),
    ("30s-1min", 30, 60),
    ("1-2min", 60, 120),
    ("2-5min", 120, 300),
    ("5-10min", 300, 600),
    ("10-15min", 600, 900),
    ("15-20min", 900, 1200),
    ("20-30min", 1200, 1800),
    ("30-60min", 1800, 3600),
    ("60min+", 3600, inf),
]

OPTION_SIZE_BUCKETS = [
    ("1-3", 1, 4), ("4-5", 4, 6), ("6-10", 6, 11), ("11-15", 11, 16),
    ("16-20", 16, 21), ("21-25", 21, 26), ("26-30", 26, 31),
    ("31-50", 31, 51), ("50+", 51, inf),
]

SHARE_NOTIONAL_BUCKETS = [
    ("<$2.5K", 0, 2500), ("$2.5K-$5K", 2500, 5000),
    ("$5K-$10K", 5000, 10000), ("$10K-$25K", 10000, 25000),
    ("$25K-$50K", 25000, 50000), ("$50K+", 50000, inf),
]

DAY_COUNT_BUCKETS = [
    ("1-10", 1, 11), ("11-20", 11, 21), ("21-30", 21, 31),
    ("31-50", 31, 51), ("50+", 51, inf),
]


def _parse_time(date_str: str, time_str: str):
    if not date_str or not time_str:
        return None
    raw = time_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p"):
        try:
            return datetime.strptime(f"{date_str} {raw}", fmt)
        except ValueError:
            continue
    return None


def _load_execs(trade):
    try:
        return json.loads(trade.get("executions") or "[]")
    except Exception:
        return []


def _entry_exit_actions(side):
    return ("BOT", "SOLD") if str(side).upper() == "LONG" else ("SOLD", "BOT")


def enrich_trade(trade):
    t = dict(trade)
    execs = _load_execs(t)
    entry_action, exit_action = _entry_exit_actions(t.get("side"))
    entries = [e for e in execs if e.get("action") == entry_action]
    exits = [e for e in execs if e.get("action") == exit_action]

    def dt(e):
        return _parse_time(e.get("date") or t.get("date"), e.get("time"))

    entries = sorted(entries, key=lambda e: dt(e) or datetime.max)
    exits = sorted(exits, key=lambda e: dt(e) or datetime.max)
    entry_dt = dt(entries[0]) if entries else None
    exit_dt = dt(exits[-1]) if exits else None
    hold_sec = (exit_dt - entry_dt).total_seconds() if entry_dt and exit_dt else None
    if hold_sec is not None and hold_sec < 0:
        hold_sec = None

    entry_qty = sum(float(e.get("qty") or 0) for e in entries)
    avg_entry = None
    if entry_qty > 0:
        avg_entry = sum(float(e.get("qty") or 0) * float(e.get("price") or 0) for e in entries) / entry_qty

    qty_bot = sum(float(e.get("qty") or 0) for e in execs if e.get("action") == "BOT")
    qty_sold = sum(float(e.get("qty") or 0) for e in execs if e.get("action") == "SOLD")
    is_open = abs(qty_bot - qty_sold) > 1e-6

    t.update({
        "execs": execs,
        "entries": entries,
        "exits": exits,
        "entry_dt": entry_dt,
        "exit_dt": exit_dt,
        "hold_sec": hold_sec,
        "entry_qty": entry_qty,
        "avg_entry": avg_entry,
        "entry_notional": entry_qty * (avg_entry or 0),
        "pnl": float(t.get("net_pnl") or 0),
        "is_open": is_open,
        "open_quantity": round(abs(qty_bot - qty_sold), 6),
    })
    return t


def _bucket_label(value, defs):
    if value is None:
        return None
    for label, lo, hi in defs:
        if lo <= value < hi:
            return label
    return defs[-1][0]


def _stats(rows):
    if not rows:
        return {"trade_count": 0, "total_pnl": 0.0, "win_rate": 0.0, "avg_pnl": 0.0}
    pnls = [r["pnl"] for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trade_count": len(rows),
        "total_pnl": round(sum(pnls), 2),
        "win_rate": round(wins / len(rows) * 100, 1),
        "avg_pnl": round(mean(pnls), 2),
    }


def _group_stats(rows, key_fn, order=None):
    groups = defaultdict(list)
    for row in rows:
        key = key_fn(row)
        if key is not None:
            groups[key].append(row)
    keys = list(groups)
    if order:
        rank = {k: i for i, k in enumerate(order)}
        keys.sort(key=lambda k: rank.get(k, 999))
    else:
        keys.sort()
    return [{"bucket": key, **_stats(groups[key])} for key in keys]


def _hold_bucket(t):
    return _bucket_label(t.get("hold_sec"), HOLD_BUCKETS)


def _size_bucket(t):
    if t.get("instrument_type") == "OPTION":
        return _bucket_label(t.get("entry_qty"), OPTION_SIZE_BUCKETS)
    return _bucket_label(t.get("entry_notional"), SHARE_NOTIONAL_BUCKETS)


def _position_size_value(t):
    return t.get("entry_qty") if t.get("instrument_type") == "OPTION" else t.get("entry_notional")


def _daily_rows(trades):
    by_day = defaultdict(list)
    for t in trades:
        by_day[t.get("date")].append(t)
    running = 0.0
    out = []
    for day in sorted(k for k in by_day if k):
        rows = sorted(by_day[day], key=lambda r: r.get("entry_dt") or datetime.max)
        options = sum(r["pnl"] for r in rows if r.get("instrument_type") == "OPTION")
        shares = sum(r["pnl"] for r in rows if r.get("instrument_type") == "STOCK")
        futures = sum(r["pnl"] for r in rows if r.get("instrument_type") == "FUTURE")
        total = options + shares + futures
        running += total
        out.append({
            "date": day, "options_pnl": round(options, 2), "shares_pnl": round(shares, 2),
            "futures_pnl": round(futures, 2), "total_pnl": round(total, 2),
            "running_total": round(running, 2), "trade_count": len(rows),
        })
    losses = [abs(d["total_pnl"]) for d in out if d["total_pnl"] < 0]
    blow_threshold = max(500.0, (median(losses) * 2 if losses else 500.0))
    for d in out:
        d["blow_up"] = d["total_pnl"] <= -blow_threshold
    return out


def _stop_model(trades):
    losses = [abs(t["pnl"]) for t in trades if t["pnl"] < 0]
    if not losses:
        return {"avg_loss": 0.0, "levels": []}
    avg_loss = mean(losses)
    dynamic = [round(avg_loss * n, 2) for n in (1, 2, 3)]
    daily_abs = [abs(d["total_pnl"]) for d in _daily_rows(trades) if d["total_pnl"] != 0]
    base = median(daily_abs) if daily_abs else avg_loss
    fixed_candidates = [300, 500, 750, 1000, 1500, 2000]
    fixed = [x for x in fixed_candidates if 0.4 * base <= x <= 3.0 * base]
    if not fixed:
        fixed = sorted({round(base * x / 50) * 50 for x in (0.75, 1.0, 1.5, 2.0) if base > 0})
    levels = []
    by_day = defaultdict(list)
    for t in trades:
        by_day[t.get("date")].append(t)
    for level in sorted(set(dynamic + fixed)):
        actual_total = sum(t["pnl"] for t in trades)
        adjusted_total = 0.0
        breaches = []
        for day, rows in by_day.items():
            rows = sorted(rows, key=lambda r: r.get("entry_dt") or datetime.max)
            actual = sum(r["pnl"] for r in rows)
            cum = 0.0
            stopped = False
            for r in rows:
                if stopped:
                    break
                cum += r["pnl"]
                if cum <= -level:
                    stopped = True
            adjusted = cum if stopped else actual
            adjusted_total += adjusted
            saved = adjusted - actual
            if stopped:
                breaches.append({"date": day, "actual_pnl": round(actual, 2), "modeled_pnl": round(adjusted, 2), "saved": round(saved, 2)})
        levels.append({
            "stop": round(level, 2), "adjusted_pnl": round(adjusted_total, 2),
            "actual_pnl": round(actual_total, 2), "saved": round(adjusted_total - actual_total, 2),
            "breach_count": len(breaches), "breaches": sorted(breaches, key=lambda x: x["date"]),
        })
    return {"avg_loss": round(avg_loss, 2), "levels": levels}


def _ticker_ranking(trades):
    groups = defaultdict(list)
    for t in trades:
        groups[t.get("ticker") or "?"].append(t)
    loss_total = abs(sum(min(0, sum(r["pnl"] for r in rows)) for rows in groups.values())) or 1
    out = []
    for ticker, rows in groups.items():
        s = _stats(rows)
        pnl = s["total_pnl"]
        if pnl > 0 and s["win_rate"] >= 55:
            label = "EDGE"
        elif pnl > 0:
            label = "MARGINAL"
        else:
            share = abs(pnl) / loss_total
            label = "HEMORRHAGE" if share >= .25 else "BLEEDING" if share >= .10 else "LEAK"
        out.append({"ticker": ticker, **s, "dollars_per_trade": s["avg_pnl"], "label": label})
    return sorted(out, key=lambda x: x["total_pnl"], reverse=True)


def _revenge_and_chase(trades):
    by_day_ticker = defaultdict(list)
    for t in trades:
        by_day_ticker[(t.get("date"), t.get("ticker"))].append(t)
    revenge = defaultdict(list)
    chase = []
    for _, rows in by_day_ticker.items():
        rows = sorted(rows, key=lambda r: r.get("entry_dt") or datetime.max)
        has_loss = False
        reentry_depth = 0
        prev = None
        for r in rows:
            if has_loss:
                reentry_depth += 1
                depth = "1st re-entry" if reentry_depth == 1 else "2nd re-entry" if reentry_depth == 2 else "3rd+ re-entry"
                revenge[depth].append(r)
            if prev and prev.get("exit_dt") and r.get("entry_dt"):
                gap = (r["entry_dt"] - prev["exit_dt"]).total_seconds()
                if 0 <= gap <= 30:
                    chase.append(r)
            if r["pnl"] < 0:
                has_loss = True
            prev = r
    return (
        [{"depth": k, **_stats(v)} for k, v in revenge.items()],
        {"trade_count": len(chase), "total_pnl": round(sum(r["pnl"] for r in chase), 2), "win_rate": round(sum(r["pnl"] > 0 for r in chase) / len(chase) * 100, 1) if chase else 0},
        chase,
    )


def _averaging_down(t):
    entries = t.get("entries") or []
    if len(entries) < 2:
        return False
    first = float(entries[0].get("price") or 0)
    side = str(t.get("side")).upper()
    for e in entries[1:]:
        p = float(e.get("price") or 0)
        if side == "LONG" and p < first:
            return True
        if side == "SHORT" and p > first:
            return True
    return False


def _time_bucket(t):
    dt = t.get("entry_dt")
    if not dt:
        return None
    mins = dt.hour * 60 + dt.minute
    start = (mins // 30) * 30
    h, m = divmod(start, 60)
    return f"{h:02d}:{m:02d}"


def _dow(t):
    try:
        return datetime.strptime(t.get("date"), "%Y-%m-%d").strftime("%a")
    except Exception:
        return None


def _behavior_analysis(trades):
    revenge, chase_summary, chase_rows = _revenge_and_chase(trades)
    avg_down = [t for t in trades if _averaging_down(t)]
    clean = [t for t in trades if not _averaging_down(t)]
    short_winners = [t for t in trades if t["pnl"] > 0 and t.get("hold_sec") is not None and t["hold_sec"] < 120]
    winner_holds = [t["hold_sec"] for t in trades if t["pnl"] > 0 and t.get("hold_sec") is not None]
    avg_winner_hold = mean(winner_holds) if winner_holds else None

    by_day = defaultdict(list)
    for t in trades:
        by_day[t.get("date")].append(t)
    overtrade_rows = []
    for label, lo, hi in DAY_COUNT_BUCKETS:
        days = [rows for rows in by_day.values() if lo <= len(rows) <= hi]
        pnl = sum(sum(r["pnl"] for r in rows) for rows in days)
        green = sum(sum(r["pnl"] for r in rows) > 0 for rows in days)
        overtrade_rows.append({
            "bucket": label, "days": len(days), "total_pnl": round(pnl, 2),
            "green_day_rate": round(green / len(days) * 100, 1) if days else 0,
        })

    losses = [abs(t["pnl"]) for t in trades if t["pnl"] < 0]
    threshold = mean(losses) if losses else 0
    first_sizes, after_loss_sizes, after_loss_rows = [], [], []
    for rows in by_day.values():
        rows = sorted(rows, key=lambda r: r.get("entry_dt") or datetime.max)
        first_sizes += [v for v in (_position_size_value(r) for r in rows[:3]) if v]
        cum = 0.0
        for r in rows:
            cum += r["pnl"]
            if threshold and cum <= -threshold:
                after = [x for x in rows if (x.get("entry_dt") or datetime.max) > (r.get("entry_dt") or datetime.max)]
                after_loss_rows += after
                after_loss_sizes += [v for v in (_position_size_value(x) for x in after) if v]
                break

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    loss_pnls = [t["pnl"] for t in trades if t["pnl"] < 0]
    avg_win = mean(wins) if wins else 0
    avg_loss = abs(mean(loss_pnls)) if loss_pnls else 0

    flaws = []
    def add_flaw(name, rows, evidence=None):
        pnl = sum(r["pnl"] for r in rows)
        if rows and pnl < 0:
            flaws.append({"name": name, "trade_count": len(rows), "pnl": round(pnl, 2), "dollar_impact": round(-pnl, 2), "evidence": evidence})

    revenge_rows = []
    for key, rows in by_day_ticker_sorted(trades).items():
        loss_seen = False
        for r in rows:
            if loss_seen:
                revenge_rows.append(r)
            if r["pnl"] < 0:
                loss_seen = True
    add_flaw("Revenge / same-ticker re-entry after loss", revenge_rows)
    add_flaw("Impulsive same-ticker re-entry <=30 sec", chase_rows)
    add_flaw("Averaging down / adding to losers", avg_down)
    add_flaw("Sub-2-minute trades", [t for t in trades if t.get("hold_sec") is not None and t["hold_sec"] < 120])
    add_flaw("Trading after loss threshold", after_loss_rows)
    flaws.sort(key=lambda x: x["dollar_impact"], reverse=True)

    return {
        "revenge_trading": revenge,
        "overtrading": overtrade_rows,
        "tilt_escalation": {
            "loss_threshold": round(threshold, 2),
            "first3_avg_size": round(mean(first_sizes), 2) if first_sizes else None,
            "post_threshold_avg_size": round(mean(after_loss_sizes), 2) if after_loss_sizes else None,
            "post_threshold_trade_count": len(after_loss_rows),
            "post_threshold_pnl": round(sum(r["pnl"] for r in after_loss_rows), 2),
        },
        "chasing_fomo": chase_summary,
        "premature_exits": {
            "under_2m_winner_count": len(short_winners),
            "under_2m_winner_pnl": round(sum(r["pnl"] for r in short_winners), 2),
            "avg_winner_hold_sec": round(avg_winner_hold, 1) if avg_winner_hold is not None else None,
            "left_on_table": None,
            "note": "Post-exit market data is required to quantify money left on the table.",
        },
        "averaging_down": {
            "averaged_down": _stats(avg_down), "clean_entries": _stats(clean),
        },
        "winner_loser_asymmetry": {
            "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "reward_risk": round(avg_win / avg_loss, 2) if avg_loss else None,
        },
        "ranked_flaws": flaws,
    }


def by_day_ticker_sorted(trades):
    groups = defaultdict(list)
    for t in trades:
        groups[(t.get("date"), t.get("ticker"))].append(t)
    return {k: sorted(v, key=lambda r: r.get("entry_dt") or datetime.max) for k, v in groups.items()}


def build_performance_report(trades):
    enriched = [enrich_trade(t) for t in trades]
    open_positions = [t for t in enriched if t.get("is_open")]
    rows = [t for t in enriched if not t.get("is_open") and t.get("net_pnl") is not None]
    hold_order = [x[0] for x in HOLD_BUCKETS]
    option_order = [x[0] for x in OPTION_SIZE_BUCKETS]
    share_order = [x[0] for x in SHARE_NOTIONAL_BUCKETS]

    hold = _group_stats(rows, _hold_bucket, hold_order)
    options = [t for t in rows if t.get("instrument_type") == "OPTION"]
    shares = [t for t in rows if t.get("instrument_type") == "STOCK"]
    option_sizes = _group_stats(options, _size_bucket, option_order)
    share_sizes = _group_stats(shares, _size_bucket, share_order)

    size_vals = sorted(v for v in (_position_size_value(t) for t in rows) if v is not None)
    typical = median(size_vals) if size_vals else None
    small = [t for t in rows if typical is not None and (_position_size_value(t) or 0) <= typical]
    big = [t for t in rows if typical is not None and (_position_size_value(t) or 0) > typical]
    cross = {
        "small_size_long_hold": _stats([t for t in small if (t.get("hold_sec") or 0) >= 300]),
        "big_size_short_hold": _stats([t for t in big if t.get("hold_sec") is not None and t["hold_sec"] < 300]),
        "typical_size_median": round(typical, 2) if typical is not None else None,
        "size_unit": "contracts for options; entry notional dollars for shares/futures",
    }

    daily = _daily_rows(rows)
    behavior = _behavior_analysis(rows)
    time_blocks = _group_stats(rows, _time_bucket)
    dow = _group_stats(rows, _dow, ["Mon", "Tue", "Wed", "Thu", "Fri"])
    first10 = _stats([t for t in rows if t.get("entry_dt") and 570 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 580])
    rest = _stats([t for t in rows if t.get("entry_dt") and not (570 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 580)])
    first30 = _stats([t for t in rows if t.get("entry_dt") and 570 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 600])
    last30 = _stats([t for t in rows if t.get("entry_dt") and 930 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 960])
    middle = _stats([t for t in rows if t.get("entry_dt") and 600 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 930])

    normal_sizes = size_vals
    lo = normal_sizes[max(0, int(len(normal_sizes) * .10) - 1)] if normal_sizes else None
    hi = normal_sizes[min(len(normal_sizes) - 1, int(len(normal_sizes) * .90))] if normal_sizes else None
    disciplined = [
        t for t in rows
        if (t.get("hold_sec") or 0) >= 300
        and (lo is None or (_position_size_value(t) or 0) >= lo)
        and (hi is None or (_position_size_value(t) or 0) <= hi)
    ]
    destructive = [t for t in rows if t not in disciplined]

    trading_days = max(1, len({t.get("date") for t in disciplined if t.get("date")}))
    daily_edge = sum(t["pnl"] for t in disciplined) / trading_days if disciplined else 0

    # Forward projection uses remaining weekdays as an explicit approximation.
    # It deliberately does not pretend to know the exchange holiday calendar.
    today = datetime.now().date()
    year_end = today.replace(month=12, day=31)
    remaining_weekdays = 0
    cursor = today
    from datetime import timedelta
    while cursor <= year_end:
        if cursor.weekday() < 5:
            remaining_weekdays += 1
        cursor += timedelta(days=1)

    daily_totals = defaultdict(float)
    for t in rows:
        if t.get("date"):
            daily_totals[t["date"]] += t["pnl"]
    cum = peak = 0.0
    for day in sorted(daily_totals):
        cum += daily_totals[day]
        peak = max(peak, cum)
    current_drawdown = cum - peak

    projection_rates = [1.0, .75, .50, .33, .25]
    projections = []
    for r in projection_rates:
        rate_edge = daily_edge * r
        gross = rate_edge * remaining_weekdays
        net_after_drawdown = gross + current_drawdown
        monthly_edge = rate_edge * 21
        months_to_recover = abs(current_drawdown) / monthly_edge if current_drawdown < 0 and monthly_edge > 0 else 0
        projections.append({
            "rate": r,
            "daily_edge": round(rate_edge, 2),
            "remaining_weekdays": remaining_weekdays,
            "gross_earnings": round(gross, 2),
            "current_drawdown": round(current_drawdown, 2),
            "net_after_current_drawdown": round(net_after_drawdown, 2),
            "months_to_recover": round(months_to_recover, 2) if months_to_recover else 0,
        })

    return {
        "meta": {
            "trade_count": len(rows),
            "open_position_count": len(open_positions),
            "timestamp_coverage": round(sum(t.get("hold_sec") is not None for t in rows) / len(rows) * 100, 1) if rows else 0,
            "note": "All source metrics are deterministic from stored trades/executions. AI should interpret these values, not recalculate them.",
        },
        "matching": {
            "completed_trades": len(rows),
            "open_positions": [
                {
                    "trade_group": t.get("trade_group"), "ticker": t.get("ticker"),
                    "instrument_type": t.get("instrument_type"), "side": t.get("side"),
                    "open_quantity": t.get("open_quantity"), "date": t.get("date"),
                } for t in open_positions
            ],
        },
        "hold_time": hold,
        "position_size": {"options": option_sizes, "shares_by_notional": share_sizes, "cross_reference": cross},
        "daily_pnl": daily,
        "daily_stop_model": _stop_model(rows),
        "ticker_ranking": _ticker_ranking(rows),
        "behavior": behavior,
        "time_analysis": {
            "half_hour_blocks": time_blocks, "day_of_week": dow,
            "first_10_minutes": first10, "rest_of_day": rest,
            "first_30_minutes": first30, "middle_of_day": middle, "last_30_minutes": last30,
        },
        "two_traders": {"disciplined": _stats(disciplined), "destructive": _stats(destructive)},
        "projections": {"proven_daily_edge": round(daily_edge, 2), "rates": projections},
    }
