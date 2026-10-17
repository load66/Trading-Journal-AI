"""Synthetic regression checks for deterministic Finances categorization."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from finance_category_rules import category_for, micro_deposit_refs, CATEGORIES


@pytest.mark.parametrize("description,amount,expected", [
    ("ACCTVERIFY", "0.37", "verification"),
    ("ACCT VERIFY", "-0.37", "verification"),
    ("RTP TST STTL", "0.04", "verification"),
    ("RTPTSTSTTL", "-0.04", "verification"),
    ("TRIAL DEP", "0.25", "verification"),
    ("MICRO DEP", "0.12", "verification"),
    ("PRENOTE", "0.00", "verification"),
    ("MTG PYMT $0.00", "0.00", "verification"),
    ("Payment Thank You", "-98.27", "transfer"),
    ("X Money Transfer", "-120", "transfer"),
    ("CREDIT CARD AUTOPAY", "250.00", "transfer"),
    ("Zelle to myself", "-35", "transfer"),
    ("My mortgage payment", "-1300", "housing"),
    ("Auto loan payment", "-315", "loan"),
    ("Employer payroll", "1980", "income"),
    ("Interest earned", "0.08", "income"),
    ("Dividend", "2.52", "income"),
    ("Merchant refund", "15.50", "refund"),
    ("ALDI #0123", "-42.30", "groceries"),
    ("Netflix monthly", "-9.99", "subscription"),
    ("Starbucks", "-5.16", "dining"),
    ("Shell Oil", "-30.25", "transportation"),
    ("Liberty Utilities", "-132.70", "utilities"),
    ("Amazon retail", "-35", "shopping"),
    ("Doctor clinic", "-89.75", "health"),
    ("Hotel", "-112.00", "travel"),
    ("Service charge", "-3.00", "fees"),
    ("Transaction", "-19.83", "unreviewed"),
    ("Venmo to someone", "-18.25", "unreviewed")
])
def test_classify_examples(description, amount, expected):
    category, confidence, signals = category_for(description, amount)
    assert category == expected
    assert category in CATEGORIES
    assert confidence in ("high", "medium", "low")
    assert isinstance(signals, list)


def test_pending_and_bonus_candidates_stay_unconfirmed():
    assert category_for("Employer payroll", "450", pending=True)[0] == "unreviewed"
    assert category_for("Promotion reward", "400", kind="possible_bonus")[0] == "bonus"
    assert category_for("Bonus clawback", "-400", kind="possible_reversal")[0] == "unreviewed"
    assert category_for("No description", "NaN")[0] == "unreviewed"


def test_microdeposits_only_same_account_source_and_close_in_time():
    rows = [
        {"accountKey":"a","id":"m1","posted":100000,"amount":"0.13","description":"Synthetic Verify"},
        {"accountKey":"a","id":"m2","posted":100000+3600,"amount":"0.24","description":"Synthetic Verify"},
        {"accountKey":"b","id":"m3","posted":100000+3600,"amount":"0.45","description":"Synthetic Verify"},
        {"accountKey":"a","id":"m4","posted":100000+12*86400,"amount":"0.15","description":"Synthetic Verify"},
        {"accountKey":"a","id":"m5","posted":100000+3600,"amount":"-0.37","description":"Synthetic Verify"},
    ]
    assert micro_deposit_refs(rows) == {("a","m1"),("a","m2")}


def test_200_realistic_synthetic_examples_below_ten_percent_unreviewed():
    descriptions = [
        ("Employer Payroll Direct Deposit", "1750.00"),
        ("ALDI Grocery", "-63.18"),
        ("Shell Oil gas station", "-48.12"),
        ("Netflix monthly subscription", "-15.49"),
        ("Liberty Utilities electric", "-74.56"),
        ("Amazon retail", "-27.90"),
        ("Mortgage payment", "-865.34"),
        ("X Money Transfer", "-100.00"),
        ("Starbucks", "-4.57"),
        ("Merchant Refund", "8.99"),
    ]
    rows = [category_for(*descriptions[i % len(descriptions)]) for i in range(200)]
    assert sum(category == "unreviewed" for category, _, _ in rows) < 20
    # This measures these synthetic descriptions, not general real-bank accuracy.


def test_malformed_and_empty_are_safe():
    assert category_for("", None)[0] == "unreviewed"
    assert category_for("", "0.00")[0] == "verification"
