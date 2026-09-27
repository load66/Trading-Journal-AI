import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def fresh_main(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("APCA_API_KEY_ID", "test-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AUTH_REQUIRED", "false")

    for name in ("auth", "database", "main"):
        sys.modules.pop(name, None)

    import main
    return main


def add_trade(conn, main, account_id, group, date, ticker, *, setup=None, pnl=0.0):
    conn.execute(
        """INSERT INTO trades
           (account_id, trade_group, date, ticker, instrument_type, side,
            gross_pnl, net_pnl, commissions, executions, source, setup)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            account_id,
            group,
            date,
            ticker,
            "OPTION",
            "LONG",
            pnl,
            pnl,
            0.0,
            json.dumps([
                {"action": "BOT", "qty": 1, "price": 1.0, "date": date, "time": "09:40:00"},
                {"action": "SOLD", "qty": 1, "price": 1.2, "date": date, "time": "09:55:00"},
            ]),
            "imported",
            setup,
        ),
    )


def test_copy_journal_merge_only_reuses_safe_context(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Journal Copy", "day_trading", "schwab"),
        )
        add_trade(conn, main, account_id, "source", "2026-09-24", "QCOM", setup="A+ Pivot", pnl=125.0)
        add_trade(conn, main, account_id, "target", "2026-09-25", "NVDA", setup=None, pnl=-35.0)

        conn.execute(
            """INSERT INTO trade_analysis
               (trade_group, ticker, date, strategy, risk_per_trade, emotional_state,
                entry_reason, exit_reason, mistakes, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "source", "QCOM", "2026-09-24", "LE 10m 8 EMA", 250.0, "Focused",
                "Source entry", "Source exit", "Source mistake", "Source journal",
            ),
        )
        conn.execute(
            """INSERT INTO trade_analysis
               (trade_group, ticker, date, strategy, risk_per_trade, emotional_state,
                entry_reason)
               VALUES (?,?,?,?,?,?,?)""",
            (
                "target", "NVDA", "2026-09-25", None, 55.0, "Anxious",
                "Keep destination entry",
            ),
        )

        for tag_type, tag_value in (
            ("setup", "Outside Day"),
            ("mistake", "Chased"),
            ("strategy", "legacy-strategy-tag"),
        ):
            conn.execute(
                """INSERT INTO trade_tags (trade_group, tag_type, tag_value, source)
                   VALUES (?,?,?,'manual')""",
                ("source", tag_type, tag_value),
            )
        conn.execute(
            """INSERT INTO trade_tags (trade_group, tag_type, tag_value, source)
               VALUES (?,?,?,'manual')""",
            ("target", "emotion", "Patient"),
        )
        conn.commit()

        result = main.copy_trade_journal(
            "target",
            main.JournalCopyRequest(source_trade_group="source"),
            conn,
        )

        analysis = result["analysis"]
        assert analysis["entry_reason"] == "Keep destination entry"
        assert analysis["exit_reason"] == "Source exit"
        assert analysis["mistakes"] == "Source mistake"
        assert analysis["notes"] == "Source journal"
        assert analysis["strategy"] == "LE 10m 8 EMA"

        # Trade-specific fields must remain destination-specific.
        assert analysis["risk_per_trade"] == 55.0
        assert analysis["emotional_state"] == "Anxious"
        assert result["trade"]["net_pnl"] == -35.0
        assert result["trade"]["setup"] == "A+ Pivot"

        tags = {(tag["tag_type"], tag["tag_value"]) for tag in result["tags"]}
        assert ("emotion", "Patient") in tags
        assert ("setup", "Outside Day") in tags
        assert ("mistake", "Chased") in tags
        assert ("strategy", "legacy-strategy-tag") not in tags
        assert result["copied"]["tags_added"] == 2
    finally:
        conn.close()


def test_copy_journal_replace_mirrors_selected_review_and_structured_tags(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Journal Replace", "day_trading", "schwab"),
        )
        add_trade(conn, main, account_id, "source", "2026-09-24", "QCOM", setup="EMA Retest", pnl=80.0)
        add_trade(conn, main, account_id, "target", "2026-09-25", "TSLA", setup="Old Setup", pnl=15.0)

        conn.execute(
            """INSERT INTO trade_analysis
               (trade_group, ticker, date, strategy, risk_per_trade,
                entry_reason, exit_reason, mistakes, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "source", "QCOM", "2026-09-24", "LE", 300.0,
                "New entry", "New exit", None, "New journal",
            ),
        )
        conn.execute(
            """INSERT INTO trade_analysis
               (trade_group, ticker, date, strategy, risk_per_trade,
                entry_reason, exit_reason, mistakes, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                "target", "TSLA", "2026-09-25", "Old Strategy", 40.0,
                "Old entry", "Old exit", "Old mistake", "Old journal",
            ),
        )

        conn.execute(
            """INSERT INTO trade_tags (trade_group, tag_type, tag_value, source)
               VALUES (?,?,?,'manual')""",
            ("source", "setup", "PDH Break"),
        )
        for tag_type, tag_value in (("emotion", "FOMO"), ("mistake", "Old mistake tag")):
            conn.execute(
                """INSERT INTO trade_tags (trade_group, tag_type, tag_value, source)
                   VALUES (?,?,?,'manual')""",
                ("target", tag_type, tag_value),
            )
        conn.commit()

        result = main.copy_trade_journal(
            "target",
            main.JournalCopyRequest(source_trade_group="source", mode="replace"),
            conn,
        )

        analysis = result["analysis"]
        assert analysis["strategy"] == "LE"
        assert analysis["entry_reason"] == "New entry"
        assert analysis["exit_reason"] == "New exit"
        assert analysis["mistakes"] is None
        assert analysis["notes"] == "New journal"
        assert analysis["risk_per_trade"] == 40.0
        assert result["trade"]["setup"] == "EMA Retest"

        tags = {(tag["tag_type"], tag["tag_value"]) for tag in result["tags"]}
        assert tags == {("setup", "PDH Break")}
        assert result["copied"]["tags_removed"] == 2
        assert result["copied"]["tags_added"] == 1
    finally:
        conn.close()


def test_copy_journal_rejects_copying_trade_to_itself(monkeypatch, tmp_path):
    main = fresh_main(monkeypatch, tmp_path)
    main.init_db()
    conn = main.get_db()
    try:
        account_id = main.insert_and_get_id(
            conn,
            "INSERT INTO accounts (name, type, broker) VALUES (?,?,?)",
            ("Journal Self", "day_trading", "schwab"),
        )
        add_trade(conn, main, account_id, "same", "2026-09-25", "SPY")
        conn.commit()

        with pytest.raises(main.HTTPException) as exc:
            main.copy_trade_journal(
                "same",
                main.JournalCopyRequest(source_trade_group="same"),
                conn,
            )
        assert exc.value.status_code == 400
    finally:
        conn.close()
