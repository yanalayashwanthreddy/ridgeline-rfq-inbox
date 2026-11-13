"""Long PDFs are read in windows of pages, one cached model call each.

A fake model stands in for OpenAI, so these run offline and free. It answers each window with
the rows on that window's pages, and can be told to fail, to check that a lost window makes the
email need review instead of silently dropping its lines.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from rfq.ingest import PDF_WINDOW_PAGES, load_email
from rfq.llm import CallResult, ModelUnavailable
from rfq.pipeline import process_one
from tests.test_ingest import _eml


def _pdf(pages: int) -> bytes:
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        doc.new_page().insert_text((54, 72), f"{i} | 2 | EA | 2\" 150# RF slip-on flange, A105, item {i}", fontsize=10)
    return doc.tobytes()


class FakeLLM:
    """Answers with the numbered rows it can see in the PAGES section (or the first window)."""
    model, effort = "fake", "none"

    def __init__(self, fail_windows=False):
        self.fail_windows = fail_windows
        self.labels = []

    def request(self, instructions, content, schema):
        return {"content": content}

    def call(self, req, label=""):
        self.labels.append(label)
        text = "\n".join(c.get("text", "") for c in req["content"])
        window = "=== PAGES " in text
        if window and self.fail_windows:
            raise ModelUnavailable("provider down")
        if window:
            text = text.split("=== PAGES ", 1)[1]  # never the CONTEXT sections
        rows = re.findall(r"^(\d+ \| 2 \| EA \| .+)$", text, re.M)
        data = {"classification_reason": "asks for prices", "classification": "rfq", "needs_review": False,
                "review_reason": None,
                "line_items": [{"raw": r, "description": r.split(" | ")[-1], "qty": 2, "uom": "EA", "source": "big.pdf"}
                               for r in rows]}
        return CallResult(data, {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_tokens": 0},
                          0.0, False, 0.0, key=label)


def test_short_pdf_is_one_window(tmp_path):
    (p,) = load_email(_eml(tmp_path, [("big.pdf", "application/pdf", _pdf(PDF_WINDOW_PAGES))])).parts
    assert p.windows == [] and "--- page 30" in p.text


def test_long_pdf_is_split_and_all_text_covers_every_page(tmp_path):
    (p,) = load_email(_eml(tmp_path, [("big.pdf", "application/pdf", _pdf(65))])).parts
    assert [w["pages"] for w in p.windows] == ["31-60", "61-65"]
    assert re.search(r"item 1\b", p.text) and not re.search(r"item 31\b", p.text)
    assert all(re.search(rf"item {i}\b", p.all_text) for i in (1, 31, 65))
    assert any("not missing" in n for n in p.notes), "the model must be told the rest is read separately"


def test_every_window_adds_its_lines_once(tmp_path):
    llm = FakeLLM()
    _, rec, stats = process_one(_eml(tmp_path, [("big.pdf", "application/pdf", _pdf(65))]), llm)
    items = sorted(int(ln["raw"].split(" | ")[0]) for ln in rec["line_items"])
    assert items == list(range(1, 66)), "65 pages, 65 rows, none twice (CONTEXT is never extracted)"
    assert not rec["needs_review"]
    assert len(llm.labels) == 3 and stats["model_calls"] == 3


def test_a_window_that_cannot_be_read_flags_the_email(tmp_path):
    _, rec, _ = process_one(_eml(tmp_path, [("big.pdf", "application/pdf", _pdf(65))]), FakeLLM(fail_windows=True))
    assert rec["needs_review"] and "pages 31-60 could not be read" in rec["review_reason"]
    assert len(rec["line_items"]) == 30, "keep what was read"
