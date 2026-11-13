# MATCHER

Built: [rfq/matcher.py](rfq/matcher.py). It runs inside `parse` and adds `sku` / `match` /
`match_reason` (and `candidates` when ambiguous) to every line. It is code only: no model calls,
so it costs nothing and the keyless replay produces it too. Tests: [tests/test_matcher.py](tests/test_matcher.py).

## The decision

The costly failure is a **confident wrong part**: the nearest part, one attribute off, quoted
as if it were what the customer asked for. So the matcher only says `matched` when exactly one
stocked part agrees with everything the customer wrote, and it says why in one checkable line.

1. **Normalise the line into the catalog's own columns.** These are product, size, spec (schedule
   or pressure class), material, ends, and stated features (full/std port, LR/SR, wafer, lug,
   inner ring). Sizes: `1 1/2"`, `1.5"` → `1-1/2`; `8" x 6"` → `8 x 6`; bolt thread pitch
   dropped (`5/8"-11` → `5/8`); a length written apart from the diameter is joined
   (`close nipple`, `B7, 4-1/2" long`). Specs: `Class 150`/`150 lb` → `150#`; `Schedule 40`
   → `SCH 40`. Ends: butt weld → BW, NPT/threaded → THD (pipe: TBE / T&C / TOE / PE / BE).
   These are the README's equivalences plus standard trade usage. No dev strings.
2. **Candidates = same product and same size.** If there are none: `not_in_catalog`
   ("no y-strainer stocked in 6\"").
3. **Every attribute the customer stated must agree.** An attribute the customer *didn't*
   state is not a requirement, unless the remaining parts differ on it (step 4).
   - Schedules 40/40S and 80/80S count as equal up to 8" (same wall per ASME B36.19), not above.
   - "A53B" or "black pipe" excludes galvanized.
   - An attribute the catalog doesn't record for that product (rod has no end type) cannot
     disagree.
4. **One part left → `matched`.** It names the part. **None left → `not_in_catalog`.** The reason
   names the nearest stocked part and exactly what's off: "nearest RL-43757 FLG BLIND 4\" 150#
   A105 RF is off: 150# not 300#". A rep checks that in seconds. **Several left → `ambiguous`**,
   with the candidates and the attribute the customer didn't state ("customer did not state
   product": slip-on, weld neck or blind?). That is "ask the customer", never a guess.
5. **A material, grade or end the catalog doesn't carry means `not_in_catalog`, never the nearest
   steel part.** This covers copper, PVC, duplex, 321/347, chrome-moly P11/P22, solder/grooved
   ends, and **any ASTM standard the catalog's materials don't come from** (A350, A694, A333…).
   It is "known standards only", because a list of bad grades is never complete. Without this,
   an A350 LF2 low-temperature flange would "match" the A105 flange: right size, right class,
   wrong metal. A line whose product can't be read is `ambiguous`, never matched.

## Advice for the rep (never changes `qty` or the match)

- **Unit.** The customer's unit is compared with the unit we sell in. Dev has 12 pipe lines
  asked in joints ("12 jts") but sold per foot. Quoting 12 against a per-foot price is a wrong
  quote by a factor of 20. With a stated length the reason does the arithmetic
  ("20 × 21 ft lengths = 420 FT"); without one it says "ask the footage". Gaskets asked in SETs
  but sold each get "check the quantity". `qty` stays as stated, as the README requires.
- **Ambiguous, but the request decides it.** A 4" 316/graphite gasket is stocked in 150# and
  300#. When the same request's matched flanges are 300#, the 300# gasket is listed first and
  named as likely. It stays `ambiguous` with no SKU, so a person confirms; the viewer
  pre-selects it, so that's one click. Only a single survivor is ever suggested.

## Where a model helps, and what it must never decide

**Helps:**
- reading messy descriptions into attributes when the rules find nothing ("FS 90 3M SW" style
  shorthand);
- for an ambiguous line, drafting the question to the customer.

**Never decides:**
- the SKU;
- that a part is "close enough";
- that an unstated attribute can be assumed.

Any model suggestion would go back through steps 2–4, so the model can only *narrow* to a part
that code confirms, never pick one. I didn't add a model step: on dev the rules alone reach
every line, and an unexplained model choice is exactly the confident-wrong risk.

## How it's measured

The scorer (`scripts/score.py`) prints the README's three numbers. Confident wrong comes first.

| | dev (24 emails, 210 lines) |
|---|---|
| confident wrong matches | **0** |
| not-in-catalog recall | **16/16** |
| match accuracy | **210/210** |

**The generated test (`scripts/matcher_fuzz.py`).** This is the check that mattered. Every
catalog part is written as a customer would write it, in random wording and order (WNRF,
class 150, SS316, 2 1/2 inch, 3M…). The expected answers come from the catalog *table*, not the
matcher's logic:
- **fully stated:** must give that SKU;
- **one attribute swapped for one not stocked:** must give not-in-catalog;
- **material left out:** gives whatever the table says is left.

The **first run found 113 confident wrong matches** on the version that scored 210/210 on dev.
- Named grades were too loose: WP316L on a valve matched the F316L valve.
- `A-105 class 150` was read as class 105.
- WNRF/SORF and 3M were unknown.
- My 40S equivalence ran in both directions.

After the fixes: **0 confident wrong in ~19,700 lines over four seeds**. The 40/40S, metal-family
and twin-part policies are written into the generator and documented here. A pytest runs one seed
on every test run. Caveat: the generator's vocabulary is mine, so it tests combinations and order,
not wording neither of us thought of.

Dev is also what I tuned on, so it overstates. The first unseen check was the stress inbox
(118 lines, no SKU key). There I read every decision, and it exposed the copper/A350 gap (step 5).
The tests pin traps that aren't in dev: one attribute off, a stocked-elsewhere grade, an
unstated flange type, the 40S/80S boundary.

In production the real measure is the **rep overturn rate**. The viewer's accept/overturn buttons
write `parsed.rep_decisions.json` next to the system's answer, not over it. Every overturn of a
`matched` line is a confident-wrong to investigate.

**Held-out prediction:** confident wrong **0–2**, not-in-catalog recall **~85%**, match
accuracy **~85%** of paired lines.

## Known weaknesses

- **It reads only our `description`.** If extraction drops the schedule from a line, the matcher
  can't see it. It then says `ambiguous` (safe) or, if only one part exists, `matched` on less
  than the customer wrote.
- **Non-ASTM material names are still a list.** Unknown ASTM numbers are caught generically, but a
  trade name nobody listed ("Hastelloy" is listed; "Alloy 625" is not) is ignored and could
  match a steel part.
- **Unstated trims and features are accepted.** A gate valve "800# A105 SW" matches the
  13CR-trim part, because that is the only one stocked. A rep may want that confirmed.
