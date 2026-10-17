"""Synthetic regression checks for deterministic Finances categorization."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from finance_category_rules import category_for, micro_deposit_refs, CATEGORIES
from bonus_simplefin import TRANSACTION_CATEGORIES, TransactionReviewBody


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
    ("CAPITAL ONE CRCARDPMT", "-200.00", "transfer"),
    ("CHASE EPAY PAYMENT", "-1000.00", "transfer"),
    ("AMEX AUTOPAY", "-180.00", "transfer"),
    ("CITI CARD PMT", "-250.00", "transfer"),
    ("Robinhood IRA contribution", "-1000.00", "transfer"),
    ("Fidelity brokerage deposit", "-500.00", "transfer"),
    ("Capital One Cafe purchase", "-20.00", "unreviewed"),

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
    assert micro_deposit_refs(rows) == {("a","m1"),("a","m2"),("a","m5")}


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


def test_loan_category_is_persistable_as_user_review():
    assert 'loan' in CATEGORIES
    assert 'loan' in TRANSACTION_CATEGORIES
    assert TransactionReviewBody(account='a'*64, transaction='b'*64, category='loan').category == 'loan'


def test_diverse_200_transaction_month_offline_coverage():
    # 40 distinct descriptions x 5 synthetic bank references: 190/200 categorized.
    examples=[
      ('Schreiber Payroll','1800'),('Direct deposit salary','2100'),('Interest earned','.18'),('Dividend','2.40'),
      ('ALDI','-41.22'),('Kroger Grocery','-52.10'),('Whole Foods','-18.24'),('Costco','-87.12'),
      ('Starbucks','-6.20'),('Chipotle','-14.70'),('DoorDash','-26.12'),('Panera','-11.22'),
      ('Shell Oil','-32.10'),('QuikTrip','-29.10'),('Lyft','-15.40'),('Parking','-7.00'),
      ('Netflix','-15.49'),('Spotify','-11.99'),('YouTube Premium','-13.99'),('Hulu','-9.99'),
      ('Liberty Utilities','-122.17'),('Water Utility','-35.10'),('Internet Provider','-55'),('Cellular bill','-62'),
      ('Amazon retail','-28.20'),('Target','-25.22'),('Best Buy','-42.22'),('Etsy','-17.10'),
      ('CVS pharmacy','-14.30'),('Dental clinic','-70'),('Hotel','-120.50'),('Southwest Air','-165.40'),
      ('Mortgage payment','-1100'),('Auto loan payment','-320'),('X Money Transfer','-250'),('Payment Thank You','-130'),
      ('RTP TST STTL','.05'),('MTG PYMT $0.00','0'),('Unfamiliar local merchant','-17.35'),('Opaque bank debit','-19.80')]
    fixture=[{'description':description+' REF '+str(1000+i),'amount':amount} for i in range(5) for description,amount in examples]
    assert len(fixture)==200
    unknown=sum(category_for(r['description'],r['amount'])[0]=='unreviewed' for r in fixture)
    assert unknown==10  # 5%, fixture-specific; never presented as a production guarantee.



@pytest.mark.parametrize("description,amount,expected,signal", [
    ("To Brokerage","-2000","transfer","investment_transfer"),
    ("To Citibank","-251","transfer","possible_transfer"),
    ("From Lafayette Fcu","500","transfer","possible_transfer"),
    ("To Personal Checking","-7379.04","transfer","possible_transfer"),
    ("From Personal Savings","7379.04","transfer","possible_transfer"),
    ("To Paypal","-428.17","transfer","possible_transfer"),
    ("U.S. Bank","-820.77","transfer","card_payment"),
    ("Capital One","-200","transfer","card_payment"),
    ("T-Mobile","-157.62","utilities",None),
    ("Apple","-249.52","shopping",None),
    ("Apple.com/Bill","-12.99","subscription",None),
    ("iCloud","-2.99","subscription",None),
    ("Unidentified fintech 2000","-2000","unreviewed",None),
    ("To an unknown account","-2000","unreviewed","possible_transfer"),
    ("Local bakery","-13.51","unreviewed",None)
])
def test_real_wording_financial_movements_and_purchase_merchants(description,amount,expected,signal):
    category,_,signals=category_for(description,amount)
    assert category==expected
    if signal is not None: assert signal in signals
    else: assert 'possible_transfer' not in signals
