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
    # "2-1/2" is one size, not "2" and "1/2": keep a hyphen between digits as part of the token
    s = re.sub(r"(?<=\d)-(?=\d)", "_", s.lower())
    return re.sub(r"[^a-z0-9/#._]+", " ", s).strip()


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
        raw = _snap(em, raw, src)
        if STRIKE_L in raw:
            warn.append(f"dropped struck-through line: {raw!r}")
            continue
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


def _source_text(em: Email, src: str) -> str | None:
    if src == "body":
        return em.body
    part = next((p for p in em.parts if p.filename == src), None)
    return part.all_text if part is not None and not part.images else None


def _snap(em: Email, raw: str, src: str) -> str:
    """Replace the model's copy of a line with the source line itself, when it is unambiguous.

    The model's transcription of the same row varies run to run (dropping a cell, reformatting a
    quote mark). If exactly one source line contains every token of `raw` and is not much longer,
    that line *is* the raw text. Pictures are left alone: there the model's reading is all we have.
    """
    text = _source_text(em, src)
    if not text:
        return raw
    want = set(norm(raw).split())
    if not want:
        return raw
    scored = []
    for line in dict.fromkeys(_clean_line(x) for x in text.splitlines()):
        have = set(norm(line).split())
        if not have:
            continue
        inter = len(want & have)
        # at most 3 source tokens the model left out: enough to restore dropped cells (item no.,
        # "EA", a price), not enough to widen a clause into its whole prose sentence.
        # 0.7 overlap: a row with one truncated word ("inner rin") scores 11/15 = 0.73
        if len(have) - inter <= 3:
            scored.append((inter / len(want | have), line))
    scored.sort(reverse=True)
    if scored and scored[0][0] >= 0.7 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
        return scored[0][1]
    return raw


def _clean_line(line: str) -> str:
    line = ROW_LABEL.sub("", line.strip().lstrip(">").strip())
    return re.sub(r"\s+\[HIDDEN ROW\]$", "", line)


def _jac(a: str, b: str) -> float:
    ta, tb = set(norm(a).split()), set(norm(b).split())
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def missing_rows(em: Email, lines: list[dict]) -> dict[str, list[str]]:
    """Numbered table rows in text attachments that no extracted line accounts for.

    A row counts when it has 3+ cells, one of its first two cells is a bare integer (item no. or
    qty), and it has words. Struck-through, hidden and total rows are excluded: code already
    knows those are not requests."""
    out: dict[str, list[str]] = {}
    for p in em.parts:
        if p.kind != "text" or not p.text:
            continue
        hidden = {norm(r) for r in p.hidden_rows}
        mine = [ln["raw"] for ln in lines if ln["source"] == p.filename]
        for line in p.text.splitlines():
            if STRIKE_L in line or "[HIDDEN" in line:
                continue
            row = _clean_line(line)
            cells = [c.strip() for c in row.split(" | ")]
            if len(cells) < 3 or not any(re.fullmatch(r"\d+", c) for c in cells[:2]):
                continue
            if not re.search(r"[A-Za-z]{2}", row) or re.search(r"\b(sub)?total\b", row, re.I) or norm(row) in hidden:
                continue
            if not any(_jac(row, r) >= 0.6 for r in mine):
                out.setdefault(p.filename, []).append(row)
    return out


def _grounding(em: Email, raw: str, src: str, warn: list[str]) -> None:
    """Warn (don't drop) when raw text isn't found in its claimed text source.
    Picture sources are skipped: there the model's reading *is* the text."""
    hay = _source_text(em, src)
    if hay is None:
        return
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
