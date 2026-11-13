# MATCHER

[rfq/matcher.py](rfq/matcher.py) runs inside `parse` and adds `sku` / `match` / `match_reason`
(and `candidates` when ambiguous) to every line. It is code only: no model calls, $0, and the
keyless replay includes it.

## The decision

The costly failure is a **confident wrong part**: the nearest part, one attribute off, quoted as
what the customer asked for. So `matched` means exactly one stocked part agrees with everything
the customer wrote, and the reason says why in one checkable line.

1. **Normalise into the catalog's columns:** product, size, spec, material, ends, and stated
   features (port, LR, wafer, lug, inner ring). Examples:
   - `1 1/2"`/`1.5"` → `1-1/2`;
   - bolt pitch `5/8"-11` → `5/8`;
   - `Class 150`/`150 lb` → `150#`, `3M` → `3000#`;
   - `A-105` → `A105`;
   - WNRF/SORF → flange type plus RF.

   These are the README's equivalences plus trade usage, never dev strings.
2. **Candidates = same product and size.** None → `not_in_catalog`.
3. **Every attribute the customer stated must agree.**
   - A named grade must hold on top of its metal family: WP316L is the fitting grade, so a valve
     asked for in WP316L is not our F316L valve.
   - SCH 40 also fits 40S up to 8" (same wall), but "40S" never fits carbon.
   - An attribute the catalog doesn't record for the product can't disagree.
4. **One left → `matched`. None → `not_in_catalog`**, naming the nearest part and what's off
   ("nearest RL-43757 FLG BLIND 4" 150# A105 RF is off: 150# not 300#"). **Several →
   `ambiguous`** with the candidates and the unstated attribute, which means ask the customer.
   One exception: if exactly one candidate is the plain part and the others add something
   unasked (a locking handle), the plain one is matched, and the reason says so.
5. **A material, grade or end we don't carry is `not_in_catalog`, never the nearest steel part.**
   This covers copper, PVC, duplex, 321, P11, solder/grooved, and *any* ASTM standard the
   catalog's materials don't come from (A350, A694…), because a list of bad grades is never
   complete. An unreadable product is `ambiguous`, never matched.

**Advice, which never changes `qty` or the match:**
- **Units.** 12 dev pipe lines are asked in joints but sold per foot, and quoting "12" per foot
  is ~20× wrong. With a stated length the reason computes it ("20 × 21 ft lengths = 420 FT");
  without one it says to ask.
- **Suggestions from the rest of the request.** A 4" gasket stocked in 150# and 300#, on a request
  whose flanges are 300#, lists the 300# gasket first as *likely*. It stays `ambiguous`, and the
  viewer pre-selects it for a one-click confirm.

## Where a model helps, and what it must never decide

A model could read shorthand the rules miss, or draft the question to the customer for an
ambiguous line. It must never pick the SKU, decide a part is "close enough", or assume an
unstated attribute. Any model proposal would go back through steps 2–5, so it can only narrow to a
part code confirms. I added no model step: the rules reach every dev line, and an unexplained
model choice is exactly the confident-wrong risk.

## How it's measured

The scorer prints the README's numbers, confident wrong first. On dev: **0 confident wrong,
not-in-catalog 16/16, accuracy 210/210**. That overstates, because I tuned on dev.

**The test that mattered** is `scripts/matcher_fuzz.py`. It writes every catalog part as a
customer might, in random wording and order. Its answers come from the catalog *table*, not the
matcher's logic:
- fully stated → that SKU;
- one attribute swapped for an unstocked one → not-in-catalog;
- material omitted → whatever the table leaves.

Its **first run found 113 confident wrong matches** in the version that scored 210/210 on dev
(loose grades, `A-105 class 150` read as class 105, unknown WNRF/3M, the 40S rule running both
ways). Now: **0 in ~19,700 lines over four seeds**, and one seed runs in the test suite. The
caveat is that the generator's vocabulary is mine: it tests combinations, not wording neither of
us thought of.

In production the measure is the **rep overturn rate**. The viewer's Accept / Overturn writes
`parsed.rep_decisions.json` beside the system's answer, never over it, and every overturned
`matched` line is a confident-wrong to investigate.

**Held-out prediction:** confident wrong 0–2, not-in-catalog recall ~85%, accuracy ~85% of paired
lines.

## Known weaknesses

- It reads only our `description`. A spec dropped upstream gives `ambiguous`, or a match on less
  than the customer wrote.
- Non-ASTM trade names are a list ("Alloy 625" isn't on it).
- Unstated trims are accepted: "gate valve 800# A105 SW" matches the only one stocked, which has
  13CR trim.
