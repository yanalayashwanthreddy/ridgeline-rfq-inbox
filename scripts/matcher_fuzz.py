"""Test the matcher on thousands of lines it was never tuned on, generated from the catalog.

    python scripts/matcher_fuzz.py [--per-part 3] [--seed 7] [-v]

The expected answers come from the catalog TABLE, never from the matcher's logic:
  stated  every attribute of a part, in customer wording/order        -> that SKU
  off     one attribute swapped for a value not stocked in that combo -> not_in_catalog
  omit    the material left out                                       -> the one part left,
                                                                         else ambiguous
Customer wording is trade usage (WNRF, class 150, SS316, 2 1/2 inch, 3M...), written as a table
below, separately from the matcher's own vocabulary.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfq.matcher import load_catalog, match_line  # noqa: E402

PRODUCT = {
    "pipe": ["pipe"], "nipple": ["nipple", "pipe nipple"],
    "tee": ["tee", "straight tee"], "cap": ["cap", "weld cap"],
    "90 elbow": ["90 elbow", "90° ell", "90 deg elbow", "90 ell", "elbow 90"],
    "45 elbow": ["45 elbow", "45° ell", "45 deg elbow", "45 ell"],
    "street elbow": ["street elbow", "street ell"],
    "blind flange": ["blind flange", "blind flg"], "slip-on flange": ["slip-on flange", "SO flange", "slip on flg"],
    "weld neck flange": ["weld neck flange", "WN flange", "weldneck flg"],
    "ball valve": ["ball valve"], "gate valve": ["gate valve"], "globe valve": ["globe valve"],
    "check valve": ["check valve"], "swing check valve": ["swing check valve"],
    "butterfly valve": ["butterfly valve"], "y-strainer": ["Y-strainer", "Y strainer"],
    "spiral wound gasket": ["spiral wound gasket", "SPW gasket", "spiral-wound gskt"],
    "coupling": ["coupling", "full coupling", "cplg"], "half coupling": ["half coupling"],
    "union": ["union"], "hex bushing": ["hex bushing", "bushing"], "plug": ["plug", "hex head plug"],
    "concentric reducer": ["concentric reducer", "conc reducer", "conc. red."],
    "eccentric reducer": ["eccentric reducer", "ecc reducer"],
    "stud bolt": ["stud bolt", "stud", "studs"],
}
# flange + raised face in one trade word
COMBO = {("blind flange", "RF"): "BLRF", ("slip-on flange", "RF"): "SORF", ("weld neck flange", "RF"): "WNRF"}

SPEC = {
    "SCH 40": ["SCH 40", "sch. 40", "schedule 40", "SCH40"], "SCH 80": ["SCH 80", "sch 80", "schedule 80", "SCH80"],
    "SCH 10": ["SCH 10", "schedule 10"], "SCH 160": ["SCH 160", "schedule 160"],
    "SCH 10S": ["SCH 10S", "sch 10s", "schedule 10S"], "SCH 40S": ["SCH 40S", "schedule 40S"],
    "SCH 80S": ["SCH 80S", "sch 80s"],
}
for c in ("125", "150", "300", "600", "800", "1500", "2000", "3000", "6000"):
    SPEC[f"{c}#"] = [f"{c}#", f"{c} lb", f"class {c}", f"{c} LB"] + ([f"{c[0]}M"] if c in ("2000", "3000", "6000") else [])
for c in ("200", "600", "1000", "2000"):
    SPEC[f"{c} WOG"] = [f"{c} WOG", f"{c} psi WOG"]

MATERIAL = {
    "A105": ["A105", "A-105", "forged steel A105"], "A234 WPB": ["A234 WPB", "WPB", "A234-WPB"],
    "A106B SMLS": ["A106B seamless", "A106 Gr. B SMLS", "seamless A106-B"],
    "A53B ERW": ["A53B ERW", "A53 Gr B ERW", "ERW A53-B"], "A53 GALV": ["galvanized", "A53 galv", "galv"],
    "A403 WP316L": ["A403 WP316L", "WP316L"], "A403 WP304L": ["A403 WP304L", "WP304L"],
    "316 SS": ["316 SS", "SS316", "316 stainless"], "304 SS": ["304 SS", "SS304", "304 stainless"],
    "A182 F316L": ["A182 F316L", "F316L"], "A182 F304L": ["A182 F304L", "F304L"],
    "A312 TP316L": ["A312 TP316L", "TP316L"], "A312 TP304L": ["A312 TP304L", "TP304L"],
    "MI GALV": ["galvanized malleable iron", "galv MI", "malleable iron, galvanized"],
    "MI BLACK": ["black malleable iron", "black MI"],
    "BRONZE": ["bronze"], "BRASS": ["brass"], "CAST IRON": ["cast iron"], "WCB": ["WCB", "A216 WCB", "cast steel WCB"],
    "B7/2H": ["B7 w/ 2H nuts", "B7 with 2 heavy hex nuts", "A193 B7 / A194 2H"], "B8M/8M": ["B8M w/ 8M nuts", "B8M/8M"],
    "304/GRAPHITE": ["304/graphite", "304 SS/graphite"], "316/GRAPHITE": ["316/graphite", "316 SS/graphite"],
}
ENDS = {
    "THD": ["THD", "threaded", "NPT", "FNPT"], "SW": ["SW", "socket weld", "socket-weld"],
    "BW": ["BW", "butt weld", "buttweld", "butt-weld"], "RF": ["RF", "raised face"], "FF": ["FF", "flat face"],
    "RTJ": ["RTJ", "ring joint"], "TBE": ["TBE", "threaded both ends"], "T&C": ["T&C", "threaded and coupled"],
    "PE": ["PE", "plain end", "plain ends"], "BE": ["BE", "beveled ends", "bevel end"], "TOE": ["TOE", "threaded one end"],
}
EXTRA = {"LR": ["LR", "long radius"], "full port": ["full port", "FP"], "std port": ["standard port", "std port"],
         "full port, locking handle": ["full port w/ locking handle"], "wafer": ["wafer"], "lug": ["lug", "lug style"],
         "13CR": ["13Cr trim", ""], "6 FT": ["6 ft"]}


def fam(m: str) -> str:
    """Metal family: 316 SS / WP316L / F316L / TP316L are one alloy; all galvanized are one."""
    return "316" if "316" in m else "304" if "304" in m else "GALV" if "GALV" in m else m


def small(size: str) -> bool:
    """NPS up to 8 inches."""
    a = size.split(" x ")[0]
    whole = a.split("-")[0]
    return "/" in whole or int(whole) <= 8


def size_words(rng, size: str, product: str) -> str:
    def one(s):
        f = {"1-1/2": "1.5", "2-1/2": "2.5", "1/2": "0.5", "3/4": "0.75", "1-1/4": "1.25"}
        return rng.choice([f'{s}"', f"{s} in", f"{s} inch"] + ([f'{f[s]}"'] if s in f else [])
                          + ([f'{s.replace("-", " ")}"'] if "-" in s else []))
    if " x " in size:
        a, b = size.split(" x ")
        if b == "close":
            return f"{one(a)} x close"
        if product == "stud bolt" and rng.random() < 0.4:
            return f"{one(a)}, {b}\" long"
        return rng.choice([f'{one(a)} x {one(b)}', f'{a}x{b}"'])
    return one(size)


def phrase(rng, p, *, spec=None, material=None, drop=()):
    spec = p.spec if spec is None else spec
    material = p.material if material is None else material
    words = []
    prod = rng.choice(PRODUCT[p.product])
    ends_used = False
    if (p.product, p.ends) in COMBO and rng.random() < 0.4:
        prod, ends_used = COMBO[(p.product, p.ends)], True
    if spec and "spec" not in drop:
        words.append(rng.choice(SPEC[spec]))
    if material and "material" not in drop:
        words.append(rng.choice(MATERIAL[material]))
    if p.ends and not ends_used:
        words.append(rng.choice(ENDS[p.ends]))
    if p.extra:
        words.append(rng.choice(EXTRA[p.extra]))
    rng.shuffle(words)
    words = [w for w in words if w]
    size = size_words(rng, p.size, p.product)
    form = rng.randrange(3)
    if form == 0:
        return f"{size} {prod}, {', '.join(words)}"
    if form == 1:
        return f"{size} {' '.join(words)} {prod}"
    return f"{prod}, {size}, {' '.join(words)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-part", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    cat = [p for p in load_catalog(Path(__file__).resolve().parents[1] / "data" / "catalog.csv")
           if p.product in PRODUCT and p.size]
    key = lambda p: (p.product, p.size, p.spec, p.material, p.ends, p.extra)  # noqa: E731
    counts = Counter(key(p) for p in cat)
    combos = {(p.product, p.size, p.spec, p.material, p.ends) for p in cat}

    cases = []  # (kind, description, expected match, expected sku)
    for p in cat:
        if counts[key(p)] > 1:
            continue
        # a sibling of the same metal family, otherwise identical ("304 SS" and "A182 F304L"
        # unions): a generic "304 stainless" can't choose between them, so ambiguous is right
        twin = any(q is not p and fam(q.material) == fam(p.material) and
                   (q.product, q.size, q.spec, q.ends, q.extra) == (p.product, p.size, p.spec, p.ends, p.extra) for q in cat)
        for _ in range(a.per_part):
            cases.append(("stated", phrase(rng, p), "matched|ambiguous" if twin else "matched", p.sku))
        # one attribute off: a spec of the same family, or another material, not stocked in this combo
        spec_alts = [s for s in SPEC if s != p.spec and s.split()[0] == p.spec.split()[0] and s[-1] == p.spec[-1]] if p.spec else []
        spec_alts = [s for s in spec_alts if (p.product, p.size, s, p.material, p.ends) not in combos
               and {s, p.spec} not in ({"SCH 40", "SCH 40S"}, {"SCH 80", "SCH 80S"})]
        if spec_alts:
            cases.append(("off-spec", phrase(rng, p, spec=rng.choice(spec_alts)), "not_in_catalog", None))
        # a truly different metal: "316 SS" and "A403 WP316L" are the same alloy in two grade
        # names, so a swap within 304 or 316 is not "off"
        # (and galvanized is one family: "galv" on a union means the galvanized malleable union).
        # Not stocked = no part of that metal family, at this spec or its same-wall S twin.
        specs_ok = {p.spec} | ({p.spec + "S"} if p.spec in ("SCH 40", "SCH 80") and small(p.size) else set())
        stocked_fams = {fam(q.material) for q in cat if (q.product, q.size, q.ends) == (p.product, p.size, p.ends)
                        and q.spec in specs_ok}
        mats = [m for m in MATERIAL if fam(m) != fam(p.material) and fam(m) not in stocked_fams]
        if p.material and mats:
            cases.append(("off-material", phrase(rng, p, material=rng.choice(mats)), "not_in_catalog", None))
        # material left out: what the table says is left
        if p.material:
            # policy (MATCHER.md): a plain "SCH 40/80" also fits a 40S/80S part up to 8"
            specs = {p.spec} | ({p.spec + "S"} if p.spec in ("SCH 40", "SCH 80") and small(p.size) else set())
            left = [q for q in cat if (q.product, q.size, q.ends, q.extra) == (p.product, p.size, p.ends, p.extra)
                    and q.spec in specs]
            cases.append(("omit-material", phrase(rng, p, drop=("material",)),
                          "matched" if len(left) == 1 else "ambiguous", p.sku if len(left) == 1 else None))

    by = defaultdict(Counter)
    fails = defaultdict(list)
    wrong = 0
    for kind, desc, exp, sku in cases:
        r = match_line(desc, cat)
        ok = r.match in exp.split("|") and (r.match != "matched" or r.sku == sku)
        by[kind]["n"] += 1
        by[kind]["ok"] += ok
        if r.match == "matched" and r.sku != sku:
            wrong += 1
            by[kind]["confident_wrong"] += 1
        if not ok:
            fails[kind].append(f"{desc!r}: want {exp} {sku or ''}, got {r.match} {r.sku or ''} | {r.reason}")
    total = sum(c["n"] for c in by.values())
    print(f"{total} generated lines from {len(cat)} parts (seed {a.seed})")
    for kind, c in by.items():
        print(f"  {kind:14} {c['ok']:5}/{c['n']:<5} {100 * c['ok'] / c['n']:5.1f}%   confident wrong {c['confident_wrong']}")
    print(f"confident wrong matches: {wrong}")
    for kind, fs in fails.items():
        print(f"\n{kind}: {len(fs)} failures" + ("" if a.v else " (first 8; -v for all)"))
        for f in fs if a.v else fs[:8]:
            print("  " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
