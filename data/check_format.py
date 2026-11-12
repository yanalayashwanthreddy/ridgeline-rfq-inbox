#!/usr/bin/env python3
"""Validate the SHAPE of your parsed.json.  This checks structure only — it scores nothing.

    python check_format.py parsed.json
    python check_format.py parsed.json --emails dev/emails     # also: every email appears

Every problem is printed as <email>.<field> or <email>.line_items[i].<field>;
exit 0 = ok, 2 = shape problems, 1 = file missing or not JSON.  README.md defines each field.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CLASSES = (
    "rfq",
    "rfq_revision",
    "purchase_order",
    "vendor_quote",
    "status_inquiry",
    "auto_reply",
    "other",
)
MATCHES = ("matched", "not_in_catalog", "ambiguous")
UOMS = ("EA", "FT", "M", "IN", "BX", "PK", "PR", "DZ", "RL", "LB", "SET", "GAL", "BAG", "CS")


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _str(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def check_line(w: str, ln) -> list[str]:
    if not isinstance(ln, dict):
        return [f"{w}: must be an object"]
    errs = []
    if not _str(ln.get("raw")):
        errs.append(f"{w}.raw: required — the line's text as it appears in the email or attachment")
    if not _str(ln.get("description")):
        errs.append(f"{w}.description: required — the item, self-contained, with its context")
    if "qty" not in ln:
        errs.append(f"{w}.qty: required (a number, or null when not stated)")
    elif ln["qty"] is not None and not _num(ln["qty"]):
        errs.append(f"{w}.qty: must be a number or null")
    if ln.get("uom") is not None and ln["uom"] not in UOMS:
        errs.append(f"{w}.uom: must be one of {list(UOMS)} or null")
    if not _str(ln.get("source")):
        errs.append(f"{w}.source: required — 'body' or the attachment's filename")
    if "match" in ln or "sku" in ln:  # optional matcher
        m = ln.get("match")
        if m not in MATCHES:
            errs.append(f"{w}.match: one of {list(MATCHES)} when you match (optional)")
        elif m == "matched" and not _str(ln.get("sku")):
            errs.append(f"{w}.sku: required when match is 'matched'")
        elif m != "matched" and ln.get("sku") is not None:
            errs.append(f"{w}.sku: must be null when match is '{m}'")
        if ln.get("candidates") is not None and not (
            isinstance(ln["candidates"], list) and all(_str(c) for c in ln["candidates"])
        ):
            errs.append(f"{w}.candidates: a list of skus")
    return errs


def check(data) -> list[str]:
    if not isinstance(data, dict):
        return ['parsed.json must be a JSON object keyed by email id (e.g. "E001")']
    errs = []
    for eid, v in data.items():
        if not isinstance(v, dict):
            errs.append(f"{eid}: must be an object")
            continue
        if v.get("classification") not in CLASSES:
            errs.append(f"{eid}.classification: must be one of {list(CLASSES)}")
        for f in ("revises", "duplicate_of"):
            if v.get(f) is not None and not _str(v[f]):
                errs.append(f"{eid}.{f}: must be an email id string or null")
        if not isinstance(v.get("needs_review"), bool):
            errs.append(f"{eid}.needs_review: required, true or false")
        if v.get("needs_review") and not _str(v.get("review_reason")):
            errs.append(f"{eid}.review_reason: required when needs_review is true")
        lines = v.get("line_items")
        if not isinstance(lines, list):
            errs.append(f"{eid}.line_items: required, a list ([] when there is nothing to extract)")
            continue
        for i, ln in enumerate(lines):
            errs += check_line(f"{eid}.line_items[{i}]", ln)
    return errs


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("parsed", type=Path)
    ap.add_argument("--emails", type=Path, help="directory of .eml files that must all appear")
    a = ap.parse_args(argv)
    try:
        data = json.loads(a.parsed.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"{a.parsed}: {e}", file=sys.stderr)
        return 1
    errs = check(data)
    if a.emails:
        want = {p.stem for p in a.emails.glob("*.eml")}
        errs += [
            f"{eid}: missing (every email in {a.emails} must appear)"
            for eid in sorted(want - set(data))
        ]
    for e in errs:
        print(e)
    print(f"{len(data) if isinstance(data, dict) else 0} emails, {len(errs)} problem(s)")
    return 2 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
