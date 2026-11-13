"""Lines read from pictures get an independent second reading; code compares the two.

A fake model gives the first answer and a scripted second reading, so each disagreement can be
tested offline: a quantity (the 6/8 misread), a missing line, a different count; and the cases
that must NOT flag: agreement, a nameplate whose quantity is only in the text, a failed call.
"""

from __future__ import annotations

import io

from PIL import Image as PILImage

from rfq.llm import CallResult, ModelUnavailable
from rfq.pipeline import process_one
from rfq.prompt import REREAD_INSTRUCTIONS
from tests.test_ingest import _eml

FIRST = [("6 - 2\" 90 ell SW 3000#", 6), ("4 - 2\" tee SW 3000#", 4)]


def _photo() -> bytes:
    b = io.BytesIO()
    PILImage.new("RGB", (300, 200), "white").save(b, "JPEG")
    return b.getvalue()


class Fake:
    model, effort = "fake", "none"

    def __init__(self, second, fail=False):
        self.second, self.fail = second, fail

    def request(self, instructions, content, schema):
        return {"reread": instructions == REREAD_INSTRUCTIONS}

    def call(self, req, label=""):
        usage = {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_tokens": 0}
        if req["reread"]:
            if self.fail:
                raise ModelUnavailable("down")
            return CallResult({"lines": [{"source": "list.jpg", "raw": r, "qty": q} for r, q in self.second]}, usage, 0.0, False, 0.0, "b")
        data = {"classification_reason": "asks for prices", "classification": "rfq", "needs_review": False,
                "review_reason": None,
                "line_items": [{"raw": r, "description": r, "qty": q, "uom": "EA", "source": "list.jpg"} for r, q in FIRST]}
        return CallResult(data, usage, 0.0, False, 0.0, "a")


def run(tmp_path, second, fail=False):
    _, rec, stats = process_one(_eml(tmp_path, [("list.jpg", "image/jpeg", _photo())]), Fake(second, fail))
    return rec, stats


def test_two_readings_that_agree_change_nothing(tmp_path):
    rec, stats = run(tmp_path, FIRST)
    assert not rec["needs_review"] and stats["second_read"] == "agrees"


def test_a_quantity_read_differently_is_flagged_and_named(tmp_path):
    rec, _ = run(tmp_path, [("6 - 2\" 90 ell SW 3000#", 8), FIRST[1]])
    assert rec["needs_review"] and "quantity read as 6 and as 8" in rec["review_reason"]
    assert [ln["qty"] for ln in rec["line_items"]] == [6, 4], "lines stay as first read; a person decides"


def test_a_line_seen_only_once_is_flagged(tmp_path):
    rec, _ = run(tmp_path, FIRST[:1])
    assert rec["needs_review"] and "1 the second time" in rec["review_reason"]


def test_quantity_only_in_the_text_is_not_a_disagreement(tmp_path):
    rec, _ = run(tmp_path, [(r, None) for r, _ in FIRST])  # nameplate-style: no qty in the picture
    assert not rec["needs_review"]


def test_a_failed_second_reading_keeps_the_first(tmp_path):
    rec, stats = run(tmp_path, FIRST, fail=True)
    assert not rec["needs_review"] and len(rec["line_items"]) == 2
    assert any("second reading of pictures failed" in w for w in stats["warnings"])
