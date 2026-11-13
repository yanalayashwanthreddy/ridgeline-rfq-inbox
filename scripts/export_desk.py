"""Export parsed results as two flat JSON tables for the hosted quote-desk page.

    python scripts/export_desk.py parsed.json data/dev/emails [data/dev/answer_key.json] --out work/desk

lines.json  one row per extracted line item (what a rep would quote from)
emails.json one row per email (classification, review flag, links, cost, key check)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfq.ingest import load_email  # noqa: E402
from rfq.llm import cost_usd  # noqa: E402
from rfq.score import mentions, pair, qty_ok  # noqa: E402


def name(sender: str) -> str:
    return re.sub(r"\s*<.*>", "", sender or "").strip('" ') or sender


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("parsed", type=Path)
    ap.add_argument("emails", type=Path)
    ap.add_argument("key", type=Path, nargs="?")
    ap.add_argument("--out", type=Path, default=Path("work/desk"))
    a = ap.parse_args()
    parsed = json.loads(a.parsed.read_text(encoding="utf-8"))
    rep_path = a.parsed.with_name(a.parsed.stem + ".report.json")
    report = json.loads(rep_path.read_text(encoding="utf-8")) if rep_path.exists() else {"emails": {}}
    key = json.loads(a.key.read_text(encoding="utf-8"))["emails"] if a.key else {}

    lines, emails = [], []
    for eid in sorted(parsed):
        o, em, st = parsed[eid], load_email(a.emails / f"{eid}.eml"), report["emails"].get(eid, {})
        k = key.get(eid)
        checks, missed = {}, []
        if k:
            pairs, extra, missed = pair(o["line_items"], k.get("line_items") or [])
            for i, j in pairs:
                kk, ln, probs = k["line_items"][j], o["line_items"][i], []
                if kk.get("qty_tol") is not None and not qty_ok(ln, kk):
                    probs.append(f"qty should be {kk['qty']}")
                if (ln["uom"] or "EA") != (kk["uom"] or "EA"):
                    probs.append(f"unit should be {kk['uom'] or 'EA'}")
                probs += [f"missing {m}" for m in kk.get("must_mention") or [] if not mentions(ln["description"], m)]
                probs += [f"should not say {m}" for m in kk.get("must_not_mention") or [] if mentions(ln["description"], m)]
                checks[i] = "; ".join(probs) or "matches"
            for i in extra:
                checks[i] = "not in key"
        try:
            received = parsedate_to_datetime(em.date).strftime("%Y-%m-%d %H:%M")
        except Exception:
            received = em.date
        for i, ln in enumerate(o["line_items"], 1):
            lines.append({
                "line_id": f"{eid}-{i}", "email": eid, "customer": name(em.sender), "subject": em.subject,
                "classification": o["classification"], "line_no": i, "raw": ln["raw"],
                "description": ln["description"], "qty": ln["qty"], "uom": ln["uom"] or "EA",
                "source": ln["source"], "needs_review": "yes" if o["needs_review"] else "no",
                "key_check": checks.get(i - 1, "") if k else "",
            })
        emails.append({
            "email": eid, "customer": name(em.sender), "subject": em.subject, "received": received,
            "classification": o["classification"], "lines": len(o["line_items"]),
            "needs_review": "yes" if o["needs_review"] else "no", "review_reason": o.get("review_reason") or "",
            "revises": o.get("revises") or "", "duplicate_of": o.get("duplicate_of") or "",
            "model_reason": st.get("model_reason") or "",
            # None (unknown price), not 0: a model missing from PRICES must not look free
            "cost_usd": (round(c, 5) if (c := cost_usd(report.get("model", ""), st["usage"])) is not None else None)
            if st.get("usage") else None,
            "key_classification": k["classification"] if k else "",
            "key_lines": len(k.get("line_items") or []) if k else None,
            # key lines no extracted line pairs with: the silent part of a recall miss
            "missed_lines": [k["line_items"][j]["raw"] for j in missed] if k else [],
        })
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "lines.json").write_text(json.dumps(lines, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    (a.out / "emails.json").write_text(json.dumps(emails, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{len(lines)} lines, {len(emails)} emails -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
