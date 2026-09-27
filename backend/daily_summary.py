import hashlib
import json
import httpx

from behavior_rules import detect_daily_flags, deterministic_strengths, recorded_observations
from trade_metrics import trade_is_closed
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
    }
    canonical = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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

    return {
        "date": date,
        "trades": trades,
        "day_kpis": day_kpis,
        "alltime_kpis": alltime_kpis,
        "diary_summary": diary_summary,
        "behavior_flags": behavior_flags,
        "verified_strengths": verified_strengths,
        "recorded_observations": recorded,
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
    result["diagnostic_mode"] = "unfiltered"
    result["analytics_engine_version"] = ANALYTICS_ENGINE_VERSION
    return result
