"""Synthetic descriptor-matching regression; no owner data or live provider calls."""
from finance_category_rules import possible_person_to_person

def test_mixed_case_p2p_descriptor_does_not_depend_on_capitalization():
    assert possible_person_to_person("ZELLE FROM ALPHA BETA")
    assert possible_person_to_person("Zelle from Alpha Beta")
    assert possible_person_to_person("zelle from alpha beta")

def test_account_descriptors_are_not_people():
    assert not possible_person_to_person("FROM PERSONAL SAVINGS")
    assert not possible_person_to_person("From Business Checking")
    assert not possible_person_to_person("From Retirement Account")
    assert not possible_person_to_person("Zelle from ALPHA LLC")
