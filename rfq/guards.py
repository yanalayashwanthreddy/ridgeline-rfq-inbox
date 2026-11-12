"""Code checks the model. Every rule here is a fact code can verify; none needs judgment.

The ordering of the graded failures drives the rules:
  1. silent loss (a request with no lines and no flag) -> a request class with nothing usable is
     always flagged for review;
  2. hallucinated lines -> struck-through and hidden-sheet lines are dropped by code, whatever
     the model said;
  3. lost/phantom requests -> non-request classes never carry lines or review flags.
"""

from __future__ import annotations

import re

from .ingest import STRIKE_L, Email
from .prompt import REQUEST_CLASSES

ROW_LABEL = re.compile(r"^\s*R\d+:\s*")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9/#.]+", " ", s.lower()).strip()


def finalize(em: Email, data: dict, truncated: list[str]) -> tuple[dict, list[str]]:
    """Model answer -> output record, plus warnings for the run report."""
    warn: list[str] = []
    cls = data.get("classification")
    is_request = cls in REQUEST_CLASSES

    hidden = {norm(r) for p in em.parts for r in p.hidden_rows}
    visible = {norm(r) for p in em.parts for r in p.visible_rows}
    sources = {"body", *(p.filename for p in em.parts)}

    lines = []
    for ln in data.get("line_items") or []:
        if not is_request:
            break
        raw = ROW_LABEL.sub("", (ln.get("raw") or "").strip())
        desc = (ln.get("description") or "").strip()
        if not raw or not desc:
            warn.append(f"dropped a line with empty raw/description: {ln!r}")
            continue
        if STRIKE_L in raw:
            warn.append(f"dropped struck-through line: {raw!r}")
            continue
        if norm(raw) in hidden and norm(raw) not in visible:
            warn.append(f"dropped line from a hidden sheet/row: {raw!r}")
            continue
        qty = ln.get("qty")
        if isinstance(qty, bool) or (qty is not None and not isinstance(qty, (int, float))) or (qty is not None and qty <= 0):
            warn.append(f"qty {qty!r} is not a positive number; set to null: {raw!r}")
            qty = None
        if isinstance(qty, float) and qty.is_integer():
            qty = int(qty)
        src = ln.get("source") if ln.get("source") in sources else "body"
        lines.append({"raw": raw, "description": desc, "qty": qty, "uom": ln.get("uom"), "source": src})
        _grounding(em, raw, src, warn)

    if not is_request and data.get("line_items"):
        warn.append(f"model returned {len(data['line_items'])} lines for a {cls}; dropped")

    review = bool(data.get("needs_review")) and is_request
    reasons = [data["review_reason"].strip()] if review and data.get("review_reason") else []
    if is_request:
        for p in em.parts:
            if p.kind == "unreadable":
                review = True
                reasons.append(f"attachment {p.filename} could not be read ({'; '.join(p.notes)})")
        for t in truncated:
            review = True
            reasons.append(t)
        for p in em.parts:
            for n in p.notes:
                if "only the first" in n or "truncated" in n:
                    review = True
                    reasons.append(f"{p.filename}: {n}")
        if not lines and not review:
            review = True
            reasons.append("asked for items but none could be extracted; a person should read it")
    if review and not reasons:
        reasons.append("flagged for review by the model without a stated reason")

    record = {
        "classification": cls,
        "revises": None,
        "duplicate_of": None,
        "needs_review": review,
        "review_reason": "; ".join(dict.fromkeys(reasons)) if review else None,
        "line_items": lines,
    }
    return record, warn


def _grounding(em: Email, raw: str, src: str, warn: list[str]) -> None:
    """Warn (don't drop) when raw text isn't found in its claimed text source.
    Picture sources are skipped: there the model's reading *is* the text."""
    if src == "body":
        hay = em.body
    else:
        part = next((p for p in em.parts if p.filename == src), None)
        if part is None or part.images:
            return
        hay = part.text
    toks = [t for t in norm(raw).split() if len(t) > 1]
    if not toks:
        return
    h = norm(hay)
    missing = [t for t in toks if t not in h]
    if len(missing) / len(toks) > 0.34:
        warn.append(f"raw not found in {src}: {raw!r} (missing {missing[:5]})")


def fallback(em: Email, why: str) -> dict:
    """No model answer. Decide what we safely can from headers; flag everything else.

    Silent losses are the costliest failure, so an email we could not read is treated as a
    possible request and flagged for a person -- unless the headers prove it is a machine
    message or a bulk mailing."""
    sender = em.sender.lower()
    if (em.auto_submitted and em.auto_submitted != "no") or re.search(r"mailer-daemon|postmaster", sender):
        cls = "auto_reply"
    elif em.precedence in ("bulk", "list", "junk"):
        cls = "other"
    else:
        return {
            "classification": "rfq",
            "revises": None,
            "duplicate_of": None,
            "needs_review": True,
            "review_reason": f"not read automatically ({why}); a person must read this email",
            "line_items": [],
        }
    return {"classification": cls, "revises": None, "duplicate_of": None, "needs_review": False, "review_reason": None, "line_items": []}
