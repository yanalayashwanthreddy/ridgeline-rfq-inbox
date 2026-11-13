"""Build report.html: the results, readable by anyone, in one file that opens in any browser.

    python scripts/build_report.py            -> report.html (repo root)

Everything comes from the committed outputs (parsed.json, *.report.json, the answer keys, the
catalog), so the page always shows what the code actually produced. No server, no install,
works offline. Email content is untrusted: the page only ever inserts it as text.
"""

from __future__ import annotations

import base64
import io
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rfq.ingest import load_email  # noqa: E402
from rfq.llm import cost_usd  # noqa: E402
from rfq.matcher import load_catalog  # noqa: E402
from rfq.score import mentions, pair, qty_ok  # noqa: E402

KIND = {
    "rfq": "Price request", "rfq_revision": "Changed request", "purchase_order": "Purchase order",
    "vendor_quote": "Supplier's quote", "status_inquiry": "Status question", "auto_reply": "Automatic reply",
    "other": "Other",
}
REQUESTS = {"rfq", "rfq_revision", "purchase_order"}

# What each stress email tests, in plain words (the generator's comments say the same)
SCENARIO = {
    "S01": "An out-of-office reply that has no 'automatic' header",
    "S02": "Items named only as 'same as our last order': can't be read alone",
    "S03": "A Word file with tracked changes: a deleted row and a changed quantity",
    "S04": "One of our reps forwards a customer's email and adds an item",
    "S05": "A supplier's invoice that lists items (not a request)",
    "S06": "'We went with someone else', quoting our quote back",
    "S07": "A customer asking about the status of a quote, with no number",
    "S08": "A spreadsheet with a default spec, a crossed-out row, a hidden row, an override and a total",
    "S09": "A broken (cut-off) spreadsheet",
    "S10": "A request written in Spanish",
    "S11": "A purchase order written in the email body",
    "S12A": "A request with 5 numbered items...",
    "S12B": "...then the reply 'make #3 twelve and kill #5'",
    "S13": "The same purchase order delivered a second time",
    "S14": "A supplier's quote as a PDF, answering our request",
    "S15": "A phone photo of a typed list, turned sideways",
    "S16": "A newsletter without a 'bulk mail' header",
    "S17": "An HTML table with a crossed-out row",
    "S18": "A 35-page PDF (105 items)",
    "S19": "The item list is a spreadsheet inside a zip file",
    "S20": "A phone photo stored sideways with a rotation tag",
    "S21": "An iPhone HEIC photo of a list",
    "S22": "'Make #2 eight, kill #4' when the original request is nowhere to be seen",
    "S23": "The same kind of change, with the original quoted below",
    "S24": "A handwritten list with one line crossed out by pen",
}


def thumb(data: bytes) -> str:
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    im.thumbnail((1000, 1000))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def name(sender: str) -> str:
    return re.sub(r"\s*<.*>", "", sender or "").strip('" ') or sender


def inbox(emails_dir: Path, parsed_path: Path, key_path: Path | None, cat_by_sku: dict) -> dict:
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    rep = json.loads(parsed_path.with_name(parsed_path.stem + ".report.json").read_text(encoding="utf-8"))
    key = json.loads(key_path.read_text(encoding="utf-8"))["emails"] if key_path else {}
    model = rep.get("model", "")
    out, tot = [], Counter()
    for eid in sorted(parsed):
        o, st, k = parsed[eid], rep["emails"].get(eid, {}), key.get(eid)
        em = load_email(emails_dir / f"{eid}.eml")
        lines = [dict(ln, catalog=(cat_by_sku[ln["sku"]].description if ln.get("sku") in cat_by_sku else None))
                 for ln in o["line_items"]]
        verdict, missed = {}, []
        if k:
            kl = k.get("line_items") or []
            pairs, extra, miss = pair(o["line_items"], kl)
            for i, j in pairs:
                kk, ln, probs = kl[j], o["line_items"][i], []
                if kk.get("qty_tol") is not None and not qty_ok(ln, kk):
                    probs.append(f"quantity should be {kk['qty']}")
                if (ln["uom"] or "EA") != (kk["uom"] or "EA"):
                    probs.append(f"unit should be {kk['uom'] or 'EA'}")
                if ln["source"] != kk["source"]:
                    probs.append(f"should come from {kk['source']}")
                probs += [f"description should mention '{m}'" for m in kk.get("must_mention") or [] if not mentions(ln["description"], m)]
                probs += [f"description should not say '{m}'" for m in kk.get("must_not_mention") or [] if mentions(ln["description"], m)]
                if kk.get("match") and "match" in ln and (kk["match"], kk.get("sku")) != (ln["match"], ln.get("sku")):
                    probs.append(f"catalog: answer key says {kk['match']} {kk.get('sku') or ''}".strip())
                verdict[i] = probs
                tot["ok_lines"] += not probs
                tot["ctx_n"] += bool(kk.get("must_mention") or kk.get("must_not_mention"))
                tot["ctx_ok"] += not [p for p in probs if p.startswith("description")] and bool(kk.get("must_mention") or kk.get("must_not_mention"))
                if kk.get("match") and "match" in ln:
                    tot["m_n"] += 1
                    tot["m_ok"] += (kk["match"], kk.get("sku")) == (ln["match"], ln.get("sku"))
                    tot["m_wrong"] += ln["match"] == "matched" and (kk["match"], kk.get("sku")) != (ln["match"], ln.get("sku"))
            for i in extra:
                verdict[i] = ["not in the answer key (an extra item)"]
            missed = [kl[j]["raw"] for j in miss]
            tot["key_lines"] += len(kl)
            tot["paired"] += len(pairs)
            tot["extra"] += len(extra)
            tot["class_ok"] += k["classification"] == o["classification"]
            tot["review_ok"] += bool(k.get("needs_review")) == o["needs_review"]
            needs_action = bool(kl) or k.get("needs_review")
            tot["silent"] += bool(needs_action and not o["line_items"] and not o["needs_review"])
        tot["emails"] += 1
        tot["requests"] += o["classification"] in REQUESTS
        tot["lines"] += len(o["line_items"])
        tot["review"] += o["needs_review"]
        tot["attention"] += bool(o.get("attention"))
        parts = []
        for p in em.parts:
            imgs = [thumb(im.data) for im in p.images[:4]]
            parts.append({"name": p.filename, "kind": p.kind, "notes": p.notes,
                          "text": (p.all_text if hasattr(p, "all_text") else p.text)[:6000], "images": imgs,
                          "more_images": max(0, len(p.images) - 4)})
        cost = cost_usd(model, st["usage"]) if st.get("usage") else None
        out.append({
            "id": eid, "from": name(em.sender), "from_full": em.sender, "subject": em.subject, "date": em.date,
            "kind": o["classification"], "kind_label": KIND.get(o["classification"], o["classification"]),
            "review": o["needs_review"], "review_reason": o.get("review_reason"), "attention": o.get("attention"),
            "revises": o.get("revises"), "duplicate_of": o.get("duplicate_of"),
            "lines": lines, "verdict": {str(i): v for i, v in verdict.items()}, "missed": missed,
            "key_kind": k["classification"] if k else None, "key_kind_label": KIND.get(k["classification"]) if k else None,
            "key_review": bool(k.get("needs_review")) if k else None,
            "reason": st.get("model_reason"), "second_read": st.get("second_read"), "coverage": st.get("coverage_retry"),
            "windows": st.get("windows"), "fallback": st.get("fallback"),
            "calls": len(st.get("cache_keys") or []), "cost": round(cost, 5) if cost is not None else None,
            "body": (em.body or "")[:8000], "body_source": em.body_source, "parts": parts,
            "scenario": SCENARIO.get(eid),
        })
    t = rep["totals"]
    tot["cost"] = t.get("cost_usd_if_uncached")
    tot["cost_each"] = t.get("avg_cost_usd_per_email")
    return {"emails": out, "totals": dict(tot), "model": rep.get("model"), "effort": rep.get("effort")}


def fuzz() -> dict:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "matcher_fuzz.py"), "--seed", "7"],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    n = re.search(r"(\d+) generated lines from (\d+) parts", r.stdout)
    w = re.search(r"confident wrong matches: (\d+)", r.stdout)
    return {"lines": int(n[1]) if n else None, "parts": int(n[2]) if n else None, "wrong": int(w[1]) if w else None}


def main() -> int:
    cat = {p.sku: p for p in load_catalog(ROOT / "data" / "catalog.csv")}
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    # the number pytest actually runs (a parametrized test counts once per case)
    col = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         capture_output=True, text=True, encoding="utf-8", cwd=ROOT).stdout
    m = re.search(r"(\d+) tests? collected", col)
    tests = int(m[1]) if m else None
    data = {
        "dev": inbox(ROOT / "data" / "dev" / "emails", ROOT / "parsed.json", ROOT / "data" / "dev" / "answer_key.json", cat),
        "stress": inbox(ROOT / "tests" / "stress" / "emails", ROOT / "tests" / "stress" / "parsed.json",
                        ROOT / "tests" / "stress" / "answer_key.json", cat),
        "fuzz": fuzz(), "commit": commit, "tests": tests, "catalog_parts": len(cat),
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = (ROOT / "scripts" / "report_template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    out = ROOT / "report.html"
    out.write_text(html, encoding="utf-8", newline="\n")
    print(f"report.html: {len(html) / 1e6:.1f} MB | dev {data['dev']['totals']['emails']} emails, "
          f"stress {data['stress']['totals']['emails']} | matcher test {data['fuzz']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
