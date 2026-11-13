"""Score parsed.json against an answer key the way data/README.md describes grading.

    python scripts/score.py parsed.json [data/dev/answer_key.json] [-v]

Our reading of the README, not the graders' code: lines are paired per email on `raw` tokens;
`must_mention` alternatives split on "|", compared case/space/punctuation-insensitively with the
README's listed spelling equivalences. Treat absolute numbers as approximate, deltas as real.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REQ = {"rfq", "rfq_revision", "purchase_order"}

# README: "common spellings of the same spec count as equal"
EQUIV = [
    (r"\bschedule\b", "sch"),
    (r"\bclass\s*(\d+)", r"\1#"), (r"(\d+)\s*(lb|lbs|pound)\b", r"\1#"),
    (r"\bbutt\s*-?\s*weld(ed)?\b", "bw"),
    (r"\b(threaded|thrd|thd|npt)\b", "npt"),
    (r"\bseamless\b", "smls"),
    (r"\bgalvanized\b", "galv"),
    (r"\b(elbows?|ell)\b", "ell"), (r"\b(valves?|vlv)\b", "vlv"), (r"\b(flanges?|flg)\b", "flg"),
    (r"\b(gaskets?|gskt)\b", "gskt"), (r"\bspiral\s*-?\s*wound\b", "spw"),
    (r"\bmalleable(\s*iron)?\b", "mi"), (r"\bcarbon\s*steel\b", "cs"), (r"\bfull\s*port\b", "fp"),
    (r"\b(reducers?|red)\b", "red"), (r"\b(couplings?|cplg)\b", "cplg"),
]


def canon(s: str) -> str:
    s = s.lower().replace("″", '"').replace("”", '"')
    for pat, rep in EQUIV:
        s = re.sub(pat, rep, s)
    return re.sub(r"[^a-z0-9#/]", "", s)


def mentions(desc: str, phrase: str) -> bool:
    d = canon(desc)
    return any(canon(alt) and canon(alt) in d for alt in phrase.split("|"))


def toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+(?:[/.\-][a-z0-9]+)*", s.lower().replace("″", '"')))


def sim(a: str, b: str) -> float:
    ta, tb = toks(a), toks(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def qty_ok(ln: dict, kk: dict) -> bool:
    """README: qty_tol is a relative tolerance (0 = exact); both null counts as agreeing."""
    if ln["qty"] is None or kk["qty"] is None:
        return ln["qty"] is None and kk["qty"] is None
    return abs(ln["qty"] - kk["qty"]) <= (kk.get("qty_tol") or 0) * abs(kk["qty"])


def line_problems(ln: dict, kk: dict) -> list[tuple[str, str]]:
    """Everything wrong with one paired line against its key line, as (kind, plain words).
    kind: qty | uom | source | context | match. The one definition the scorer, the viewer,
    the report and the desk export all use."""
    out = []
    if kk.get("qty_tol") is not None and not qty_ok(ln, kk):
        out.append(("qty", f"quantity should be {kk['qty']}"))
    if (ln.get("uom") or "EA") != (kk.get("uom") or "EA"):
        out.append(("uom", f"unit should be {kk.get('uom') or 'EA'}"))
    if ln.get("source") != kk.get("source"):
        out.append(("source", f"should come from {kk.get('source')}"))
    out += [("context", f"description should mention '{m}'") for m in kk.get("must_mention") or []
            if not mentions(ln.get("description") or "", m)]
    out += [("context", f"description should not say '{m}'") for m in kk.get("must_not_mention") or []
            if mentions(ln.get("description") or "", m)]
    if kk.get("match") and "match" in ln and (kk["match"], kk.get("sku")) != (ln["match"], ln.get("sku")):
        out.append(("match", f"catalog: answer key says {kk['match']} {kk.get('sku') or ''}".strip()))
    return out


def pair(ours: list[dict], keys: list[dict], thresh: float = 0.5):
    cand = sorted(((sim(o["raw"], k["raw"]), i, j) for i, o in enumerate(ours) for j, k in enumerate(keys)), reverse=True)
    used_o, used_k, pairs = set(), set(), []
    for s, i, j in cand:
        if s < thresh:
            break
        if i in used_o or j in used_k:
            continue
        used_o.add(i)
        used_k.add(j)
        pairs.append((i, j))
    return pairs, [i for i in range(len(ours)) if i not in used_o], [j for j in range(len(keys)) if j not in used_k]


def main(argv) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    verbose = "-v" in argv
    argv = [a for a in argv if a != "-v"]
    parsed = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    key = json.loads(Path(argv[1] if len(argv) > 1 else "data/dev/answer_key.json").read_text(encoding="utf-8"))["emails"]

    c = dict(emails=0, cls_ok=0, silent=0, lost=0, phantom=0, key_lines=0, our_lines=0, paired=0,
             halluc=0, qty_ok=0, qty_n=0, uom_ok=0, src_ok=0, ctx_ok=0, ctx_n=0,
             link_ok=0, link_n=0, rv_tp=0, rv_fp=0, rv_fn=0, m_n=0, m_ok=0, m_wrong=0, nic_n=0, nic_ok=0)
    problems: list[str] = []
    for eid, k in sorted(key.items()):
        o = parsed.get(eid)
        c["emails"] += 1
        if o is None:
            problems.append(f"{eid}: MISSING")
            continue
        kc, oc = k["classification"], o["classification"]
        c["cls_ok"] += kc == oc
        if kc != oc:
            problems.append(f"{eid}: class {oc} (key {kc})")
        if kc in REQ and oc not in REQ:
            c["lost"] += 1
        if kc not in REQ and oc in REQ:
            c["phantom"] += 1
        needs_action = bool(k.get("line_items")) or k.get("needs_review")
        if needs_action and not o["line_items"] and not o["needs_review"]:
            c["silent"] += 1
            problems.append(f"{eid}: SILENT LOSS")
        for f in ("revises", "duplicate_of"):
            if k.get(f) or o.get(f):
                c["link_n"] += 1
                c["link_ok"] += k.get(f) == o.get(f)
                if k.get(f) != o.get(f):
                    problems.append(f"{eid}: {f} {o.get(f)} (key {k.get(f)})")
        kr, orv = bool(k.get("needs_review")), bool(o["needs_review"])
        c["rv_tp"] += kr and orv
        c["rv_fp"] += orv and not kr
        c["rv_fn"] += kr and not orv
        if kr != orv:
            problems.append(f"{eid}: needs_review {orv} (key {kr}) {o.get('review_reason') or ''}")

        kl, ol = k.get("line_items") or [], o["line_items"]
        c["key_lines"] += len(kl)
        c["our_lines"] += len(ol)
        pairs, extra, missed = pair(ol, kl)
        c["paired"] += len(pairs)
        c["halluc"] += len(extra)
        for i in extra:
            problems.append(f"{eid}: EXTRA line {ol[i]['raw']!r}")
        for j in missed:
            problems.append(f"{eid}: MISSED line {kl[j]['raw']!r}")
        for i, j in pairs:
            ln, kk = ol[i], kl[j]
            if kk.get("qty_tol") is not None:
                c["qty_n"] += 1
                q_ok = qty_ok(ln, kk)
                c["qty_ok"] += q_ok
                if not q_ok:
                    problems.append(f"{eid}: qty {ln['qty']} (key {kk['qty']}) on {kk['raw']!r}")
            u_ok = (ln["uom"] or "EA") == (kk["uom"] or "EA")
            c["uom_ok"] += u_ok
            if not u_ok:
                problems.append(f"{eid}: uom {ln['uom']} (key {kk['uom']}) on {kk['raw']!r}")
            s_ok = ln["source"] == kk["source"]
            c["src_ok"] += s_ok
            if not s_ok:
                problems.append(f"{eid}: source {ln['source']} (key {kk['source']})")
            must, mustnt = kk.get("must_mention") or [], kk.get("must_not_mention") or []
            if must or mustnt:
                c["ctx_n"] += 1
                miss = [m for m in must if not mentions(ln["description"], m)]
                bad = [m for m in mustnt if mentions(ln["description"], m)]
                c["ctx_ok"] += not miss and not bad
                if miss or bad:
                    problems.append(f"{eid}: context {kk['raw']!r} -> {ln['description']!r} missing={miss} forbidden={bad}")
            # optional matcher (README): confident wrong = we named a part and the answer differs
            if "match" in ln and kk.get("match"):
                c["m_n"] += 1
                m_ok = ln["match"] == kk["match"] and ln.get("sku") == kk.get("sku")
                c["m_ok"] += m_ok
                c["m_wrong"] += ln["match"] == "matched" and not m_ok
                c["nic_n"] += kk["match"] == "not_in_catalog"
                c["nic_ok"] += kk["match"] == "not_in_catalog" and ln["match"] == "not_in_catalog"
                if not m_ok:
                    problems.append(f"{eid}: match {ln['match']}/{ln.get('sku')} (key {kk['match']}/{kk.get('sku')}) "
                                    f"on {ln['description']!r}: {ln.get('match_reason')}")

    pct = lambda a, b: f"{100 * a / b:.1f}%" if b else "n/a"  # noqa: E731
    print(f"silent losses        {c['silent']}")
    print(f"hallucinated lines   {c['halluc']}")
    print(f"lost / phantom       {c['lost']} / {c['phantom']}")
    print(f"classification       {c['cls_ok']}/{c['emails']} {pct(c['cls_ok'], c['emails'])}")
    print(f"line recall          {c['paired']}/{c['key_lines']} {pct(c['paired'], c['key_lines'])}")
    print(f"line precision       {c['paired']}/{c['our_lines']} {pct(c['paired'], c['our_lines'])}")
    print(f"qty | uom | source   {pct(c['qty_ok'], c['qty_n'])} | {pct(c['uom_ok'], c['paired'])} | {pct(c['src_ok'], c['paired'])}")
    print(f"context carried      {c['ctx_ok']}/{c['ctx_n']} {pct(c['ctx_ok'], c['ctx_n'])}")
    print(f"revises/duplicate_of {c['link_ok']}/{c['link_n']}")
    print(f"needs_review P / R   {pct(c['rv_tp'], c['rv_tp'] + c['rv_fp'])} / {pct(c['rv_tp'], c['rv_tp'] + c['rv_fn'])}")
    if c["m_n"]:
        print(f"matcher: confident wrong {c['m_wrong']} | not-in-catalog recall {c['nic_ok']}/{c['nic_n']} "
              f"{pct(c['nic_ok'], c['nic_n'])} | match accuracy {c['m_ok']}/{c['m_n']} {pct(c['m_ok'], c['m_n'])}")
    if problems:
        print(f"\n{len(problems)} problem(s)" + ("" if verbose else " (-v to list)"))
        if verbose:
            print("\n".join("  " + p for p in problems))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
