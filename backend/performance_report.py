from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from math import inf
from statistics import mean, median

from behavior_rules import detect_daily_flags

from smoking_gun_library import (
    ANALYTICS_ENGINE_VERSION,
    BEHAVIOR_VERSION,
    REPORT_SCHEMA_VERSION,
)


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


def _size_family(t):
    return "OPTION" if t.get("instrument_type") == "OPTION" else "NOTIONAL"


def _position_size_value(t):
    return t.get("entry_qty") if _size_family(t) == "OPTION" else t.get("entry_notional")


def _instrument_size_medians(trades):
    grouped = defaultdict(list)
    for t in trades:
        value = _position_size_value(t)
        if value is not None and value > 0:
            grouped[_size_family(t)].append(value)
    return {family: median(values) for family, values in grouped.items() if values}


def _size_multiple(t, medians):
    value = _position_size_value(t)
    med = medians.get(_size_family(t))
    if value is None or not med:
        return None
    return value / med


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
    option_sizes = [t["entry_qty"] for t in trades if t.get("instrument_type") == "OPTION" and t.get("entry_qty")]
    share_sizes = [t["entry_notional"] for t in trades if t.get("instrument_type") == "STOCK" and t.get("entry_notional")]
    return {
        "avg_loss": round(avg_loss, 2),
        "typical_option_contracts": round(median(option_sizes), 2) if option_sizes else None,
        "typical_share_notional": round(median(share_sizes), 2) if share_sizes else None,
        "levels": levels,
    }


def _ticker_ranking(trades):
    groups = defaultdict(list)
    for t in trades:
        groups[t.get("ticker") or "?"].append(t)
    loss_total = abs(sum(min(0, sum(r["pnl"] for r in rows)) for rows in groups.values())) or 1
    out = []
    for ticker, rows in groups.items():
        s = _stats(rows)
        pnl = s["total_pnl"]
        if pnl > 0 and s["win_rate"] >= 55 and s["trade_count"] >= 5:
            label = "EDGE"
        elif pnl > 0:
            label = "MARGINAL"
        else:
            share = abs(pnl) / loss_total
            label = "HEMORRHAGE" if share >= .25 else "BLEEDING" if share >= .10 else "LEAK"
        out.append({
            "ticker": ticker, **s, "dollars_per_trade": s["avg_pnl"], "label": label,
            "sample_quality": "established" if s["trade_count"] >= 5 else "thin",
        })
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
    """True only when an add is executed worse than the running average entry.

    This is stronger evidence than comparing every add with the first fill and
    avoids flagging harmless scaling around the same average price.
    """
    entries = t.get("entries") or []
    if len(entries) < 2:
        return False
    side = str(t.get("side")).upper()
    qty = float(entries[0].get("qty") or 0)
    cost = qty * float(entries[0].get("price") or 0)
    if qty <= 0:
        return False
    for e in entries[1:]:
        p = float(e.get("price") or 0)
        running_avg = cost / qty
        if side == "LONG" and p < running_avg:
            return True
        if side == "SHORT" and p > running_avg:
            return True
        add_qty = float(e.get("qty") or 0)
        if add_qty > 0:
            cost += add_qty * p
            qty += add_qty
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
    size_medians = _instrument_size_medians(trades)
    first_sizes, after_loss_sizes, after_loss_rows = [], [], []
    for rows in by_day.values():
        rows = sorted(rows, key=lambda r: r.get("entry_dt") or datetime.max)
        first_sizes += [v for v in (_size_multiple(r, size_medians) for r in rows[:3]) if v is not None]
        cum = 0.0
        for r in rows:
            cum += r["pnl"]
            if threshold and cum <= -threshold:
                after = [x for x in rows if (x.get("entry_dt") or datetime.max) > (r.get("entry_dt") or datetime.max)]
                after_loss_rows += after
                after_loss_sizes += [v for v in (_size_multiple(x, size_medians) for x in after) if v is not None]
                break

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    loss_pnls = [t["pnl"] for t in trades if t["pnl"] < 0]
    avg_win = mean(wins) if wins else 0
    avg_loss = abs(mean(loss_pnls)) if loss_pnls else 0

    flaws = []
    actual_total = sum(t["pnl"] for t in trades)
    def add_flaw(name, rows, evidence=None):
        pnl = sum(r["pnl"] for r in rows)
        if rows and pnl < 0:
            impact = -pnl
            flaws.append({
                "name": name,
                "trade_count": len(rows),
                "pnl": round(pnl, 2),
                "dollar_impact": round(impact, 2),
                "pnl_if_eliminated": round(actual_total + impact, 2),
                "evidence": evidence,
            })

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

    # Only classify trade-count/session cohorts as flaws when the actual cohort
    # is net negative. Presence alone is not evidence of a mistake.
    for label, lo, hi in DAY_COUNT_BUCKETS[1:]:
        cohort = []
        for day_rows in by_day.values():
            if lo <= len(day_rows) < hi:
                cohort.extend(day_rows)
        add_flaw(f"High-volume trading days ({label} trades)", cohort)

    opening_rows = [
        t for t in trades if t.get("entry_dt")
        and 570 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 600
    ]
    closing_rows = [
        t for t in trades if t.get("entry_dt")
        and 930 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 960
    ]
    add_flaw("First 30 minutes", opening_rows)
    add_flaw("Last 30 minutes", closing_rows)
    flaws.sort(key=lambda x: x["dollar_impact"], reverse=True)

    return {
        "revenge_trading": revenge,
        "overtrading": overtrade_rows,
        "tilt_escalation": {
            "loss_threshold": round(threshold, 2),
            "size_unit": "multiple of typical size within instrument family",
            "first3_avg_size": round(mean(first_sizes), 2) if first_sizes else None,
            "post_threshold_avg_size": round(mean(after_loss_sizes), 2) if after_loss_sizes else None,
            "post_threshold_trade_count": len(after_loss_rows),
            "post_threshold_pnl": round(sum(r["pnl"] for r in after_loss_rows), 2),
        },
        "chasing_fomo": chase_summary,
        "premature_exits": {
            "under_2m_winner_count": len(short_winners),
            "under_2m_winner_pnl": round(sum(r["pnl"] for r in short_winners), 2),
            "under_2m_winner_avg_pnl": round(mean([r["pnl"] for r in short_winners]), 2) if short_winners else None,
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


def _scoreboard(trades, daily):
    pnls = [float(t.get("pnl") or 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross = sum(
        float(t.get("gross_pnl")) if t.get("gross_pnl") is not None
        else float(t.get("pnl") or 0) + float(t.get("commissions") or 0)
        for t in trades
    )
    fees = sum(float(t.get("commissions") or 0) for t in trades)
    avg_winner = mean(wins) if wins else 0.0
    avg_loser = abs(mean(losses)) if losses else 0.0

    peak = 0.0
    max_drawdown = 0.0
    for day in daily:
        running = float(day.get("running_total") or 0)
        peak = max(peak, running)
        max_drawdown = min(max_drawdown, running - peak)

    best = max(daily, key=lambda d: d["total_pnl"]) if daily else None
    worst = min(daily, key=lambda d: d["total_pnl"]) if daily else None
    return {
        "net_pnl": round(sum(pnls), 2),
        "gross_pnl": round(gross, 2),
        "fees": round(fees, 2),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "reward_risk": round(avg_winner / avg_loser, 2) if avg_loser else None,
        "max_drawdown": round(max_drawdown, 2),
        "active_days": len(daily),
        "best_day": {"date": best["date"], "pnl": round(best["total_pnl"], 2)} if best else None,
        "worst_day": {"date": worst["date"], "pnl": round(worst["total_pnl"], 2)} if worst else None,
    }


def _compact_trade_ledger(trades):
    out = []
    for t in trades:
        entry_dt = t.get("entry_dt")
        exit_dt = t.get("exit_dt")
        out.append({
            "trade_group": t.get("trade_group"),
            "date": t.get("date"),
            "ticker": t.get("ticker"),
            "instrument_type": t.get("instrument_type"),
            "side": t.get("side"),
            "entry_time": entry_dt.isoformat(sep=" ") if entry_dt else None,
            "exit_time": exit_dt.isoformat(sep=" ") if exit_dt else None,
            "hold_sec": round(t["hold_sec"], 3) if t.get("hold_sec") is not None else None,
            "entry_size": round(float(_position_size_value(t)), 6) if _position_size_value(t) is not None else None,
            "gross_pnl": round(float(t["gross_pnl"]), 2) if t.get("gross_pnl") is not None else None,
            "commissions": round(float(t.get("commissions") or 0), 2),
            "net_pnl": round(float(t.get("pnl") or 0), 2),
            "hold_bucket": _hold_bucket(t),
            "size_bucket": _size_bucket(t),
        })
    return out


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

    size_medians = _instrument_size_medians(rows)
    for t in rows:
        t["size_multiple"] = _size_multiple(t, size_medians)
    small = [t for t in rows if t.get("size_multiple") is not None and t["size_multiple"] <= 1.0]
    big = [t for t in rows if t.get("size_multiple") is not None and t["size_multiple"] > 1.0]
    cross = {
        "small_size_long_hold": _stats([t for t in small if (t.get("hold_sec") or 0) >= 300]),
        "big_size_short_hold": _stats([t for t in big if t.get("hold_sec") is not None and t["hold_sec"] < 300]),
        "typical_option_contracts": round(size_medians.get("OPTION"), 2) if size_medians.get("OPTION") is not None else None,
        "typical_share_notional": round(size_medians.get("NOTIONAL"), 2) if size_medians.get("NOTIONAL") is not None else None,
        "size_unit": "small/big is relative to the median within its instrument family",
    }

    daily = _daily_rows(rows)
    behavior = _behavior_analysis(rows)
    raw_by_day = defaultdict(list)
    for t in trades:
        if t.get("date"):
            raw_by_day[t.get("date")].append(t)
    verified_rule_flags = []
    prior_counts = []
    for day in sorted(raw_by_day):
        for flag in detect_daily_flags(raw_by_day[day], prior_counts):
            verified_rule_flags.append({"date": day, **flag})
        prior_counts.append(len(raw_by_day[day]))
    behavior["verified_rule_flags"] = verified_rule_flags
    time_blocks = _group_stats(rows, _time_bucket)
    dow = _group_stats(rows, _dow, ["Mon", "Tue", "Wed", "Thu", "Fri"])
    first10 = _stats([t for t in rows if t.get("entry_dt") and 570 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 580])
    rest = _stats([t for t in rows if t.get("entry_dt") and not (570 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 580)])
    first30 = _stats([t for t in rows if t.get("entry_dt") and 570 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 600])
    last30 = _stats([t for t in rows if t.get("entry_dt") and 930 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 960])
    middle = _stats([t for t in rows if t.get("entry_dt") and 600 <= t["entry_dt"].hour * 60 + t["entry_dt"].minute < 930])

    size_multiples = sorted(t["size_multiple"] for t in rows if t.get("size_multiple") is not None)
    lo = size_multiples[max(0, int(len(size_multiples) * .10) - 1)] if size_multiples else None
    hi = size_multiples[min(len(size_multiples) - 1, int(len(size_multiples) * .90))] if size_multiples else None
    disciplined = [
        t for t in rows
        if t.get("hold_sec") is not None
        and t["hold_sec"] >= 300
        and t.get("size_multiple") is not None
        and (lo is None or t["size_multiple"] >= lo)
        and (hi is None or t["size_multiple"] <= hi)
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
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "analytics_engine_version": ANALYTICS_ENGINE_VERSION,
            "behavior_version": BEHAVIOR_VERSION,
            "trade_count": len(rows),
            "open_position_count": len(open_positions),
            "timestamp_coverage": round(sum(t.get("hold_sec") is not None for t in rows) / len(rows) * 100, 1) if rows else 0,
            "note": "All source metrics are deterministic from stored trades/executions. AI should interpret these values, not recalculate them.",
            "ticker_edge_rule": "EDGE requires positive P&L, win rate >=55%, and at least 5 completed trades; positive thinner samples are MARGINAL.",
            "behavior_counterfactual_note": "P&L-if-eliminated is an independent what-if for each negative cohort. Behavior cohorts can overlap, so impacts must not be summed.",
        },
        "scoreboard": _scoreboard(rows, daily),
        "trade_ledger": _compact_trade_ledger(rows),
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
