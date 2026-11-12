"""Ingest must never raise, and must say *why* an attachment could not be read.

The dev inbox has only one broken attachment (E022, password-protected). The brief says the
held-out inbox has more, "broken on purpose", so these tests manufacture them.
"""

from __future__ import annotations

import io
from email.message import EmailMessage
from pathlib import Path

import pytest

from rfq.ingest import STRIKE_L, html_to_text, load_email

DEV = Path(__file__).resolve().parents[1] / "data" / "dev" / "emails"


def _eml(tmp_path: Path, attachments: list[tuple[str, str, bytes]], body="see attached") -> Path:
    m = EmailMessage()
    m["From"] = "a@customer.example"
    m["To"] = "rfq@ridgeline-supply.example"
    m["Subject"] = "RFQ"
    m["Date"] = "Mon, 05 Oct 2026 09:12:00 -0500"
    m.set_content(body)
    for name, mime, data in attachments:
        maintype, subtype = mime.split("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    p = tmp_path / "X001.eml"
    p.write_bytes(m.as_bytes())
    return p


BROKEN = [
    ("bom.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"PK\x03\x04garbage"),
    ("list.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"not a zip"),
    ("rfq.pdf", "application/pdf", b"%PDF-1.4\n%%EOF garbage"),
    ("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 50),
    ("empty.pdf", "application/pdf", b""),
    ("old.xls", "application/vnd.ms-excel", b"\xd0\xcf\x11\xe0" + b"\x00" * 100),
    ("thing.bin", "application/octet-stream", b"\x00\x01\x02"),
]


@pytest.mark.parametrize("name,mime,data", BROKEN, ids=[b[0] for b in BROKEN])
def test_broken_attachment_is_flagged_not_raised(tmp_path, name, mime, data):
    em = load_email(_eml(tmp_path, [(name, mime, data)]))
    assert em.body.strip() == "see attached"
    (p,) = em.parts
    assert p.kind == "unreadable", p
    assert p.notes, "an unreadable part must say why"


def test_one_broken_attachment_does_not_hide_the_good_one(tmp_path):
    good = b"Qty,Size,Description\n10,2\",90 elbow\n"
    em = load_email(_eml(tmp_path, [BROKEN[0], ("list.csv", "text/csv", good)]))
    kinds = {p.filename: p.kind for p in em.parts}
    assert kinds == {"bom.xlsx": "unreadable", "list.csv": "text"}
    assert '10 | 2" | 90 elbow' in em.parts[1].text


def test_garbage_file_is_not_an_email_crash(tmp_path):
    p = tmp_path / "junk.eml"
    p.write_bytes(b"\x00\xff" * 100)
    em = load_email(p)  # must not raise
    assert em.id == "junk"


def test_html_strikethrough_survives():
    t = html_to_text("<table><tr><td>3</td><td><s>3/4 ball valve</s></td></tr><tr><td>12</td><td>2in coupling</td></tr></table>")
    assert f"{STRIKE_L}3/4 ball valve" in t
    assert "12 | 2in coupling" in t


# --- properties of the real dev data that later steps rely on


def test_hidden_sheet_is_marked():
    em = load_email(DEV / "E005.eml")
    assert "[HIDDEN SHEET]" in em.parts[0].text


def test_password_pdf_is_unreadable():
    em = load_email(DEV / "E022.eml")
    assert em.parts[0].kind == "unreadable"


def test_scanned_pdf_becomes_images():
    em = load_email(DEV / "E009.eml")
    assert len(em.parts[0].images) == 2


def test_pdf_rows_rebuilt_like_the_key():
    em = load_email(DEV / "E006.eml")
    assert '1 | 7 | EA | 1/2" | WELD NECK FLANGE, STD BORE' in em.parts[0].text


def test_docx_headings_stay_above_their_tables():
    t = load_email(DEV / "E019.eml").parts[0].text
    assert t.index("Socket Weld Fittings") < t.index("90 elbow") < t.index("Deliver to yard")
    assert t.index("Carbon Steel Pipe") < t.index("10 jts") < t.index("Socket Weld Fittings")


def test_whole_dev_inbox_ingests():
    for f in sorted(DEV.glob("*.eml")):
        em = load_email(f)
        assert not em.errors, (f.name, em.errors)
