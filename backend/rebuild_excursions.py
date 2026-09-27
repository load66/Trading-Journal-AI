from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

import main


async def rebuild(date_from: str, date_to: str, account_id: int | None, force: bool) -> dict:
    start = datetime.strptime(date_from, "%Y-%m-%d").date()
    end = datetime.strptime(date_to, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("date_to must be on or after date_from")

    conn = main.get_db()
    try:
        sql = "SELECT DISTINCT date FROM trades WHERE date >= ? AND date <= ?"
        params: list = [date_from, date_to]
        if account_id is not None:
            sql += " AND account_id = ?"
            params.append(account_id)
        sql += " ORDER BY date"
        dates = [row["date"] for row in conn.execute(sql, params).fetchall()]

        results = []
        for trade_date in dates:
            result = await main._calculate_excursions_for_date(
                trade_date,
                account_id,
                force,
                conn,
            )
            results.append(result)
            print(
                f"{trade_date}: computed={result.get('computed', 0)} "
                f"skipped={result.get('skipped', 0)}",
                flush=True,
            )

        return {
            "date_from": date_from,
            "date_to": date_to,
            "days_checked": len(dates),
            "computed": sum(int(r.get("computed") or 0) for r in results),
            "skipped": sum(int(r.get("skipped") or 0) for r in results),
        }
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recompute persisted excursion metrics with the current engine."
    )
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--account-id", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def cli() -> int:
    args = parse_args()
    if not main.ALPACA_KEY or main.ALPACA_KEY == "your_alpaca_api_key_here":
        raise RuntimeError("Alpaca market data is not configured.")

    summary = asyncio.run(
        rebuild(args.date_from, args.date_to, args.account_id, args.force)
    )
    print(f"SUMMARY {summary}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
