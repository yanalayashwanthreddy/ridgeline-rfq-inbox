"""Matcher rules, on the real catalog, with phrasings that are not in the dev inbox.

Each test names the rule it protects. The costly failure is a confident wrong part, so most
tests are traps: right product and size, one attribute off.
"""

from pathlib import Path

import pytest

from rfq.matcher import load_catalog, match_all, match_line, read_line

CAT = load_catalog(Path(__file__).resolve().parents[1] / "data" / "catalog.csv")


def m(desc):
    return match_line(desc, CAT)


@pytest.mark.parametrize("desc, sku", [
    ('2" 150# RF weld neck flange, A105', "RL-11086"),
    ('2" class 300 RF WN flange A105', "RL-28550"),                    # class N == N#
    ('1.5" 3000# socket weld tee, A105', "RL-23888"),                   # decimal size
    ('1 1/2" 3000# socket weld tee, A105', "RL-23888"),                 # spaced fraction
    ('4" elbow, 90 LR, BW, SCH 40, WPB', "RL-36135"),                   # angle after the noun
    ('3/4"-10 x 4-1/2" stud B7 w/ 2H nuts', "RL-41796"),                # thread pitch dropped
    ('1" sch 80 A312 TP316L pipe, PE', "RL-12899"),                     # SCH 80 == 80S up to 8"
    ('2" ball valve, 316 SS, threaded, full port, 600 WOG', "RL-28513"),
])
def test_unique_part_is_matched(desc, sku):
    r = m(desc)
    assert (r.match, r.sku) == ("matched", sku), r.reason


@pytest.mark.parametrize("desc", [
    '4" 300# RF blind flange, A105',          # stocked only in 150#: one attribute off
    '2" 150# RF weld neck flange, A350 LF2',  # low-temp grade, not A105
    '2" 3000# 321 SS threaded coupling',      # a stainless grade the catalog lacks
    '1" copper 90 elbow, sweat',              # material not stocked at all
    '2" tee, sch 40, A234 WPB, grooved',      # end type not stocked
    '12" sch 40 A312 TP316L pipe, PE',        # 40 vs 40S differ above 8"
    '6" butterfly valve, lug',                # size not stocked
])
def test_one_attribute_off_is_not_in_catalog(desc):
    r = m(desc)
    assert r.match == "not_in_catalog" and r.sku is None, r.reason


@pytest.mark.parametrize("desc", [
    '2" 150# flange, A105, RF',               # slip-on, weld neck or blind?
    '2" ball valve, 316 SS, threaded',        # port and pressure rating unstated
])
def test_unstated_choice_is_ambiguous(desc):
    r = m(desc)
    assert r.match == "ambiguous" and r.sku is None and len(r.candidates) > 1, r.reason


def test_unreadable_line_is_never_matched():
    assert m("misc. hardware as discussed").match == "ambiguous"


def test_not_in_catalog_reason_names_the_nearest_part():
    r = m('4" 300# RF blind flange, A105')
    assert "RL-43757" in r.reason and "300#" in r.reason


def test_black_pipe_is_uncoated_carbon_not_malleable():
    w = read_line('2" schedule 40 A53B ERW black pipe, plain ends')
    assert w.products == {"pipe"} and w.ends == {"PE"}
    assert m('2" schedule 40 A53B ERW black pipe, plain ends').match == "matched"


def test_matcher_error_never_breaks_the_parse():
    parsed = {"E1": {"line_items": [{"description": None}]}}
    match_all(parsed, CAT)
    assert parsed["E1"]["line_items"][0]["match"] == "ambiguous"
