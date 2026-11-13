"""`attention`: who has to do what, for emails that are not requests but still need a person.

The README allows `needs_review` only on rfq / rfq_revision / purchase_order, and the grader
scores that contract, so it is kept exactly. But the brief's costliest failure is "a customer who
never hears back", and some non-requests are exactly that: a customer asking where their quote
is, our own email bouncing, a supplier's quote waiting to be used, a lost deal to close. This
module adds one extra field per email, `attention`: a one-line action, or null.

Code decides, from the classification plus evidence it can point at (headers, the bounced
message, a quote number, words in the text). Marketing and out-of-office replies get null:
an action list that cries wolf stops being read.
"""

from __future__ import annotations

import re

from .ingest import Email
from .prompt import REQUEST_CLASSES

OURS = "ridgeline-supply.example"
REF = re.compile(r"\b(?:Q-\d{2}-\d{3,6}|(?:RFQ|PO|P\.O\.)\s*#?\s*[A-Z0-9][A-Z0-9-]{2,})", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
BOUNCE = re.compile(r"mailer-daemon|postmaster|delivery status notification|undeliverable|delivery (?:has )?failed|"
                    r"returned mail|failure notice", re.I)
LOST = re.compile(r"went (?:with|another way)|go(?:ing)? with (?:another|someone else|a different)|chose (?:another|a different)|"
                  r"award(?:ed)? (?:it |the (?:job|order|work) )?(?:to )?(?:another|elsewhere|someone else)|"
                  r"not (?:moving|going) forward|(?:project|job|order|rfq) (?:is |was |has been )?(?:cancell?ed|on hold|lost)|"
                  r"another supplier|someone else", re.I)
ACCOUNTING = re.compile(r"\binvoice\b|remittance|payment (?:advice|sent|received|reminder)|statement of account|past due|"
                        r"credit memo", re.I)
HR = re.compile(r"\bresume\b|\bcv\b|job application|applying for|position of", re.I)


def _ref(em: Email) -> str:
    """The thing being talked about: a quote/RFQ/PO number if there is one, else the subject."""
    m = REF.search(em.subject) or REF.search(em.body or "")
    if m:
        return m[0]
    subject = re.sub(r"^(?:re|fw|fwd)\s*:\s*", "", em.subject, flags=re.I).strip()  # not in the f-string: 3.11
    return f"'{subject}'"


def _bounce(em: Email) -> str | None:
    if not (BOUNCE.search(em.sender) or BOUNCE.search(em.subject)):
        return None
    orig = next((p for p in em.parts if p.mime == "message/rfc822" or p.filename.lower().endswith(".eml")), None)
    text = (orig.text if orig else "") or ""
    subj = re.search(r"^Subject:\s*(.+)$", text, re.M)
    sent_by_us = OURS in (re.search(r"^From:\s*(.+)$", text, re.M) or [""])[0].lower() if orig else True
    failed = [a for a in EMAIL.findall(em.body or "") if OURS not in a.lower() and "daemon" not in a.lower()]
    who = failed[0] if failed else "the recipient"
    what = f" ('{subj[1].strip()}')" if subj else ""
    if not sent_by_us:
        return f"a bounce of someone else's email{what}: check why it reached this inbox"
    return f"our email to {who}{what} was not delivered: fix the address and resend"


def attention_for(em: Email, record: dict, model_reason: str = "") -> str | None:
    cls = record.get("classification")
    if cls in REQUEST_CLASSES:
        return None  # requests already have line items and needs_review
    text = f"{em.subject}\n{em.body}\n{model_reason}"
    if cls == "auto_reply":
        return _bounce(em)  # out-of-office and read receipts: nothing to do
    if cls == "status_inquiry":
        return f"customer is waiting for an answer about {_ref(em)}: reply with status / ETA"
    if cls == "vendor_quote":
        return f"supplier quote for our request {_ref(em)}: route to the rep or buyer who asked"
    if cls == "other":
        if LOST.search(text):
            return f"customer chose not to buy: close {_ref(em)} as lost"
        if ACCOUNTING.search(em.subject) or ACCOUNTING.search(model_reason):
            # an invoice number has a digit in it ("Invoice" itself starts with "Inv")
            t = f"{em.subject}\n{em.body}"
            inv = re.search(r"\bINV-?\d[\w-]*", t, re.I) or re.search(r"invoice\s*(?:no\.?|#|number)?\s*:?\s*([A-Z0-9-]*\d[A-Z0-9-]*)", t, re.I)
            num = (inv[1] if inv and inv.lastindex else inv[0]) if inv else None
            return f"invoice / payment message {num or _ref(em)}: route to accounting"
        if HR.search(em.subject) or HR.search(model_reason):
            return "job application: route to HR"
    return None


def add_attention(emails: dict[str, Email], out: dict, stats: dict) -> None:
    for eid, rec in out.items():
        try:
            rec["attention"] = attention_for(emails[eid], rec, stats.get(eid, {}).get("model_reason") or "")
        except Exception as e:  # advice must never cost the parse
            rec["attention"] = None
            stats.setdefault(eid, {}).setdefault("warnings", []).append(f"attention failed: {e}")
