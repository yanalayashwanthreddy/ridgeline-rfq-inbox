"""Links between emails: `revises` and `duplicate_of`. Decided by code, across the inbox.

A per-email model call can't see other emails, and these are mechanical questions anyway:
  * revises: an rfq_revision points at the earlier request it changes. Found through
    In-Reply-To / References first, then the same normalized subject from the same sender
    domain.
  * duplicate_of: the same request sent twice. Fingerprint = the customer, the bytes of the
    real (non-inline) attachments, and the set of extracted lines. The email with the later
    Date is the duplicate; it keeps the original's classification and loses its lines.
"""

from __future__ import annotations

import re

from .guards import norm
from .ingest import Email
from .prompt import REQUEST_CLASSES

DUP_WINDOW_DAYS = 30
SUBJ_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|tr|resend|resent|reminder|follow[- ]?up|2nd request)\s*(\[\d+\])?\s*[:\-]\s*)+", re.I)


def _addr(sender: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+", sender or "")
    return m.group(0).lower() if m else (sender or "").lower()


def _domain(sender: str) -> str:
    return _addr(sender).rpartition("@")[2]


def subject_key(s: str) -> str:
    return re.sub(r"\s+", " ", SUBJ_PREFIX.sub("", s or "")).strip().lower()


def link(emails: dict[str, Email], out: dict[str, dict]) -> None:
    order = sorted(emails, key=lambda i: (emails[i].timestamp or 0, i))
    _revisions(emails, out, order)
    _duplicates(emails, out, order)


def _revisions(emails, out, order) -> None:
    by_msgid = {emails[i].message_id: i for i in order if emails[i].message_id}
    for i in order:
        if out[i]["classification"] != "rfq_revision":
            continue
        em = emails[i]
        target = None
        for ref in [em.in_reply_to, *reversed(em.references)]:
            j = by_msgid.get(ref)
            if j and j != i and _earlier(emails[j], em) and out[j]["classification"] in REQUEST_CLASSES:
                target = j
                break
        if target is None:
            subj = subject_key(em.subject)
            cands = [
                j for j in order
                if j != i and _earlier(emails[j], em)
                and out[j]["classification"] in ("rfq", "rfq_revision")
                and subject_key(emails[j].subject) == subj and subj
                and _domain(emails[j].sender) == _domain(em.sender)
            ]
            target = cands[-1] if cands else None  # the most recent earlier version
        out[i]["revises"] = target


def _earlier(a: Email, b: Email) -> bool:
    return (a.timestamp or 0) < (b.timestamp or 0)


def _attachments(em: Email) -> frozenset:
    return frozenset(p.sha256 for p in em.parts if not p.inline and p.kind != "skipped")


def _toks(s: str) -> set:
    return set(norm(s).split())


def same_lines(a: list[dict], b: list[dict]) -> bool:
    """Same request, tolerant of how the model worded `raw`: equal counts, and every line pairs
    with a distinct line of equal qty and mostly shared tokens."""
    if len(a) != len(b) or not a:
        return False
    free = list(range(len(b)))
    for la in a:
        ta = _toks(la["raw"])
        best, best_s = None, 0.5
        for j in free:
            if la["qty"] != b[j]["qty"]:
                continue
            tb = _toks(b[j]["raw"])
            s = len(ta & tb) / len(ta | tb) if ta | tb else 0
            if s >= best_s:
                best, best_s = j, s
        if best is None:
            return False
        free.remove(best)
    return True


def _duplicates(emails, out, order) -> None:
    originals: list[str] = []
    for i in order:
        rec, em = out[i], emails[i]
        if rec["classification"] not in REQUEST_CLASSES or not rec["line_items"]:
            continue
        for j in originals:
            o_em, o_rec = emails[j], out[j]
            if _addr(o_em.sender) != _addr(em.sender) and _domain(o_em.sender) != _domain(em.sender):
                continue
            if em.timestamp and o_em.timestamp and em.timestamp - o_em.timestamp > DUP_WINDOW_DAYS * 86400:
                continue
            # the attachments must be byte-identical (or both absent) AND the lines the same:
            # a "revision" that changes nothing is a resend
            if _attachments(em) == _attachments(o_em) and same_lines(rec["line_items"], o_rec["line_items"]):
                rec.update(
                    classification=o_rec["classification"],
                    duplicate_of=j,
                    revises=None,
                    needs_review=False,
                    review_reason=None,
                    line_items=[],
                )
                break
        else:
            originals.append(i)

