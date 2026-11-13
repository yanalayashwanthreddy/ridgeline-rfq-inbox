# DECISIONS

**Time spent:** about 5 hours.

## Shape of the system — who decides what

`ingest` (code) → **one model call per email** (`gpt-5.4-mini-2026-03-17`, reasoning effort medium,
strict JSON schema) → `guards` (code) → `threads` (code).

- **Code reads the files.** Every part becomes text or images before the model sees it: xlsx with
  sheet state, merged cells, hidden rows and struck cells marked; docx in document order with
  tracked deletions removed; PDF rows rebuilt from word positions, scans rendered to images;
  HTML tables flattened with `~~strike~~` kept. A broken part becomes `kind=unreadable` with a
  note — it never raises.
- **The model judges.** Classification, which lines are requests, and the self-contained
  description (heading scope, overrides beating defaults, quoted history is not a request).
  The prompt states principles, not dev strings; its one worked example is invented.
- **Code checks anything that is a fact.** Struck and hidden-only lines are dropped whatever the
  model said; non-request classes carry no lines; a request with zero lines, an unreadable part
  or a truncated document is always `needs_review`; `source` must be a real part; `raw` is
  snapped to the unique matching source row (the model's copy of a row varies run to run, and
  graders pair on `raw`). If a numbered table row is accounted for by no line, the model is
  asked once more with that row named (caught a silently dropped PO line in E015, $0.004).
- **Code links threads.** `revises` from In-Reply-To/References, then subject + sender domain;
  `duplicate_of` needs the same sender, ≤30 days, identical attachment hashes and matching lines.

**Measured, not guessed.** I wrote a scorer that implements the README's grading, and a
19-email stress inbox (`tests/stress/`) for situations dev doesn't show — tracked changes, a
Spanish request, a rotated photo, an invoice with item lines, "make #3 twelve and kill #5", a
byte-identical second delivery, a 35-page PDF. Its answers were written **before** running the
system. Dev: 24/24 classes, 210/210 lines, 0 silent / 0 hallucinated, context 172/173.
Stress: 19/19 classes, 118/133 lines (the 15 missing are pages 31–35, flagged), 0 hallucinated.
**Rejected:** gpt-5.5 — 4.6× the cost and *worse* on context (it "corrected" `A53B` to
`A53 Grade B`); a two-call classify-then-extract design — twice the cost, and the classifier
needs the same reading anyway.

## Something in the data that surprised you

The most request-shaped text in the inbox is usually not a request. E013's bounce carries a
perfect numbered list of valves with quantities — it is *our own* RFQ to a supplier. E011 is a
supplier's price list. E005's workbook is named `RevB` and hides a sheet `Rev A (old)` with the
superseded rows; a reader that loads every sheet quotes both revisions, and a model that sees the
filename calls it a revision although no earlier email exists. Meanwhile the real requests hide:
the spec is in a merged cell two rows above (`ALL FITTINGS BW SCH 40 A234 WPB UNLESS NOTED`) with
one row overriding it to SCH 80, or on a phone photo of a nameplate. So most of the accuracy came
from *ingest* — marking hidden, struck and merged structure faithfully — not from the prompt.

## Something in the brief or README you disagree with

The README allows `needs_review` only on `rfq`, `rfq_revision` and `purchase_order`. But the
brief's own costliest failure is "a customer who never hears back", and some non-requests need a
person: a bounce of *our quote to a customer*, a "we went with someone else" that should close a
quote, an out-of-office from the buyer on a job with a deadline. Under the contract those come back
`auto_reply` / `other` with nothing to act on. **What I did:** kept the contract in
`parsed.json` (guards enforce it, because the grader scores it), and made the model state a
one-line reason for every classification; it is in `parsed.report.json` and the viewer, so a
person can see *why* something was set aside. In production I'd add a separate `attention`
field rather than overload `needs_review`. Not built.

## Predicted held-out numbers, and two kinds of email I expect to get wrong

Dev is 100% partly because I looked at it; the stress inbox (unseen until written) is the fairer
guide. For ~40 held-out emails: **silent losses 0** (every request with no lines is flagged by
code), **hallucinated lines 2–5**, **lost/phantom 1–2**, classification **~90%**, line recall
**~90%**, precision **~95%**, qty **~95%**, uom/source **~97%**, context carried **~85%**,
links mostly right, needs_review P/R **~80% / ~90%** (I flag on doubt).

1. **Photos and scans of handwritten lists.** The reading is all we have, so a misread `6`/`8`,
   a missed line or a `raw` that pairs badly with the key's reading costs qty and recall. Code
   cannot check a picture; only the review flag helps, and the model flags too rarely.
2. **Terse revisions against an earlier email that isn't in the inbox.** "Kill #5" needs the
   original list; when it's only partly quoted, the "complete order as now intended" will be
   incomplete or wrong. A second hazard on the same boundary: Rev B / resend / "also add" as new
   rfq vs revision — I chose "revision only if it changes an *earlier email*", which held-out
   may draw differently.

## How this runs for real

**Built today:** content-addressed cache (sha256 of model + effort + prompt + full content incl.
image bytes, atomic writes), 3 retries with timeout, a circuit breaker after 3 consecutive
failures, a per-email header fallback, a 30-page cap, per-email isolation, and a per-email
cost/token report. **Not built:** the webhook, a queue, a database.

- **3,000 emails a day.** Measured cost is $0.0072 per email (3,940 tokens incl. reasoning) →
  about **$22/day**. Median latency 4.9 s, worst 24 s; 3,000/day is ~2 per minute, so one worker
  keeps up. Design: the webhook stores the raw `.eml` and enqueues its hash; workers run
  `process_one`; thread linking moves from "whole inbox" to a lookup in a store of past emails by
  Message-ID, subject and domain.
- **Provider down for two hours.** Today: retries, then the breaker trips and every remaining
  email gets the fallback — headers decide auto_reply/bulk, everything else becomes
  `rfq` + `needs_review` ("not read automatically"). Fallbacks are not cached, so the next run
  calls the model for exactly those emails. For real I'd hold them in the queue with backoff
  for a while before handing them to people, with an alert — 250 emails to a person is worse
  than a 2-hour delay.
- **A 300-page PDF.** Today: the first 30 pages are read and the email is flagged
  "only the first 30 of 300 pages read" (stress S18). Next: split into page windows, one call each,
  merge lines, and still flag for review — a 300-page document is usually a spec book, not a list.
- **The same email delivered twice.** Built two ways: the second delivery hashes to the same
  cache key, so it costs nothing, and `threads` marks the later one `duplicate_of` the first
  (stress S13 is a byte copy). For real, dedupe on Message-ID + body hash at the webhook before
  any work.

## Known broken / next

- `raw` for pictures varies between runs (E023's nameplate); the cache makes it stable for
  replay, not right.
- Thread linking only sees the current inbox; there's no memory of last week.
- The optional matcher is not built.
- Next: page-window chunking, an `attention` field, and the matcher design (`size`/`spec`/
  `material`/`ends` must agree exactly; the model may propose, never decide).
