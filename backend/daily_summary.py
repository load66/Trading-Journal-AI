import hashlib
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from behavior_rules import detect_daily_flags, deterministic_strengths, recorded_observations
from trade_metrics import MARKET_TIMEZONE, trade_entry_exit_datetimes, trade_is_closed
from smoking_gun_library import ANALYTICS_ENGINE_VERSION

from ai_analysis import (
    GROQ_API_URL,
    GROQ_MODEL,
    MODEL,
    _strip_json_fence,
    _valid_api_key,
    get_client,
    response_text,
)

DAY_REVIEW_TIMEZONE = os.getenv("DAY_REVIEW_TIMEZONE", "America/Chicago")

SESSION_WINDOWS = (
    ("OPENING", 9 * 60 + 30, 9 * 60 + 40),
    ("PRIME", 9 * 60 + 40, 11 * 60 + 30),
    ("CHOP", 11 * 60 + 30, 13 * 60 + 30),
    ("AFTERNOON", 13 * 60 + 30, 15 * 60),
    ("HARD_CLOSE", 15 * 60, 15 * 60 + 45),
    ("LATE_CLOSE", 15 * 60 + 45, 16 * 60),
)


def _session_window_label(exit_dt: datetime) -> str:
    minute = exit_dt.hour * 60 + exit_dt.minute
    for label, start, end in SESSION_WINDOWS:
        if start <= minute < end:
            return label
    return "OUTSIDE_RTH"


def build_session_path_analysis(trades: list[dict]) -> dict:
    """Build the deterministic evidence represented by the Day Review P&L chart.

    The chart is a realized-P&L timeline: each completed trade's full net P&L is
    booked at its final exit time. It is not a price chart and it does not
    estimate unrealized P&L between fills.
    """
    total_trades = len(trades or [])
    day_total = round(sum(float(t.get("net_pnl") or 0) for t in (trades or [])), 2)
    timed = []

    for trade in trades or []:
        entry_dt, exit_dt = trade_entry_exit_datetimes(
            trade,
            target_timezone=MARKET_TIMEZONE,
        )
        if exit_dt is None:
            continue
        timed.append((exit_dt, entry_dt, trade))

    timed.sort(key=lambda item: (item[0], str(item[2].get("trade_group") or "")))

    window_stats = {
        label: {"trade_count": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
        for label, _start, _end in SESSION_WINDOWS
    }
    window_stats["OUTSIDE_RTH"] = {
        "trade_count": 0,
        "wins": 0,
        "losses": 0,
        "net_pnl": 0.0,
    }

    points = []
    cumulative = 0.0
    peak = 0.0
    peak_index = None
    trough = 0.0
    trough_index = None
    running_peak = 0.0
    running_peak_index = None
    max_drawdown = 0.0
    max_drawdown_from_index = None
    max_drawdown_to_index = None

    for index, (exit_dt, entry_dt, trade) in enumerate(timed):
        pnl = round(float(trade.get("net_pnl") or 0), 2)
        cumulative = round(cumulative + pnl, 2)
        window = _session_window_label(exit_dt)
        stats = window_stats[window]
        stats["trade_count"] += 1
        stats["net_pnl"] = round(stats["net_pnl"] + pnl, 2)
        if pnl > 0:
            stats["wins"] += 1
        elif pnl < 0:
            stats["losses"] += 1

        point = {
            "sequence": index + 1,
            "trade_group": trade.get("trade_group"),
            "ticker": trade.get("ticker"),
            "side": trade.get("side"),
            "entry_time_et": entry_dt.strftime("%H:%M:%S") if entry_dt else None,
            "exit_time_et": exit_dt.strftime("%H:%M:%S"),
            "session_window": window,
            "trade_pnl": pnl,
            "cumulative_pnl": cumulative,
        }
        points.append(point)

        if cumulative > peak:
            peak = cumulative
            peak_index = index
        if cumulative < trough:
            trough = cumulative
            trough_index = index

        if cumulative > running_peak:
            running_peak = cumulative
            running_peak_index = index
        drawdown = round(running_peak - cumulative, 2)
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            max_drawdown_from_index = running_peak_index
            max_drawdown_to_index = index

    chart_final = round(cumulative, 2)
    timed_count = len(points)
    coverage_pct = round(timed_count / total_trades * 100, 1) if total_trades else 0.0

    def point_ref(index):
        if index is None or index < 0 or index >= len(points):
            return None
        p = points[index]
        return {
            "trade_group": p["trade_group"],
            "ticker": p["ticker"],
            "exit_time_et": p["exit_time_et"],
            "cumulative_pnl": p["cumulative_pnl"],
        }

    post_peak = points[peak_index + 1:] if peak_index is not None else []
    post_peak_net = round(sum(float(p["trade_pnl"]) for p in post_peak), 2)

    largest_winner = max(points, key=lambda p: p["trade_pnl"], default=None)
    if largest_winner and largest_winner["trade_pnl"] <= 0:
        largest_winner = None
    largest_loser = min(points, key=lambda p: p["trade_pnl"], default=None)
    if largest_loser and largest_loser["trade_pnl"] >= 0:
        largest_loser = None

    return {
        "basis": "realized_pnl_booked_at_final_exit",
        "timezone": MARKET_TIMEZONE,
        "total_trades": total_trades,
        "timed_trades": timed_count,
        "timing_coverage_pct": coverage_pct,
        "day_total_realized_pnl": day_total,
        "chart_final_realized_pnl": chart_final,
        "untimed_realized_pnl": round(day_total - chart_final, 2),
        "peak_realized_pnl": round(peak, 2),
        "peak_point": point_ref(peak_index),
        "trough_realized_pnl": round(trough, 2),
        "trough_point": point_ref(trough_index),
        "giveback_from_positive_peak": round(max(0.0, peak - chart_final), 2) if peak > 0 else 0.0,
        "max_drawdown_from_high_water": round(max_drawdown, 2),
        "max_drawdown_from": point_ref(max_drawdown_from_index),
        "max_drawdown_to": point_ref(max_drawdown_to_index),
        "post_peak": {
            "trade_count": len(post_peak),
            "net_pnl": post_peak_net,
            "wins": sum(1 for p in post_peak if p["trade_pnl"] > 0),
            "losses": sum(1 for p in post_peak if p["trade_pnl"] < 0),
        },
        "largest_winner": largest_winner,
        "largest_loser": largest_loser,
        "window_realized_pnl": window_stats,
        "trade_sequence": points,
        "window_definitions_et": {
            "OPENING": "09:30-09:40",
            "PRIME": "09:40-11:30",
            "CHOP": "11:30-13:30",
            "AFTERNOON": "13:30-15:00",
            "HARD_CLOSE": "15:00-15:45",
            "LATE_CLOSE": "15:45-16:00",
            "OUTSIDE_RTH": "outside 09:30-16:00",
        },
    }


DAILY_SUMMARY_PROMPT = """You are a professional trading coach producing an end-of-day performance review for a day trader.

You will receive structured data: the date, all trades with their analysis, the day's KPIs, deterministic execution flags, and optionally a diary entry. Produce a thorough, candid coaching report in JSON format.

Rules:
- Analyze the complete session, not just P&L.
- Use the supplied executions, trade sequence, sizing, entries/exits, MFE/MAE, strategy fields, diary notes, and deterministic flags to identify strengths, mistakes, patterns, and likely behavioral/process issues.
- You may make professional trading inferences from the supplied session data. Phrase uncertain interpretations as interpretations rather than invented facts.
- Do not invent tickers, prices, timestamps, trade counts, dollar amounts, or events that are absent from the supplied data.
- trade_grades must contain exactly one entry per trade provided and use the exact trade_group key.
- Grades should assess the quality of the trading decision/process using all available session evidence; outcome alone must not determine the grade.
- mistakes should include the most important trading mistakes you identify, even when they were not manually recorded in the diary.
- mental_game should give your best professional read of the trader's decision-making/behavior during the session. Do not replace it with a generic "insufficient evidence" message.
- Deterministic behavior_flags are reliable execution observations and should be incorporated where useful, but they do not limit what else you may diagnose.
- session_path_analysis is deterministic evidence from the Day Review realized-P&L chart. Use its trade sequence, peak, trough, drawdown/giveback, post-peak results, and session-window clustering when they materially improve the diagnosis.
- The session path books each trade's full realized P&L only at its final exit. It is NOT a price chart, unrealized/mark-to-market equity curve, or evidence of what happened inside a trade. Never infer intratrade price action from the session path alone.
- Respect timing_coverage_pct. If some completed trades lack usable timestamps, do not present session-path timing conclusions as complete-day facts.
- highlights.good and highlights.bad are presentation cues, not extra conclusions. Each item MUST be an exact verbatim substring copied from either narrative or mental_game.
- highlights.good should mark only concise phrases describing clearly positive execution, discipline, edge, or effective decisions.
- highlights.bad should mark only concise phrases describing mistakes, process lapses, behavioral flags, weak risk control, or poor decisions.
- Keep highlights selective: normally 1-3 good phrases and 1-3 bad phrases, preferably 4-16 words each. Do not highlight whole paragraphs.
- Never put the same phrase in both good and bad.
- Be direct, specific, and useful.
- Return ONLY valid JSON — no markdown fences, no explanation.

Required JSON schema:
{
  "narrative": "2-3 paragraph overview of the trading day — what happened, the flow of the session, and notable moments",
  "mental_game": "1-2 sentences with your professional read of decision-making and behavioral quality during the session",
  "highlights": {
    "good": ["exact verbatim positive phrase copied from narrative or mental_game"],
    "bad": ["exact verbatim mistake/risk phrase copied from narrative or mental_game"]
  },
  "strengths": ["specific thing done well 1", "specific thing done well 2"],
  "mistakes": ["specific mistake with detail 1", "specific mistake with detail 2"],
  "coaching": ["specific actionable coaching point 1", "specific actionable coaching point 2", "specific actionable coaching point 3"],
  "trade_grades": [
    {
      "trade_group": "exact trade_group key from input",
      "ticker": "SYMBOL",
      "grade": "A|B|C|D|F|N/A",
      "one_line": "One specific sentence explaining the grade."
    }
  ],
  "overall_grade": "A+|A|A-|B+|B|B-|C+|C|C-|D|F|N/A",
  "tomorrow_focus": ["specific focus point 1", "specific focus point 2"],
  "patterns": ["pattern identified today 1", "pattern identified today 2"]
}"""


def daily_context_signature(context: dict) -> str:
    """Return a stable fingerprint for the evidence used by a Day Review.

    The signature intentionally follows day-specific evidence only. This lets a
    cached diagnosis survive unrelated future trading while still refreshing
    automatically when this day's trades, journal evidence, or deterministic
    flags change.
    """
    material = {
        "date": context.get("date"),
        "trades": context.get("trades") or [],
        "day_kpis": context.get("day_kpis") or {},
        "diary_summary": context.get("diary_summary"),
        "behavior_flags": context.get("behavior_flags") or [],
        "verified_strengths": context.get("verified_strengths") or [],
        "recorded_observations": context.get("recorded_observations") or [],
        "session_path_analysis": context.get("session_path_analysis") or {},
    }
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def daily_auto_generation_is_fresh(generated_at, now: datetime | None = None) -> bool:
    """Return True when a saved diagnosis was generated today in journal-local time.

    Database CURRENT_TIMESTAMP values are UTC in SQLite and timezone-aware in
    Postgres. Naive values are therefore interpreted as UTC before comparing
    dates in DAY_REVIEW_TIMEZONE.
    """
    if not generated_at:
        return False

    if isinstance(generated_at, datetime):
        generated = generated_at
    else:
        raw = str(generated_at).strip()
        if not raw:
            return False
        try:
            generated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False

    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    try:
        local_tz = ZoneInfo(DAY_REVIEW_TIMEZONE)
    except Exception:
        local_tz = ZoneInfo("America/Chicago")

    return generated.astimezone(local_tz).date() == current.astimezone(local_tz).date()


def daily_cache_matches(content: dict, input_signature: str) -> bool:
    """Return True when a saved diagnosis was generated from identical day evidence.

    Cache validity follows the evidence fingerprint only. Engine/evidence version
    bumps must not spend AI tokens by themselves; if a code change alters the
    actual derived evidence, daily_context_signature changes naturally.
    """
    return bool(
        isinstance(content, dict)
        and content.get("input_signature")
        and content.get("input_signature") == input_signature
    )


def build_daily_context(conn, date: str, account_id) -> dict:
    """Fetch all data needed to generate a daily summary."""

    # Trades with joined analysis for the date
    sql = """
        SELECT t.id, t.trade_group, t.ticker, t.side, t.instrument_type,
               t.net_pnl, t.gross_pnl, t.commissions, t.executions,
               t.option_type, t.option_strike, t.option_expiry,
               t.mfe_pct, t.mae_pct, t.exit_efficiency,
               ta.strategy, ta.r_multiple, ta.stop_loss, ta.target_price,
               ta.risk_per_trade, ta.risk_reward, ta.mistakes,
               ta.emotional_state, ta.entry_reason, ta.exit_reason,
               ta.ai_feedback, ta.idea_source, ta.match_confidence
        FROM trades t
        LEFT JOIN trade_analysis ta ON t.trade_group = ta.trade_group
        WHERE t.date = ?
    """
    params = [date]
    if account_id is not None:
        sql += " AND t.account_id = ?"
        params.append(account_id)
    sql += " ORDER BY t.id"

    rows = conn.execute(sql, params).fetchall()
    trades = []
    for row in rows:
        d = dict(row)
        try:
            d['executions'] = json.loads(d.get('executions') or '[]')
        except Exception:
            d['executions'] = []
        if trade_is_closed(d):
            trades.append(d)

    # Day KPIs
    all_pnl = [t.get('net_pnl') or 0 for t in trades]
    winners = [p for p in all_pnl if p > 0]
    losers = [p for p in all_pnl if p < 0]
    total_trades = len(trades)
    winner_effs = [
        float(t.get("exit_efficiency"))
        for t in trades
        if t.get("exit_efficiency") is not None and float(t.get("net_pnl") or 0) > 0
    ]
    day_kpis = {
        "total_net_pnl": round(sum(all_pnl), 2),
        "total_trades": total_trades,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": round(len(winners) / total_trades * 100, 1) if total_trades else 0,
        "avg_win": round(sum(winners) / len(winners), 2) if winners else 0,
        "avg_loss": round(sum(losers) / len(losers), 2) if losers else 0,
        "profit_factor": round(sum(winners) / abs(sum(losers)), 2) if losers else None,
        "exit_efficiency": round(sum(winner_effs) / len(winner_effs), 2) if winner_effs else None,
    }

    # All-time KPIs for context
    at_params = []
    at_sql = "SELECT net_pnl, gross_pnl, side, executions FROM trades WHERE 1=1"
    if account_id is not None:
        at_sql += " AND account_id = ?"
        at_params.append(account_id)
    at_rows = [dict(r) for r in conn.execute(at_sql, at_params).fetchall()]
    at_rows = [r for r in at_rows if trade_is_closed(r)]
    at_all = [r['net_pnl'] or 0 for r in at_rows]
    at_wins = [p for p in at_all if p > 0]
    at_losses = [p for p in at_all if p < 0]
    at_total = len(at_all)
    alltime_kpis = {
        "win_rate": round(len(at_wins) / at_total * 100, 1) if at_total else 0,
        "avg_win": round(sum(at_wins) / len(at_wins), 2) if at_wins else 0,
        "avg_loss": round(sum(at_losses) / len(at_losses), 2) if at_losses else 0,
        "profit_factor": round(sum(at_wins) / abs(sum(at_losses)), 2) if at_losses else None,
    }

    # Diary entry for the date
    diary_row = conn.execute(
        "SELECT ai_analysis FROM diary_entries WHERE entry_date = ?" +
        (" AND account_id = ?" if account_id is not None else "") +
        " ORDER BY id DESC LIMIT 1",
        [date] + ([account_id] if account_id is not None else [])
    ).fetchone()

    diary_summary = None
    if diary_row and diary_row[0]:
        try:
            da = json.loads(diary_row[0])
            diary_summary = {
                "overall_summary": da.get("overall_summary"),
                "patterns_identified": da.get("patterns_identified", []),
                "improvement_areas": da.get("improvement_areas", []),
            }
        except Exception:
            pass

    hist_sql = "SELECT date, COUNT(*) AS c FROM trades WHERE date < ?"
    hist_params = [date]
    if account_id is not None:
        hist_sql += " AND account_id = ?"
        hist_params.append(account_id)
    hist_sql += " GROUP BY date ORDER BY date"
    historical_counts = [int(r["c"]) for r in conn.execute(hist_sql, hist_params).fetchall()]

    behavior_flags = detect_daily_flags(trades, historical_counts)
    verified_strengths = deterministic_strengths(trades, day_kpis)
    if not behavior_flags and trades:
        verified_strengths.append({
            "text": "No deterministic behavioral flags triggered on this session.",
            "evidence": "VERIFIED",
        })
    recorded = recorded_observations(trades, diary_summary)
    session_path_analysis = build_session_path_analysis(trades)

    return {
        "date": date,
        "trades": trades,
        "day_kpis": day_kpis,
        "alltime_kpis": alltime_kpis,
        "diary_summary": diary_summary,
        "behavior_flags": behavior_flags,
        "verified_strengths": verified_strengths,
        "recorded_observations": recorded,
        "session_path_analysis": session_path_analysis,
    }


def _groq_daily_summary(user_content: str, api_key: str) -> dict:
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": DAILY_SUMMARY_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "max_completion_tokens": 4096,
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


def _anthropic_daily_summary(user_content: str) -> dict:
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=DAILY_SUMMARY_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return json.loads(_strip_json_fence(response_text(response)))


def _normalize_highlights(result: dict) -> dict:
    """Keep only short highlight phrases that actually occur in rendered prose."""
    raw = result.get("highlights")
    raw = raw if isinstance(raw, dict) else {}
    source = "\n".join(
        str(result.get(key) or "")
        for key in ("narrative", "mental_game")
    )
    source_lower = source.lower()

    normalized = {"good": [], "bad": []}
    used = set()
    for tone in ("good", "bad"):
        values = raw.get(tone)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            phrase = " ".join(value.split()).strip()
            key = phrase.lower()
            if (
                len(phrase) < 4
                or len(phrase) > 220
                or key in used
                or key not in source_lower
            ):
                continue
            normalized[tone].append(phrase)
            used.add(key)
            if len(normalized[tone]) >= 4:
                break
    return normalized


def generate_daily_summary(context: dict) -> dict:
    """Generate a structured daily summary with Groq first, Anthropic fallback."""
    date = context["date"]
    trades = context["trades"]
    kpis = context["day_kpis"]
    alltime = context["alltime_kpis"]
    diary = context.get("diary_summary")
    session_path = context.get("session_path_analysis") or {}

    # Format trades for the prompt
    trade_lines = []
    for t in trades:
        execs = t.get("executions", [])
        first_time = next((e.get("time", "") for e in execs), "")
        line = (
            f"  - trade_group: {t['trade_group']} | ticker: {t['ticker']} | "
            f"side: {t['side']} | net_pnl: ${t.get('net_pnl') or 0:.2f} | "
            f"strategy: {t.get('strategy') or 'N/A'} | "
            f"r_multiple: {t.get('r_multiple') or 'N/A'} | "
            f"stop_loss: {t.get('stop_loss') or 'N/A'} | "
            f"risk_per_trade: {t.get('risk_per_trade') or 'N/A'} | "
            f"entry_reason: {t.get('entry_reason') or 'N/A'} | "
            f"exit_reason: {t.get('exit_reason') or 'N/A'} | "
            f"mistakes: {t.get('mistakes') or 'none'} | "
            f"emotional_state: {t.get('emotional_state') or 'N/A'} | "
            f"mfe_pct: {t.get('mfe_pct') if t.get('mfe_pct') is not None else 'N/A'} | "
            f"mae_pct: {t.get('mae_pct') if t.get('mae_pct') is not None else 'N/A'} | "
            f"exit_efficiency: {t.get('exit_efficiency') if t.get('exit_efficiency') is not None else 'N/A'} | "
            f"first_entry_time: {first_time or 'N/A'}"
        )
        trade_lines.append(line)

    diary_section = ""
    if diary:
        diary_section = f"""
Diary entry for this date:
  Overall summary: {diary.get('overall_summary') or 'N/A'}
  Patterns: {', '.join(diary.get('patterns_identified', [])) or 'N/A'}
  Improvement areas: {', '.join(diary.get('improvement_areas', [])) or 'N/A'}"""

    behavior_flags = context.get("behavior_flags") or []
    verified_strengths = context.get("verified_strengths") or []
    recorded = context.get("recorded_observations") or []
    flag_section = "\n".join(
        (
            f"  - behavior_code: {f.get('code')} | "
            f"trade_group: {f.get('trade_group') or 'N/A'} | "
            f"ticker: {f.get('ticker') or 'N/A'} | "
            f"{f.get('title')}: {f.get('detail')}"
        )
        for f in behavior_flags
    ) or "  None."
    strength_section = "\n".join(
        f"  - {o.get('text')}" for o in verified_strengths
    ) or "  None."

    user_content = f"""Date: {date}

Day KPIs:
  Net P&L: ${kpis['total_net_pnl']:.2f}
  Trades: {kpis['total_trades']} ({kpis['winning_trades']}W / {kpis['losing_trades']}L)
  Win Rate: {kpis['win_rate']}%
  Avg Win: ${kpis['avg_win']:.2f} | Avg Loss: ${kpis['avg_loss']:.2f}
  Profit Factor: {kpis['profit_factor']}

Historical averages (all-time):
  Win Rate: {alltime['win_rate']}% | Avg Win: ${alltime['avg_win']:.2f} | Avg Loss: ${alltime['avg_loss']:.2f} | Profit Factor: {alltime['profit_factor']}

Trades:
{chr(10).join(trade_lines) if trade_lines else '  No trades on this date.'}

VERIFIED deterministic strengths:
{strength_section}

VERIFIED deterministic behavior flags:
{flag_section}

SESSION PATH ANALYSIS — deterministic data behind the Day Review realized-P&L chart:
{json.dumps(session_path, indent=2, sort_keys=True) if session_path else "  No usable timed session path."}
{diary_section}

Generate the daily coaching summary JSON."""

    groq_key = _valid_api_key("GROQ_API_KEY")
    anthropic_key = _valid_api_key("ANTHROPIC_API_KEY", "your_anthropic_api_key_here")
    errors = []

    if groq_key:
        try:
            result = _groq_daily_summary(user_content, groq_key)
            result["ai_provider"] = "groq"
            result["ai_model"] = GROQ_MODEL
        except Exception as exc:
            errors.append(f"Groq: {exc}")
            if not anthropic_key:
                raise RuntimeError("Groq daily coaching failed. " + errors[-1]) from exc
            result = None
    else:
        result = None

    if result is None and anthropic_key:
        try:
            result = _anthropic_daily_summary(user_content)
            result["ai_provider"] = "anthropic"
            result["ai_model"] = MODEL
        except Exception as exc:
            errors.append(f"Anthropic: {exc}")
            raise RuntimeError("Daily coaching AI failed. " + " | ".join(errors)) from exc

    if result is None:
        raise ValueError(
            "Daily coaching AI is not configured. Add GROQ_API_KEY to the server "
            "environment, or ANTHROPIC_API_KEY as an optional fallback."
        )
    result.setdefault("narrative", "")
    result.setdefault("strengths", [])
    result.setdefault("mistakes", [])
    result.setdefault("coaching", [])
    result.setdefault("trade_grades", [])
    result.setdefault("overall_grade", "N/A")
    result.setdefault("tomorrow_focus", [])
    result.setdefault("patterns", [])
    result.setdefault("mental_game", "")
    result["highlights"] = _normalize_highlights(result)

    # The AI diagnosis is intentionally not filtered or rewritten after generation.
    # Deterministic flags and recorded observations remain attached as supplemental
    # context so the UI can show them without suppressing the model's diagnosis.
    result["behavior_flags"] = context.get("behavior_flags") or []
    result["recorded_observations"] = context.get("recorded_observations") or []
    result["session_path_analysis"] = session_path
    result["diagnostic_input_version"] = 2
    result["diagnostic_mode"] = "unfiltered"
    result["analytics_engine_version"] = ANALYTICS_ENGINE_VERSION
    return result
