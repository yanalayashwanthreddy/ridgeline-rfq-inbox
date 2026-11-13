"""Catalog matcher: an extracted line -> one stocked part, "not in catalog", or "ambiguous".

Code decides, not the model. A wrong part is a wrong quote, and every rule here can be
checked by a rep in one line of `match_reason`:

  1. Read the line's description into the catalog's own attributes: product, size, spec
     (schedule / pressure class), material, ends, and stated features (full port, inner ring...).
  2. Candidates = catalog parts with the same product and the same size. None -> not in catalog.
  3. Every attribute the customer STATED must agree. Nothing left -> not in catalog, and the
     reason names the nearest stocked part and what differs ("stocked only in SCH 80").
  4. Exactly one left -> matched. Several left -> they differ in something the customer didn't
     say -> ambiguous (ask the customer), never a guess.

The vocabulary is the catalog's, plus standard trade synonyms (README: Sch/Schedule, 150#/Class
150, BW/butt weld, threaded/NPT, SMLS/seamless, galv/galvanized). No dev-set strings.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

# ---- products: (pattern, catalog product names). Specific before generic; first hit by
# position in the text wins, and "pipe" only counts when nothing else is named.
PRODUCTS: list[tuple[str, set[str]]] = [
    (r"swing\s*check|swing\s*chk", {"swing check valve"}),
    (r"check\s*valve|\bvlv\s*check|\bcheck\b", {"check valve", "swing check valve"}),
    (r"ball\s*valve|\bvlv\s*ball", {"ball valve"}),
    (r"gate\s*valve|\bvlv\s*gate", {"gate valve"}),
    (r"globe\s*valve|\bvlv\s*globe", {"globe valve"}),
    (r"butterfly|\bbfly\b", {"butterfly valve"}),
    (r"\by[\s-]*strainer|\bstrainer\b", {"y-strainer"}),
    (r"street\s*(?:elbow|ell)|\bst\s*ell\b", {"street elbow"}),
    # up to three words between the angle and the noun: "90 degree threaded elbow"
    (r"\b45\s*(?:°|deg(?:ree)?s?\.?)?\s*(?:[a-z&/-]+,?\s+){0,3}?(?:elbow|ell)s?\b|(?:elbow|ell)s?,?\s*45\b", {"45 elbow"}),
    (r"\b90\s*(?:°|deg(?:ree)?s?\.?)?\s*(?:[a-z&/-]+,?\s+){0,3}?(?:elbow|ell)s?\b|(?:elbow|ell)s?,?\s*90\b", {"90 elbow"}),
    (r"\b(?:elbow|ell)s?\b", {"90 elbow", "45 elbow"}),
    (r"concentric|\bconc\b", {"concentric reducer"}),
    (r"eccentric|\becc\b", {"eccentric reducer"}),
    (r"\breducer\b|\bred\b", {"concentric reducer", "eccentric reducer"}),
    (r"\bblind\b", {"blind flange"}),
    (r"slip[\s-]*on|\bs/?o\b\s*(?:flange|flg)|(?:flange|flg)\s*s/?o\b", {"slip-on flange"}),
    (r"weld[\s-]*neck|\bw/?n\b\s*(?:flange|flg)?|(?:flange|flg)\s*w/?n\b", {"weld neck flange"}),
    (r"\bflanges?\b|\bflg\b", {"blind flange", "slip-on flange", "weld neck flange"}),
    (r"spiral[\s-]*wound|\bspw\b", {"spiral wound gasket"}),
    (r"stud\s*bolt|\bstuds?\b", {"stud bolt"}),
    (r"heavy\s*hex\s*nut|\bhh\s*nut", {"heavy hex nut"}),
    (r"hex\s*cap\s*screw|\bhcs\b", {"hex cap screw"}),
    (r"spring\s*nut", {"channel spring nut"}),
    (r"hex\s*nut", {"hex nut"}),
    (r"half[\s-]*coupling|half[\s-]*cplg", {"half coupling"}),
    (r"coupling|\bcplg\b", {"coupling"}),
    (r"bushing|\bbush\b", {"hex bushing"}),
    (r"\bunion\b", {"union"}),
    (r"\bnipples?\b", {"nipple"}),
    (r"\bplugs?\b", {"plug"}),
    (r"\bcaps?\b", {"cap"}),
    (r"\btees?\b", {"tee"}),
    (r"clevis", {"clevis hanger"}),
    (r"pipe\s*clamp", {"pipe clamp"}),
    (r"threaded\s*rod|all[\s-]*thread", {"threaded rod"}),
    (r"strut|unistrut", {"strut channel"}),
    (r"thread\s*tape|ptfe\s*tape|teflon\s*tape", {"ptfe thread tape"}),
    (r"thread\s*dope|pipe\s*dope", {"pipe thread dope"}),
]
PIPE = (r"\bpipe\b", {"pipe"})
PIPE_LIKE = {"pipe", "nipple"}

# ---- materials: (pattern, predicate on the catalog material). Stainless grades are read
# first; carbon-steel words only apply when no stainless grade is named.
STAINLESS = [
    (r"316|\bcf8m\b|\bcf3m\b", lambda m: "316" in m),  # CF8M / CF3M: cast 316 (valve bodies)
    (r"304|\bcf8\b|\bcf3\b", lambda m: "304" in m),
    (r"stainless|\bss\b", lambda m: "SS" in m or "304" in m or "316" in m),
]
OTHER_MATERIALS = [
    (r"\ba105\b|forged\s*steel", lambda m: m == "A105"),
    (r"\bwpb\b|a234", lambda m: m == "A234 WPB"),
    (r"a106|seamless|\bsmls\b", lambda m: m == "A106B SMLS"),
    # A53B / ERW name the uncoated grade; galvanized pipe is its own catalog material
    (r"a53b|\berw\b", lambda m: m == "A53B ERW"),
    (r"\ba53\b", lambda m: m.startswith("A53")),
    (r"galv", lambda m: "GALV" in m),
    # "black" means uncoated: black pipe is A53B/A106B, black malleable is MI BLACK
    (r"\bblack\b", lambda m: "GALV" not in m),
    (r"malleable|\bmi\b", lambda m: m.startswith("MI ")),
    (r"bronze", lambda m: m == "BRONZE"),
    (r"brass", lambda m: m == "BRASS"),
    (r"cast\s*iron|\bci\b", lambda m: m == "CAST IRON"),
    (r"\bwcb\b|a216", lambda m: m == "WCB"),
    (r"\bb8m\b", lambda m: m.startswith("B8M")),
    (r"\bb7\b", lambda m: m.startswith("B7")),
    (r"\b2h\b", lambda m: "2H" in m),
    (r"\bzinc\b", lambda m: "ZINC" in m),
    (r"carbon\s*steel|\bcs\b", lambda m: m in {"A105", "A234 WPB", "A106B SMLS", "A53B ERW", "WCB", "CARBON STEEL"}),
    # Materials and grades the customer can name that the catalog does not carry. Ignoring them
    # would quote the nearest steel part (an A350 LF2 flange as A105): a confident wrong match.
    (NOT_STOCKED_MATERIAL := r"copper|\bcu\b|\bpvc\b|\bcpvc\b|\bhdpe\b|\bpe100\b|duplex|super\s*duplex|\b2205\b|\b321\b|\b347\b"
     r"|\b317l?\b|alloy\s*20|monel|inconel|hastelloy|titanium|alumin(?:um|ium)|ductile|chrome[\s-]*moly"
     r"|\blf[1-3]\b|\bwpl[36]\b|\b[fpw]p?(?:5|9|11|22|51|53|91)\b", lambda m: False),
    # any ASTM standard the catalog's materials don't come from (A350, A694, A333...): a list of
    # bad grades can never be complete, so the rule is "known standards only"
    # ("A350", "A-350", "ASTM A 350"; not the article in "a 90 elbow")
    (r"(?:\bastm\s*a\s*-?|\ba-?)(?!(?:53|105|106|126|182|193|194|197|216|234|312|351|403)\b)\d{2,3}\b(?![\"'#/-])",
     lambda m: False),
]

# ---- ends. Pipe and nipples use pipe-end codes; everything else uses connection codes.
PIPE_ENDS = [
    (r"threaded\s*(?:&|and)\s*coupled|\bt\s*&\s*c\b", {"T&C"}),
    (r"threaded\s*one\s*end|\btoe\b", {"TOE"}),
    (r"threaded\s*both\s*ends?|\btbe\b", {"TBE"}),
    (r"bevel(?:l?ed)?|\bbe\b", {"BE"}),
    (r"plain\s*ends?|\bpe\b", {"PE"}),
    (r"thread|\bthd\b|\bnpt\b|\bmpt\b|screwed", {"TBE", "T&C", "TOE"}),
]
ENDS = [
    (r"butt[\s-]*weld|\bbw\b", {"BW"}),
    (r"socket[\s-]*weld|\bsw\b", {"SW"}),
    (r"raised\s*face|\brf\b", {"RF"}),
    (r"flat\s*face|\bff\b", {"FF"}),
    (r"ring[\s-]*(?:type[\s-]*)?joint|\brtj\b", {"RTJ"}),
    (r"thread|\bthd\b|\b[fm]?npt\b|\bmpt\b|\bfpt\b|screwed", {"THD"}),
    (r"\bflanged\b", {"RF", "FF", "RTJ"}),
    # end types the catalog does not carry: stated, they can only mean "not in catalog"
    (r"solder|sweat|\bc\s*x\s*c\b|grooved|victaulic|press[\s-]*fit|compression|flare", {"(not stocked)"}),
]

# ---- features the customer can state and the catalog records in `extra`.
FEATURES = [
    (r"full\s*port|\bfp\b", "full port"),
    (r"(?:std|standard|reduced|regular)\s*port", "std port"),
    (r"locking\s*handle|lockable", "locking handle"),
    (r"\bwafer\b", "wafer"),
    (r"\blug(?:ged)?\b", "lug"),
    (r"long\s*radius|\blr\b", "LR"),
    (r"short\s*radius|\bsr\b", "SR"),
    (r"inner\s*ring", "inner ring"),
]

# products whose catalog size is "diameter x length"
TWO_PART = {"stud bolt", "nipple", "hex cap screw"}

FRACTIONS = {".25": "1/4", ".5": "1/2", ".75": "3/4", ".125": "1/8", ".375": "3/8"}
NUM = r"\d+-\d+/\d+|\d+/\d+|\d+"
SIZE = re.compile(
    rf"(?<![\d/.#-])({NUM})\s*(\"|''|in\b|inch(?:es)?\b)?"
    rf"(?:\s*x\s*({NUM}|close)\s*(\"|''|in\b|inch(?:es)?\b)?)?",
    re.I,
)


@dataclass
class Part:
    sku: str
    description: str
    product: str
    size: str
    spec: str
    material: str
    ends: str
    extra: str


@dataclass
class Wanted:
    products: set[str] = field(default_factory=set)
    size: str | None = None
    specs: set[str] = field(default_factory=set)
    material: list = field(default_factory=list)  # predicates, all must hold
    material_words: list[str] = field(default_factory=list)
    ends: set[str] | None = None
    ends_word: str = ""
    features: set[str] = field(default_factory=set)


def load_catalog(path: Path) -> list[Part]:
    with open(path, encoding="utf-8", newline="") as f:
        return [Part(**{k: (r[k] or "").strip() for k in Part.__dataclass_fields__}) for r in csv.DictReader(f)]


def _norm_text(s: str) -> str:
    s = s.replace("″", '"').replace("”", '"').replace("“", '"').replace("°", "°")
    s = re.sub(r"(\d+)\s+(\d+/\d+)", r"\1-\2", s)  # 1 1/2 -> 1-1/2
    s = re.sub(r"(\d+)(\.\d+)", lambda m: f"{m[1]}-{FRACTIONS[m[2]]}" if m[2] in FRACTIONS else m[0], s)
    s = re.sub(r"\b0-(\d+/\d+)", r"\1", s)  # 0.75 -> 0-3/4 -> 3/4
    # bolt thread pitch: 5/8"-11, 3/4-10 UNC -> keep the diameter, drop the pitch
    s = re.sub(r"(\d+/\d+|\b\d)(\"?)-(\d{1,2})(?![\d/])(?:\s*unc)?", r"\1\2", s, flags=re.I)
    return s


def _size(s: str) -> str | None:
    for m in SIZE.finditer(s):
        a, mark_a, b, mark_b = m.groups()
        if not (mark_a or mark_b):
            continue  # a bare number is a qty, a class or a length, not a size
        return f"{a} x {b.lower()}" if b else a
    return None


def read_line(description: str) -> Wanted:
    s = _norm_text(description)
    low = s.lower()
    w = Wanted()

    hits = []
    for pat, names in PRODUCTS:
        m = re.search(pat, low)
        if m:
            hits.append((m.start(), names))
    if hits:
        w.products = min(hits, key=lambda h: h[0])[1]
    elif re.search(PIPE[0], low):
        w.products = PIPE[1]

    w.size = _size(s)
    if w.size and " x " not in w.size and w.products & TWO_PART:
        # the length is often written apart from the diameter: "close nipple", 'B7, 4-1/2" long'
        m = SIZE.search(s)
        rest = s[m.end():] if m else ""
        if re.search(r"\bclose\b", s, re.I):
            w.size += " x close"
        elif (n := _size(rest)) and " x " not in n:
            w.size += f" x {n}"

    for m in re.finditer(r"\bsch(?:edule)?\.?\s*(\d+)\s*(s)?\b", low):
        w.specs.add(f"SCH {m[1]}{'S' if m[2] else ''}")
    for m in re.finditer(r"\b(\d{2,4})\s*(?:#|lbs?\b|-?pound)|\bclass\s*(\d{2,4})\b|\b(\d{2,4})\s*class\b", low):
        w.specs.add(f"{m[1] or m[2] or m[3]}#")
    for m in re.finditer(r"\b(\d{3,4})\s*(?:psi\s*)?wog\b", low):
        w.specs.add(f"{m[1]} WOG")
    if re.search(r"\bstd\b(?!\s*port)|\bstandard\s*wall", low) and not w.specs:
        w.specs.add("SCH 40")
    if re.search(r"\bxs\b|extra\s*strong", low) and not w.specs:
        w.specs.add("SCH 80")

    stainless = [(p, f) for p, f in STAINLESS if re.search(p, low)]
    for pat, pred in stainless or []:
        w.material.append(pred)
        w.material_words.append(re.search(pat, low)[0])
        break  # the most specific grade named
    for pat, pred in OTHER_MATERIALS:
        if stainless and pat.startswith(("\\ba105", "\\bwpb", "a106", "\\ba53", "carbon")):
            continue
        m = re.search(pat, low)
        if m:
            w.material.append(pred)
            w.material_words.append(m[0])

    is_pipe = bool(w.products & PIPE_LIKE)
    for pat, codes in PIPE_ENDS if is_pipe else ENDS:
        m = re.search(pat, low)
        if m:
            w.ends, w.ends_word = codes, m[0]
            break

    for pat, name in FEATURES:
        if re.search(pat, low):
            w.features.add(name)
    return w


@dataclass
class Match:
    match: str  # matched | not_in_catalog | ambiguous
    sku: str | None
    reason: str
    candidates: list[str] = field(default_factory=list)

    def fields(self) -> dict:
        d = {"sku": self.sku, "match": self.match, "match_reason": self.reason}
        if self.match == "ambiguous" and self.candidates:
            d["candidates"] = self.candidates
        return d


def _spec_ok(part_spec: str, wanted: set[str], size: str) -> bool:
    """SCH 40/40S and 80/80S are the same wall up to 8" (ASME B36.19), and customers mix them."""
    if part_spec in wanted:
        return True
    m = re.fullmatch(r"(\d+)(?:-(\d+)/(\d+))?|(\d+)/(\d+)", size.split(" x ")[0])
    if not m:
        return False
    nps = int(m[1]) + int(m[2]) / int(m[3]) if m[1] and m[2] else int(m[1]) if m[1] else int(m[4]) / int(m[5])
    same_wall = {"SCH 40": "SCH 40S", "SCH 40S": "SCH 40", "SCH 80": "SCH 80S", "SCH 80S": "SCH 80"}
    return nps <= 8 and same_wall.get(part_spec) in wanted


def _misses(p: Part, w: Wanted) -> list[str]:
    """Stated attributes this part does not satisfy, in words a rep reads."""
    out = []
    if w.specs and not _spec_ok(p.spec, w.specs, p.size):
        out.append(f"{p.spec or 'no rating'} not {'/'.join(sorted(w.specs))}")
    if w.material and not all(f(p.material) for f in w.material):
        out.append(f"{p.material or 'no material'} not {' '.join(w.material_words)}")
    if w.ends and p.ends not in w.ends:
        out.append(f"{p.ends or 'no end type'} not {w.ends_word}")
    extra = p.extra.lower()
    for feat in sorted(w.features):
        if feat.lower() not in extra and not (feat == "std port" and "std port" in extra):
            out.append(f"no {feat}")
    return out


def match_line(description: str, catalog: list[Part]) -> Match:
    w = read_line(description)
    if not w.products:
        return Match("ambiguous", None, "could not read the product from the description; a rep must pick the part")
    if w.size:
        same = [p for p in catalog if p.product in w.products and p.size == w.size]
    else:
        # some products are stocked without a size ("PTFE thread tape")
        same = [p for p in catalog if p.product in w.products and not p.size]
        if not same:
            return Match("ambiguous", None, "could not read the size from the description; a rep must pick the part")
    if not same:
        kinds = " or ".join(sorted(w.products))
        return Match("not_in_catalog", None, f'no {kinds} stocked in {w.size}"')

    # an attribute the catalog doesn't record for this product cannot disagree ("all-thread rod")
    if w.specs and not any(p.spec for p in same):
        w.specs = set()
    if w.material and not any(p.material for p in same):
        w.material, w.material_words = [], []
    if w.ends and not any(p.ends for p in same):
        w.ends = None
    fits = [p for p in same if not _misses(p, w)]
    if not fits:
        near = min(same, key=lambda p: (len(_misses(p, w)), p.sku))
        return Match("not_in_catalog", None,
                     f"nearest stocked part {near.sku} {near.description} is off: {'; '.join(_misses(near, w))}")
    if len(fits) == 1:
        p = fits[0]
        return Match("matched", p.sku, f"{p.description}: the only stocked part with every stated attribute")

    differ = [a for a in ("product", "spec", "material", "ends", "extra") if len({getattr(p, a) for p in fits}) > 1]
    return Match("ambiguous", None,
                 f"{len(fits)} stocked parts fit; customer did not state {', '.join(differ)}",
                 [p.sku for p in fits])


def alternatives(description: str, catalog: list[Part], limit: int = 25) -> list[Part]:
    """Stocked parts a rep might pick instead: same product and size, whatever else differs."""
    w = read_line(description)
    if not w.products:
        return []
    return [p for p in catalog if p.product in w.products and p.size == (w.size or "")][:limit]


def match_all(parsed: dict, catalog: list[Part]) -> None:
    """Add sku / match / match_reason (and candidates) to every line, in place."""
    for rec in parsed.values():
        for ln in rec.get("line_items") or []:
            try:
                ln.update(match_line(ln.get("description") or "", catalog).fields())
            except Exception as e:  # a matcher bug must never cost the parse
                ln.update({"sku": None, "match": "ambiguous", "match_reason": f"matcher error: {type(e).__name__}"})
