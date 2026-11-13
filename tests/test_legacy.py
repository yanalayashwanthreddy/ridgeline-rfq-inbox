"""Outlook .msg and Word 97-2003 .doc, on real files from Apache POI's test data.

Expected values come from independent tools run on the same files (antiword for .doc,
extract-msg for .msg), not from our own reader. See tests/fixtures/legacy/NOTICE.md.
"""

from __future__ import annotations

from pathlib import Path

from rfq.guards import finalize
from rfq.ingest import load_email
from rfq.legacy import DOC_NO_FORMATTING
from tests.test_ingest import _eml

FIX = Path(__file__).resolve().parent / "fixtures" / "legacy"


def parts(tmp_path, name, mime="application/octet-stream", as_name=None):
    em = load_email(_eml(tmp_path, [(as_name or name, mime, (FIX / name).read_bytes())]))
    return em, {p.filename: p for p in em.parts}


# ---- .doc (antiword's output)


def test_doc_text_matches_antiword(tmp_path):
    _, ps = parts(tmp_path, "SampleDoc.doc", "application/msword")
    t = ps["SampleDoc.doc"].text
    for line in ("I am a test document", "This is page two", "It’s Arial Black in 16 point", "It’s also in blue"):
        assert line in t


def test_doc_table_rows_match_antiword(tmp_path):
    _, ps = parts(tmp_path, "simple-table.doc", "application/msword")
    t = ps["simple-table.doc"].text
    assert "Cell 1,1 | Cell 1,2 | Cell 1,3" in t and "Cell 2,1 | Cell 2,2 | Cell 2,3" in t
    assert "This text is below the table." in t


def test_doc_says_formatting_is_not_visible_and_the_request_is_flagged(tmp_path):
    em, ps = parts(tmp_path, "simple-table.doc", "application/msword")
    assert DOC_NO_FORMATTING in ps["simple-table.doc"].notes
    data = {"classification": "rfq", "needs_review": False, "review_reason": None,
            "line_items": [{"raw": "Cell 1,1 | Cell 1,2 | Cell 1,3", "description": "x", "qty": 1, "uom": None,
                            "source": "simple-table.doc"}]}
    rec, _ = finalize(em, data, [])
    assert rec["needs_review"] and "strikethrough" in rec["review_reason"] and rec["line_items"], \
        "lines are read, and a person checks for crossed-out ones"


def test_doc_with_a_wrong_name_is_read_by_content(tmp_path):
    _, ps = parts(tmp_path, "SampleDoc.doc", as_name="scan0001.bin")
    assert "This is page two" in ps["scan0001.bin"].text


# ---- .msg (extract-msg's output)


def test_msg_headers_body_and_attachments_match_extract_msg(tmp_path):
    _, ps = parts(tmp_path, "attachment_test_msg.msg", "application/vnd.ms-outlook")
    t = ps["attachment_test_msg.msg"].text
    assert "Subject: test pièce jointe 1" in t and "Wed, 22 Apr 2009 14:36:33" in t and "contenu" in t
    assert "nicolas1.23456@free.fr" in t
    assert {"test-unicode.doc", "pj1.txt"} <= set(ps)
    doc = ps["test-unicode.doc"]
    assert "found inside attachment_test_msg.msg" in doc.notes
    assert "€ (20AC)" in doc.text and "école (00E9)" in doc.text  # the file names its own code points


def test_msg_inside_msg_and_its_pdf(tmp_path):
    _, ps = parts(tmp_path, "attachment_msg_pdf.msg", "application/vnd.ms-outlook")
    outer = ps["attachment_msg_pdf.msg"].text
    assert "nick.booth@pof.com.au" in outer, "address found in the transport headers"
    assert "Thu, 17 Jun 2010 23:54:19" in outer and "Test email with 1 msg attachment, 1 pdf" in outer
    inner = ps["Test Attachment.msg"]
    assert "Blah blah blah" in inner.text and "Thu, 17 Jun 2010 23:52:19" in inner.text
    assert "smbprn.00009008.KdcPjl.pdf" in ps and ps["smbprn.00009008.KdcPjl.pdf"].kind == "text"


def test_msg_ansi_strings_and_exchange_names(tmp_path):
    _, ps = parts(tmp_path, "quick.msg")  # no .msg type given: recognised by content
    t = ps["quick.msg"].text
    assert "Subject: Test the content transformer" in t and "The quick brown fox jumps over the lazy dog" in t
    assert "/O=" not in t, "an Exchange X.500 name is not an email address"
