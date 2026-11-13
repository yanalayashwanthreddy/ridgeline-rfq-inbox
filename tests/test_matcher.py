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
    ('2" ball valve, CF8M, threaded, full port, 600 WOG', "RL-28513"),  # CF8M is cast 316
    ('a 90 elbow 2" 3000# A105 SW', "RL-33545"),                         # "a 90" is not ASTM A90
    ('B7 stud, A193, 3/4" x 4-1/2" w/ A194 2H nuts', "RL-41796"),        # bolting standards known
])
def test_unique_part_is_matched(desc, sku):
    r = m(desc)
    assert (r.match, r.sku) == ("matched", sku), r.reason


@pytest.mark.parametrize("desc", [
    '4" 300# RF blind flange, A105',          # stocked only in 150#: one attribute off
    '2" 150# RF weld neck flange, A350 LF2',  # low-temp grade, not A105
    '2" 150# RF weld neck flange, A694 F52',  # a standard on no list: unknown ASTM = not stocked
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


@pytest.mark.parametrize("desc, sku", [
    ('2" A-105 class 150 RF weld neck flange', "RL-11086"),   # A-105 is A105, not "class 105"
    ('1/2 inch A-105 600# WNRF', "RL-11012"),                 # WNRF = weld neck + raised face
    ('45 ell, 0.75", SW A-105 3M', "RL-10568"),                # 3M = 3000#
    ('5/8x3-1/2" B7 with 2 heavy hex nuts stud', "RL-16377"),  # the nuts come with the stud
])
def test_trade_shorthand(desc, sku):
    r = m(desc)
    assert (r.match, r.sku) == ("matched", sku), r.reason


def test_unasked_add_on_is_not_quoted():
    r = m('1" ball valve, THD, 316 SS, FP, 600 WOG')  # also stocked with a locking handle
    assert (r.match, r.sku) == ("matched", "RL-32583") and "locking handle" in r.reason


@pytest.mark.parametrize("desc", [
    '0.75" check valve, WP316L, socket weld, 800#',  # fitting grade on a valve: we stock F316L
    '45 ell, 4", sch 10s BW 316 SS/graphite',         # a gasket material on an elbow
])
def test_named_grade_must_hold(desc):
    assert m(desc).match == "not_in_catalog"


def test_40s_names_stainless_never_carbon():
    r = m('8 inch SCH 40S plain end pipe')  # no material named: only stainless parts may fit
    assert r.match != "matched" or "A312" in r.reason


def test_generated_lines_have_no_confident_wrong():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    out = subprocess.run([sys.executable, str(root / "scripts" / "matcher_fuzz.py"), "--per-part", "1", "--seed", "11"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert "confident wrong matches: 0" in out, out


# ---- advice: never changes qty or the match, only tells the rep


def _run(lines):
    parsed = {"E1": {"line_items": [dict(ln) for ln in lines]}}
    match_all(parsed, CAT)
    return parsed["E1"]["line_items"]


def test_joints_of_pipe_sold_per_foot_are_converted_when_length_is_stated():
    (ln,) = _run([{"description": '1" SCH 80 A106B seamless pipe, plain ends, 21 ft', "raw": "20 jts", "qty": 20, "uom": None}])
    assert ln["match"] == "matched" and "20 × 21 ft lengths = 420 FT" in ln["match_reason"]
    assert ln["qty"] == 20, "qty stays as the customer stated it (README)"


def test_joints_without_a_length_say_ask():
    (ln,) = _run([{"description": '2" SCH 40 A53B ERW pipe, plain ends', "raw": "12 jts", "qty": 12, "uom": "EA"}])
    assert "no length is stated" in ln["match_reason"]


def test_same_unit_gets_no_advice():
    (ln,) = _run([{"description": '2" SCH 40 A53B ERW pipe, plain ends', "raw": "200 ft", "qty": 200, "uom": "FT"}])
    assert "sold per" not in ln["match_reason"]


def test_ambiguous_line_gets_a_suggestion_from_the_rest_of_the_request():
    lines = _run([{"description": '4" 300# RF weld neck flange, A105', "qty": 8, "uom": "EA"},
                  {"description": '4" spiral wound gasket, 316/graphite', "qty": 16, "uom": "EA"}])
    gasket = lines[1]
    assert gasket["match"] == "ambiguous" and gasket["sku"] is None, "a suggestion is not a match"
    assert gasket["candidates"][0] == "RL-31103" and "likely RL-31103" in gasket["match_reason"]


def test_no_suggestion_without_evidence():
    (ln,) = _run([{"description": '4" spiral wound gasket, 316/graphite', "qty": 16, "uom": "EA"}])
    assert ln["match"] == "ambiguous" and "likely" not in ln["match_reason"]
