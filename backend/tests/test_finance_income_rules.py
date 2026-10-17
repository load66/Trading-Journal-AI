"""Synthetic money movement and paycheck regression cases; no provider calls."""
from finance_category_rules import category_for
from finance_income_rules import annotate_paychecks

DAY=86400

def row(description,amount,at,category=None,source='automatic'):
    inferred,confidence,signals=category_for(description,amount)
    return dict(description=description,amount=amount,posted=at,pending=False,
                category=category or inferred,categorySource=source,
                signals=signals,confidence=confidence,merchant=description)

def test_schreiber_biweekly_repeats_become_paychecks():
    rows=[row('Schreiber Foods','1061.24',DAY*100),
          row('Schreiber Foods','1061.24',DAY*114),
          row('Schreiber Foods','1175.00',DAY*128)]
    annotate_paychecks(rows)
    assert all(r['category']=='income' and 'paycheck' in r['signals'] for r in rows)
    assert all(r['merchant']=='Schreiber Foods' for r in rows)

def test_single_company_candidate_requires_user_confirmation():
    row_one=row('Schreiber Foods','1061.24',DAY*100)
    annotate_paychecks([row_one])
    assert row_one['category']=='unreviewed'
    assert 'possible_paycheck' in row_one['signals']

def test_unknown_person_is_not_paycheck():
    stranger=row('From Example Person','1061.24',DAY*100)
    annotate_paychecks([stranger])
    assert stranger['category']=='transfer'
    assert 'possible_p2p' in stranger['signals']
    assert 'paycheck' not in stranger['signals']
    assert 'possible_paycheck' not in stranger['signals']

def test_transfer_examples_excluded_before_paycheck():
    descriptions=['Savings (0793) → Checking (2033)','Savings (0793) → Checking (2033)',
                  'From Schwab Brokerage','To Citibank','From Lafayette Fcu','To Personal Checking']
    values=['903.00','1223.97','903.00','-903.00','903.00','-1223.97']
    rows=[row(name,value,DAY*(100+i)) for i,(name,value) in enumerate(zip(descriptions,values))]
    annotate_paychecks(rows)
    assert all(r['category']=='transfer' for r in rows)
    assert all('paycheck' not in r['signals'] for r in rows)
    assert 'investment_transfer' in rows[2]['signals']
    assert 'ownership_unconfirmed' in rows[3]['signals']
    assert 'ownership_unconfirmed' in rows[4]['signals']

def test_schwab_savings_checking_chain_has_zero_income():
    rows=[row('From Schwab Brokerage','903.00',DAY*100),
          row('To Savings (0793)','-903.00',DAY*100),
          row('Savings (0793) → Checking (2033)','903.00',DAY*101)]
    annotate_paychecks(rows)
    assert sum(float(r['amount']) for r in rows if r['category']=='income')==0
    assert all(r['category']=='transfer' for r in rows)

def test_ach_ppd_employer_credit_and_user_override():
    explicit=row('ACH PPD Schreiber Foods','1061.24',DAY*100)
    forced=row('Schreiber Foods','1061.24',DAY*114,'other','user')
    annotate_paychecks([explicit,forced])
    assert explicit['category']=='income'
    assert 'paycheck' in explicit['signals']
    assert forced['category']=='other'

def test_repeated_unknown_person_is_not_promoted_to_paycheck():
    entries=[row('Example Person','1061.24',DAY*100),row('Example Person','1061.24',DAY*114)]
    annotate_paychecks(entries)
    assert all(x['category']=='unreviewed' and 'paycheck' not in x['signals'] for x in entries)

def test_user_confirmed_employer_rule_adds_reviewable_dd_signal():
    """A user-confirmed income rule gives a candidate, never bonus qualification."""
    company=row('Schreiber Foods','1061.24',DAY*140,'income','user_rule')
    annotate_paychecks([company])
    assert company['category']=='income'
    assert 'possible_direct_deposit' in company['signals']
    overridden=row('Schreiber Foods','1061.24',DAY*140,'other','user')
    annotate_paychecks([overridden])
    assert 'possible_direct_deposit' not in overridden['signals']


def test_cross_account_same_employer_does_not_infer_a_paycheck_on_target_account():
    """A company credit in another account cannot complete the pay pattern."""
    first=row('Schreiber Foods','1061.24',DAY*100)
    second=row('Schreiber Foods','1061.24',DAY*114)
    first['accountKey']='linked-savings'
    second['accountKey']='unlinked-checking'
    annotate_paychecks([first,second])
    assert all('possible_direct_deposit' not in x['signals'] for x in (first,second))
    assert all('possible_paycheck' in x['signals'] for x in (first,second))

def test_two_credits_to_same_linked_account_emit_direct_deposit_candidates():
    rows=[row('Schreiber Foods','1061.24',DAY*100),
          row('Schreiber Foods','1061.24',DAY*114)]
    for candidate in rows:
        candidate['accountKey']='linked-checking'
    annotate_paychecks(rows)
    assert all('possible_direct_deposit' in x['signals'] for x in rows)
