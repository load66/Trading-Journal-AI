from __future__ import annotations

from collections import defaultdict
from typing import Any

from le_learning import build_le_learning_core


MIN_STABLE_SAMPLE = 8


def _money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fmt_money(value: Any) -> str:
    return '$' + format(_money(value), ',.2f')


def _stats(rows: list[dict]) -> dict:
    count = len(rows)
    wins = [row for row in rows if _money(row.get('net_pnl')) > 0]
    losses = [row for row in rows if _money(row.get('net_pnl')) < 0]
    gross_profit = sum(_money(row.get('net_pnl')) for row in wins)
    gross_loss = abs(sum(_money(row.get('net_pnl')) for row in losses))
    net = sum(_money(row.get('net_pnl')) for row in rows)
    return {
        'trades': count,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / count * 100, 1) if count else 0.0,
        'net_pnl': round(net, 2),
        'avg_pnl': round(net / count, 2) if count else 0.0,
        'gross_profit': round(gross_profit, 2),
        'gross_loss': round(gross_loss, 2),
        'profit_factor': round(gross_profit / gross_loss, 2) if gross_loss else None,
    }


def _check_map(snapshot: dict) -> dict[str, dict]:
    return {
        str(item.get('id')): item
        for item in (snapshot.get('checks') or [])
        if item.get('id')
    }


def _cohort_flags(snapshot: dict) -> dict[str, bool]:
    checks = _check_map(snapshot)
    level = checks.get('level_broken') or {}
    level_detail = str(level.get('detail') or '')
    level_pass = level.get('status') == 'pass'
    trend_pass = (checks.get('trend_established') or {}).get('status') == 'pass'
    aligned_pass = (checks.get('ema_aligned') or {}).get('status') == 'pass'
    snug_pass = (checks.get('ema_snug') or {}).get('status') == 'pass'
    sign_pass = (checks.get('market_sign') or {}).get('status') == 'pass'
    outside_chop = (checks.get('not_chop_hour') or {}).get('status') == 'pass'

    outside_day = level_pass and level_detail in {'PDH, PMH', 'PDL, PML'}
    premarket_only = level_pass and level_detail in {'PMH', 'PML'}
    previous_day_only = level_pass and level_detail in {'PDH', 'PDL'}

    return {
        'outside_day': outside_day,
        'premarket_only': premarket_only,
        'previous_day_only': previous_day_only,
        'level_ema_snug': level_pass and snug_pass,
        'level_trend_ema': level_pass and trend_pass and aligned_pass and snug_pass,
        'level_trend_ema_sign': level_pass and trend_pass and aligned_pass and snug_pass and sign_pass,
        'outside_ema_sign': outside_day and snug_pass and sign_pass,
        'outside_ema_sign_no_chop': outside_day and snug_pass and sign_pass and outside_chop,
    }


COHORTS = (
    ('outside_day', 'Outside Day — both directional levels broken'),
    ('level_ema_snug', 'Level break + EMA snug'),
    ('level_trend_ema', 'Level + established trend + EMA aligned + snug'),
    ('outside_ema_sign', 'Outside Day + EMA snug + Market Sign'),
    ('outside_ema_sign_no_chop', 'Outside Day + EMA snug + Market Sign + outside Chop Hour'),
    ('level_trend_ema_sign', 'Level + established trend + EMA aligned + snug + Market Sign'),
    ('premarket_only', 'Premarket-only break — PMH/PML without PDH/PDL'),
    ('previous_day_only', 'Previous-day-only break — PDH/PDL'),
)


def build_le_diagnosis(
    trades: list[dict],
    snapshots: list[dict],
    *,
    compliance_version: str,
) -> dict:
    '''Aggregate deterministic LE compliance into a journal-wide diagnosis.'''
    trade_by_group = {
        str(trade.get('trade_group')): trade
        for trade in trades
        if trade.get('trade_group')
    }
    current_by_group = {
        str(snapshot.get('trade_group')): snapshot
        for snapshot in snapshots
        if snapshot.get('trade_group')
        and snapshot.get('compliance_version') == compliance_version
        and str(snapshot.get('trade_group')) in trade_by_group
    }

    audited_rows = []
    for group, snapshot in current_by_group.items():
        trade = dict(trade_by_group[group])
        trade['snapshot'] = snapshot
        audited_rows.append(trade)

    audited_rows.sort(
        key=lambda row: (str(row.get('date') or ''), int(row.get('id') or 0)),
        reverse=True,
    )

    total = len(trades)
    audited = len(audited_rows)
    stale = sum(
        1
        for snapshot in snapshots
        if snapshot.get('trade_group') in trade_by_group
        and snapshot.get('compliance_version') != compliance_version
    )

    classification_groups: dict[str, list[dict]] = defaultdict(list)
    rule_groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    rule_labels: dict[str, str] = {}
    rule_manual_counts: dict[str, int] = defaultdict(int)
    rule_conflict_counts: dict[str, int] = defaultdict(int)
    cohort_groups: dict[str, list[dict]] = defaultdict(list)
    user_setup_groups: dict[str, list[dict]] = defaultdict(list)
    manual_evidence_trades = 0
    manual_override_count = 0
    manual_conflict_count = 0
    trade_rows = []

    for row in audited_rows:
        snapshot = row['snapshot']
        classification_groups[str(snapshot.get('classification') or 'UNKNOWN')].append(row)
        checks = _check_map(snapshot)

        for rule_id, check in checks.items():
            status = str(check.get('status') or 'unknown')
            rule_labels[rule_id] = str(check.get('label') or rule_id)
            rule_groups[rule_id][status].append(row)
            if check.get('manual_override'):
                rule_manual_counts[rule_id] += 1
            if check.get('conflict_with_system'):
                rule_conflict_counts[rule_id] += 1

        flags = _cohort_flags(snapshot)
        for cohort_id, _label in COHORTS:
            if flags.get(cohort_id):
                cohort_groups[cohort_id].append(row)

        manual = snapshot.get('manual_le_evidence') or {}
        if manual.get('recognized_tags'):
            manual_evidence_trades += 1
        manual_override_count += int(manual.get('override_count') or 0)
        manual_conflict_count += int(manual.get('conflict_count') or 0)
        for setup_name in manual.get('setup_tags') or []:
            user_setup_groups[str(setup_name)].append(row)

        trade_rows.append({
            'trade_group': row.get('trade_group'),
            'date': row.get('date'),
            'ticker': row.get('ticker'),
            'side': row.get('side'),
            'net_pnl': _money(row.get('net_pnl')),
            'classification': snapshot.get('classification'),
            'classification_label': snapshot.get('classification_label'),
            'score': snapshot.get('score') or {},
            'failed_rule_ids': snapshot.get('failed_rule_ids') or [],
            'unknown_rule_ids': snapshot.get('unknown_rule_ids') or [],
            'manual_le_evidence': {
                'override_count': int(manual.get('override_count') or 0),
                'conflict_count': int(manual.get('conflict_count') or 0),
                'setup_tags': manual.get('setup_tags') or [],
                'recognized_tags': manual.get('recognized_tags') or [],
            },
            'generated_at': snapshot.get('generated_at'),
        })

    classification_stats = [
        {'classification': name, **_stats(rows)}
        for name, rows in classification_groups.items()
    ]
    classification_stats.sort(key=lambda item: (-item['trades'], item['classification']))

    rules = []
    for rule_id, statuses in rule_groups.items():
        passed = _stats(statuses.get('pass', []))
        failed = _stats(statuses.get('fail', []))
        unknown = _stats(statuses.get('unknown', []))
        evaluated = passed['trades'] + failed['trades']
        rules.append({
            'id': rule_id,
            'label': rule_labels.get(rule_id, rule_id),
            'pass': passed,
            'fail': failed,
            'unknown': unknown,
            'evaluated_trades': evaluated,
            'coverage_pct': round(evaluated / audited * 100, 1) if audited else 0.0,
            'user_backed_trades': rule_manual_counts.get(rule_id, 0),
            'user_system_conflicts': rule_conflict_counts.get(rule_id, 0),
        })
    rules.sort(key=lambda item: (-item['fail']['trades'], item['label']))

    cohorts = []
    for cohort_id, label in COHORTS:
        rows = cohort_groups.get(cohort_id, [])
        if not rows:
            continue
        cohorts.append({
            'id': cohort_id,
            'label': label,
            **_stats(rows),
            'stable_sample': len(rows) >= MIN_STABLE_SAMPLE,
        })
    cohorts.sort(key=lambda item: (-item['net_pnl'], -item['trades']))

    user_confirmed_setups = []
    for setup_name, rows in user_setup_groups.items():
        user_confirmed_setups.append({
            'id': setup_name.lower().replace(' ', '_'),
            'label': setup_name,
            **_stats(rows),
            'stable_sample': len(rows) >= MIN_STABLE_SAMPLE,
            'evidence_source': 'USER_MANUAL',
        })
    user_confirmed_setups.sort(key=lambda item: (-item['net_pnl'], -item['trades'], item['label']))

    stable_positive = [row for row in cohorts if row['stable_sample'] and row['net_pnl'] > 0]
    most_profitable = max(
        stable_positive,
        key=lambda row: (row['net_pnl'], row['trades']),
        default=None,
    )
    highest_quality = max(
        stable_positive,
        key=lambda row: (
            row['profit_factor'] if row['profit_factor'] is not None else float('inf'),
            row['win_rate'],
            row['trades'],
        ),
        default=None,
    )

    leak_candidates = []
    for rule in rules:
        fail = rule['fail']
        if fail['trades'] >= 5 and fail['net_pnl'] < 0:
            leak_candidates.append({'id': rule['id'], 'label': rule['label'], **fail})
    leak_candidates.sort(key=lambda row: (row['net_pnl'], -row['trades']))
    biggest_leak = leak_candidates[0] if leak_candidates else None

    evidence_gaps = [
        {
            'id': rule['id'],
            'label': rule['label'],
            'unknown_trades': rule['unknown']['trades'],
            'unknown_pct': round(rule['unknown']['trades'] / audited * 100, 1) if audited else 0.0,
        }
        for rule in rules
        if rule['unknown']['trades']
    ]
    evidence_gaps.sort(key=lambda row: (-row['unknown_trades'], row['label']))

    findings = []
    if most_profitable:
        findings.append({
            'kind': 'edge',
            'title': 'Most profitable proven LE cohort',
            'text': (
                most_profitable['label'] + ' produced ' + _fmt_money(most_profitable['net_pnl']) +
                ' across ' + str(most_profitable['trades']) + ' trades with ' +
                format(most_profitable['win_rate'], '.1f') + '% wins and ' +
                str(most_profitable['profit_factor'] if most_profitable['profit_factor'] is not None else '—') +
                ' profit factor.'
            ),
        })
    if biggest_leak:
        findings.append({
            'kind': 'leak',
            'title': 'Largest verified LE leak',
            'text': (
                biggest_leak['label'] + ' failed on ' + str(biggest_leak['trades']) +
                ' audited trades with ' + _fmt_money(biggest_leak['net_pnl']) +
                ' combined realized P&L. This is an association, not proof of causation.'
            ),
        })
    if highest_quality and (not most_profitable or highest_quality['id'] != most_profitable['id']):
        findings.append({
            'kind': 'quality',
            'title': 'Highest-quality stable cohort',
            'text': (
                highest_quality['label'] + ' has ' + format(highest_quality['win_rate'], '.1f') +
                '% wins and ' + str(highest_quality['profit_factor'] if highest_quality['profit_factor'] is not None else '—') +
                ' profit factor across ' + str(highest_quality['trades']) + ' trades.'
            ),
        })
    if manual_evidence_trades:
        findings.append({
            'kind': 'manual',
            'title': 'User-confirmed LE evidence',
            'text': (
                str(manual_evidence_trades) + ' audited trade(s) contain authoritative manual LE tags, '
                + str(manual_override_count) + ' final rule/finding result(s) were user-backed, and '
                + str(manual_conflict_count) + ' system-vs-user conflict(s) were preserved for review.'
            ),
        })

    if evidence_gaps:
        top_gap = evidence_gaps[0]
        findings.append({
            'kind': 'coverage',
            'title': 'Largest evidence gap',
            'text': (
                top_gap['label'] + ' is Unknown on ' + str(top_gap['unknown_trades']) +
                ' audited trades (' + format(top_gap['unknown_pct'], '.1f') +
                '%). Unknown is never counted as Pass or Fail.'
            ),
        })

    learning_core = build_le_learning_core(list(current_by_group.values()))

    return {
        'compliance_version': compliance_version,
        'learning_core': learning_core,
        'total_trades': total,
        'audited_trades': audited,
        'missing_trades': max(0, total - audited),
        'stale_snapshots': stale,
        'coverage_pct': round(audited / total * 100, 1) if total else 0.0,
        'overall': _stats(trades),
        'classifications': classification_stats,
        'rules': rules,
        'cohorts': cohorts,
        'user_confirmed_setups': user_confirmed_setups,
        'manual_evidence': {
            'trades': manual_evidence_trades,
            'override_count': manual_override_count,
            'conflict_count': manual_conflict_count,
            'authoritative_source': 'USER_MANUAL',
        },
        'most_profitable_cohort': most_profitable,
        'highest_quality_cohort': highest_quality,
        'biggest_verified_leak': biggest_leak,
        'evidence_gaps': evidence_gaps,
        'findings': findings,
        'recent_trades': trade_rows[:50],
        'note': (
            'LE diagnosis uses deterministic system evidence plus authoritative recognized manual LE tags. '
            'Manual user assertions win the final status for the exact mapped concept while the prior system result is preserved. '
            'Missing evidence remains Unknown. Rule and cohort P&L are descriptive associations and do not prove causation.'
        ),
    }