"""A revision of a request we can't see, written against positions we can't see, must be flagged:
returning a partial order that looks complete is a wrong quote. The model may or may not flag it;
this code rule does, whatever the model said."""

from rfq.ingest import Email, Part
from rfq.threads import unanchored_revisions


def run(body, revises=None, parts=(), cls="rfq_revision"):
    emails = {"X": Email(id="X", body=body, parts=list(parts))}
    out = {"X": {"classification": cls, "revises": revises, "needs_review": False, "review_reason": None, "line_items": []}}
    unanchored_revisions(emails, out)
    return out["X"]


def test_positions_only_and_nothing_quoted_is_flagged_even_if_the_model_did_not():
    rec = run("Make #2 eight instead of six and kill #4. Rest stays the same.")
    assert rec["needs_review"] and "find the original" in rec["review_reason"]


def test_quoted_original_lets_the_model_rebuild_the_order():
    body = "make #3 twelve\n\nOn Mon, Nov 2, 2026 at 7:10 AM Lena <l@x.example> wrote:\n> 1. 6 ea flange\n> 2. 6 ea gasket\n"
    assert not run(body)["needs_review"]


def test_outlook_style_history_counts_as_quoted():
    body = "drop item 4 please\n\n________________________________\nFrom: Lena <l@x.example>\nSent: Monday\n1. 6 ea flange"
    assert not run(body)["needs_review"]


def test_original_attached_whole_counts():
    fwd = Part("original.eml", "message/rfc822", 1, "x", text="1. 6 ea flange")
    assert not run("kill #2", parts=[fwd])["needs_review"]


def test_a_complete_new_list_needs_no_original():
    assert not run("Updated list, replaces the earlier one:\n6 ea 3\" 300# WN flange\n12 ea 3\" 90 ell")["needs_review"]


def test_original_in_the_inbox_is_linked_not_flagged():
    assert not run("kill #4", revises="E003")["needs_review"]


def test_other_classes_are_untouched():
    assert not run("kill #4", cls="rfq")["needs_review"]
