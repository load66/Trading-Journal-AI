from __future__ import annotations

import argparse
import asyncio
import json
import sys

from database import get_db
from le_compliance import LE_COMPLIANCE_VERSION
from main import _build_trade_le_compliance, _load_cached_le_compliance


async def run(account_id: int, force: bool) -> dict:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT trade_group, date, ticker FROM trades WHERE account_id=? ORDER BY date, id",
            (account_id,),
        ).fetchall()

        cached = {
            str(item.get("trade_group")): item
            for item in _load_cached_le_compliance(conn, account_id)
            if item.get("trade_group")
        }

        processed = 0
        skipped = 0
        errors: list[dict] = []

        for index, row in enumerate(rows, start=1):
            trade_group = str(row["trade_group"])
            prior = cached.get(trade_group)

            if (
                not force
                and prior
                and prior.get("compliance_version") == LE_COMPLIANCE_VERSION
            ):
                skipped += 1
                continue

            try:
                review = await _build_trade_le_compliance(
                    conn,
                    trade_group,
                    include_ai=False,
                    persist=True,
                )
                compliance = review.get("compliance") or {}
                processed += 1
                print(
                    json.dumps(
                        {
                            "index": index,
                            "total": len(rows),
                            "trade_group": trade_group,
                            "ticker": row["ticker"],
                            "date": row["date"],
                            "classification": compliance.get("classification"),
                            "score": compliance.get("score"),
                        },
                        default=str,
                    ),
                    flush=True,
                )
            except Exception as exc:
                errors.append(
                    {
                        "trade_group": trade_group,
                        "ticker": row["ticker"],
                        "date": row["date"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                print(
                    json.dumps(
                        {
                            "index": index,
                            "total": len(rows),
                            "trade_group": trade_group,
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                        default=str,
                    ),
                    flush=True,
                )

        snapshots = _load_cached_le_compliance(conn, account_id)
        current = [
            item
            for item in snapshots
            if item.get("compliance_version") == LE_COMPLIANCE_VERSION
        ]

        result = {
            "account_id": account_id,
            "total_trades": len(rows),
            "processed": processed,
            "skipped_current": skipped,
            "errors": errors,
            "current_compliance_snapshots": len(current),
            "compliance_version": LE_COMPLIANCE_VERSION,
            "complete": len(current) == len(rows) and not errors,
        }
        print(json.dumps(result, indent=2, default=str), flush=True)
        return result
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backfill deterministic LE compliance for all trades in one account."
    )
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even when a current-version snapshot already exists.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = asyncio.run(run(args.account_id, args.force))
    if not result["complete"]:
        sys.exit(1)
