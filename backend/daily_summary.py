import json
import re

import httpx

from ai_analysis import (
    GROQ_API_URL,
    GROQ_MODEL,
    MODEL,
    _strip_json_fence,
    _valid_api_key,
    get_client,
    response_text,
)

DAILY_SUMMARY_PROMPT = """You are a professional trading coach producing an end-of-day performance review for a day trader.

You will receive structured data: the date, all trades with their analysis, the day's KPIs, and optionally a diary entry. Your job is to produce a thorough, honest coaching report in JSON format.

Rules:
- Reference actual tickers, prices, timestamps, and dollar amounts only when they are present in the supplied data.
- Never infer emotion, confidence, fear, tilt, intent, setup quality, stop discipline, chasing, breakout quality, or rule-breaking from P&L alone.
- A missing stop_loss, strategy, entry reason, exit reason, diary, or emotional_state means UNKNOWN — it is not evidence of a mistake.
- trade_grades must contain exactly one entry per trade provided (use the trade_group key).
- Grades measure PROCESS, never outcome. A profitable trade can be poor process and a losing trade can be good process.
- If a trade has process_evidence: none, its grade MUST be "N/A" and the reason must say there is insufficient process evidence.
- If there is no diary or emotional-state evidence for the day, mental_game MUST say "Insufficient evidence" and must not infer psychology from wins/losses.
- mistakes may contain only evidence-backed process problems from recorded fields. Do not turn missing documentation into a violation.
- overall_grade must be "N/A" when there is not enough process evidence to grade the day.
- Be direct and specific, but separate facts from interpretation.
- Return ONLY valid JSON — no markdown fences, no explanation

Required JSON schema:
{
  "narrative": "2-3 paragraph overview of the trading day — what happened, the flow of the session, any notable moments",
  "mental_game": "1-2 sentences on the trader's psychological state and emotional arc across the day",
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


def build_daily_context(conn, date: str, account_id) -> dict:
    """Fetch all data needed to generate a daily summary."""

    # Trades with joined analysis for the date
    sql = """
        SELECT t.id, t.trade_group, t.ticker, t.side, t.instrument_type,
               t.net_pnl, t.gross_pnl, t.commissions, t.executions,
               t.option_type, t.option_strike, t.option_expiry,
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
        trades.append(d)

    # Day KPIs
    all_pnl = [t.get('net_pnl') or 0 for t in trades]
    winners = [p for p in all_pnl if p > 0]
    losers = [p for p in all_pnl if p < 0]
    total_trades = len(trades)
    day_kpis = {
        "total_net_pnl": round(sum(all_pnl), 2),
        "total_trades": total_trades,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": round(len(winners) / total_trades * 100, 1) if total_trades else 0,
        "avg_win": round(sum(winners) / len(winners), 2) if winners else 0,
        "avg_loss": round(sum(losers) / len(losers), 2) if losers else 0,
        "profit_factor": round(sum(winners) / abs(sum(losers)), 2) if losers else None,
    }

    # All-time KPIs for context
    at_params = []
    at_sql = "SELECT net_pnl, gross_pnl FROM trades WHERE 1=1"
    if account_id is not None:
        at_sql += " AND account_id = ?"
        at_params.append(account_id)
    at_rows = conn.execute(at_sql, at_params).fetchall()
    at_all = [dict(r)['net_pnl'] or 0 for r in at_rows]
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

    return {
        "date": date,
        "trades": trades,
        "day_kpis": day_kpis,
        "alltime_kpis": alltime_kpis,
        "diary_summary": diary_summary,
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


def generate_daily_summary(context: dict) -> dict:
    """Generate a structured daily summary with Groq first, Anthropic fallback."""
    date = context["date"]
    trades = context["trades"]
    kpis = context["day_kpis"]
    alltime = context["alltime_kpis"]
    diary = context.get("diary_summary")

    # Format trades for the prompt
    trade_lines = []
    for t in trades:
        execs = t.get("executions", [])
        first_time = next((e.get("time", "") for e in execs), "")
        process_fields = [
            t.get("strategy"), t.get("stop_loss"), t.get("risk_per_trade"),
            t.get("entry_reason"), t.get("exit_reason"), t.get("mistakes"),
            t.get("emotional_state"),
        ]
        process_evidence = "present" if any(v not in (None, "", "N/A") for v in process_fields) else "none"
        line = (
            f"  - trade_group: {t['trade_group']} | ticker: {t['ticker']} | "
            f"process_evidence: {process_evidence} | "
            f"side: {t['side']} | net_pnl: ${t.get('net_pnl') or 0:.2f} | "
            f"strategy: {t.get('strategy') or 'N/A'} | "
            f"r_multiple: {t.get('r_multiple') or 'N/A'} | "
            f"stop_loss: {t.get('stop_loss') or 'N/A'} | "
            f"risk_per_trade: {t.get('risk_per_trade') or 'N/A'} | "
            f"entry_reason: {t.get('entry_reason') or 'N/A'} | "
            f"exit_reason: {t.get('exit_reason') or 'N/A'} | "
            f"mistakes: {t.get('mistakes') or 'none'} | "
            f"emotional_state: {t.get('emotional_state') or 'N/A'} | "
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

    # Evidence lock: model output may interpret supplied evidence, but it cannot
    # create process evidence that does not exist.
    by_group = {t["trade_group"]: t for t in trades}
    any_process_evidence = False
    for t in trades:
        fields = (
            t.get("strategy"), t.get("stop_loss"), t.get("risk_per_trade"),
            t.get("entry_reason"), t.get("exit_reason"), t.get("mistakes"),
            t.get("emotional_state"),
        )
        if any(v not in (None, "", "N/A") for v in fields):
            any_process_evidence = True

    locked_grades = []
    returned = {g.get("trade_group"): g for g in result.get("trade_grades", []) if isinstance(g, dict)}
    for trade_group, trade in by_group.items():
        g = returned.get(trade_group, {})
        fields = (
            trade.get("strategy"), trade.get("stop_loss"), trade.get("risk_per_trade"),
            trade.get("entry_reason"), trade.get("exit_reason"), trade.get("mistakes"),
            trade.get("emotional_state"),
        )
        has_process = any(v not in (None, "", "N/A") for v in fields)
        if not has_process:
            locked_grades.append({
                "trade_group": trade_group,
                "ticker": trade.get("ticker", ""),
                "grade": "N/A",
                "one_line": "Insufficient process evidence: no strategy, stop, entry/exit reason, mistake, or emotional state was recorded.",
            })
        else:
            locked_grades.append({
                "trade_group": trade_group,
                "ticker": trade.get("ticker", ""),
                "grade": g.get("grade") or "N/A",
                "one_line": g.get("one_line") or "Insufficient evidence to explain the process grade.",
            })
    result["trade_grades"] = locked_grades

    has_emotion_evidence = bool(diary) or any(
        (t.get("emotional_state") or "").strip() for t in trades
    )
    if not has_emotion_evidence:
        result["mental_game"] = (
            "Insufficient evidence — no diary or emotional-state data was recorded for this day."
        )

    if not any_process_evidence and not diary:
        result["mistakes"] = []
        result["overall_grade"] = "N/A"

    result["evidence_locked"] = True
    result["evidence_version"] = 2
    result["evidence_note"] = (
        "Missing process or psychological data is treated as unknown, never as a rule violation."
    )
    return result
