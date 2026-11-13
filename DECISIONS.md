# DECISIONS

**Time spent:** about 8 hours.

## Who decides what

`ingest` (code) → **one model call per email** (`gpt-5.4-mini-2026-03-17`, effort medium, strict
JSON schema) → `guards` (code) → `threads` (code) → `matcher` (code; see [MATCHER.md](MATCHER.md)).

- **Code reads the files**, keeping the structure that carries meaning: hidden sheets and rows,
  merged headings and struck cells (xlsx/xls); tracked deletions (docx); PDF rows rebuilt from
  word positions, scans sent as images; zips unpacked; photos turned upright. A broken part
  becomes `unreadable` with a reason. It never raises.
- **The model judges.** It decides the class, which lines are requests, and the self-contained
  description (heading scope, overrides beat defaults, quoted history is not a request). The prompt
  states principles; its one example is invented, not a dev row.
- **Code checks every fact it can.**
  - Struck and hidden-only lines are dropped, whatever the model said.
  - Non-requests carry no lines.
  - A request with no lines, or an unreadable part, is always `needs_review`.
  - `source` must be a real part.
  - `raw` is snapped to its unique source row, because the model's copy varies run to run.
  - A numbered row that no line covers gets one more call naming it. That caught a dropped PO line
    in E015 ($0.004).

**Measured, not guessed.** I wrote a scorer implementing the README's grading, and a 22-email
**stress inbox** of situations dev lacks, with answers written **before** each run: tracked
changes, Spanish, an invoice with item lines, "make #3 twelve and kill #5", a byte-identical
redelivery, a 35-page PDF, a BOM in a zip, an iPhone HEIC photo.

| | classes | lines (recall/precision) | silent | hallucinated | context |
|---|---|---|---|---|---|
| dev | 24/24 | 210/210 · 210/210 | 0 | 0 | 172/173 |
| stress | 22/22 | 141/141 · 141/141 | 0 | 0 | 140/141 |

`scripts/verify.py` re-checks all of this, the keyless replay and the hygiene rules from a fresh
clone in about 15 s.

**Rejected:** gpt-5.5 (4.6× the cost, *worse* context: it "corrected" `A53B` to `A53 Grade B`);
two calls per email (double cost, same reading); `.doc`/`.msg` readers (untestable here, so
flagged instead).

## Something in the data that surprised you

The most request-shaped text in the inbox is usually not a request.
- E013's bounce carries a perfect numbered list of valves: it is *our own* RFQ to a supplier.
- E011 is a supplier's price list.
- E005's workbook is named `RevB` and hides a sheet `Rev A (old)`. A reader that loads every sheet
  quotes both revisions, and a model that sees the filename calls it a revision with no earlier
  email.

Meanwhile the real requests hide: in a merged cell two rows up (`ALL FITTINGS BW SCH 40 A234 WPB
UNLESS NOTED`, with one row overriding it to SCH 80), or on a photo of a nameplate. Most of the
accuracy came from *ingest* faithfully marking hidden, struck and merged structure, not from the
prompt.

A second surprise: **dev agrees with you too easily.** The matcher scored 210/210 on dev. A test
generated from the catalog, whose expected answers come from the catalog table and not from my
logic, then found **113 confident wrong matches**: grades matched too loosely, `A-105 class 150`
read as class 105.

## Something in the brief or README you disagree with

The README allows `needs_review` only on `rfq`, `rfq_revision` and `purchase_order`. But the
brief's costliest failure is "a customer who never hears back", and some non-requests need a
person: a bounce of *our quote to a customer*, a "we went with someone else" that should close a
quote, a buyer's out-of-office on a deadline job. Under the contract these come back `auto_reply`
/ `other` with nothing to act on.

**What I did:** kept the contract in `parsed.json` (guards enforce it, because it is scored). The
model also states a one-line reason for every classification, shown in `parsed.report.json` and
the viewer, so a person can see *why* something was set aside. In production I'd add a separate
`attention` field rather than overload `needs_review`. Not built.

## Predicted held-out numbers, and two kinds of email I expect to get wrong

Dev is 100% partly because I looked at it; stress is the fairer guide. For ~40 held-out emails:

| silent | hallucinated | lost/phantom | classes | recall / precision | qty · uom · source | context | review P / R | matcher wrong · NIC recall |
|---|---|---|---|---|---|---|---|---|
| **0** | **2–5** | **1–2** | **~90%** | **~90% / ~95%** | **~95 · 97 · 97%** | **~85%** | **~80 / 90%** | **0–2 · ~85%** |

Silent is 0 because code flags any request that comes back with no lines; review recall beats
precision because I flag on doubt.

1. **Photos and scans of handwritten lists.** The model's reading is all there is. A misread
   `6`/`8`, a missed line or a `raw` that pairs badly costs qty and recall. Code can't check a
   picture, and the model flags too rarely.
2. **Terse revisions of an email that isn't in the inbox.** "Kill #5" needs the original list,
   so the "complete order as now intended" will be incomplete. The same boundary, Rev B / resend
   / "also add", is new rfq or revision? I chose "revision only if it changes an *earlier email*";
   held-out may draw it differently.

## How this runs for real

**Built:**
- a content-addressed cache (sha256 of model, effort, prompt and full content incl. image bytes;
  atomic writes);
- 3 retries with timeouts and a circuit breaker after 3 consecutive failures;
- a header-based fallback per email, and per-email isolation;
- windowed long PDFs;
- a per-email cost and token report.

**Not built:** the webhook, a queue, a database.

- **3,000 emails a day.** $0.0072 per email measured (about 3,940 tokens), so about **$22/day**.
  Median latency is 4.9 s and the worst 24 s; 3,000/day is ~2 a minute, so one worker keeps up.
  Design: the webhook stores the raw `.eml` and enqueues its hash; workers run `process_one`;
  thread linking becomes a lookup in a store of past emails by Message-ID, subject and domain.
- **Provider down for two hours.** After the retries, the breaker trips and the rest get the
  fallback: auto_reply/bulk by header, everything else `rfq` + `needs_review`. Fallbacks aren't
  cached, so the next run re-calls exactly those. For real: hold them in the queue with backoff
  and an alert. 250 emails on a person's desk is worse than a 2-hour delay.
- **A 300-page PDF.** Built. It's read in windows of 30 pages (16 if scanned), one cached call
  each: ~10 calls, ~$0.30. Each window also gets page 1 and the previous window's end as
  context-only, because a heading on page 2 can govern page 40. A failed window flags review
  and keeps what was read; past 300 pages the rest is flagged. Stress S18: 105/105 rows (the
  old 30-page cap lost 15). Not done: a relevance pass to skip spec-book pages that hold no
  items.
- **The same email delivered twice.** Built two ways. The redelivery hashes to the same cache
  key, so it's free, and `threads` marks the later one `duplicate_of` (stress S13 is a byte copy).
  For real, dedupe on Message-ID plus body hash at the webhook.

## Known broken / next

- `raw` read from pictures varies between runs (E023). The cache makes it stable, not right.
- Thread linking only sees the current inbox; there's no memory of last week.
- The matcher reads only our `description`, so a context miss upstream becomes a match miss.
- Next: the `attention` field, and the viewer's rep overturns as the matcher's live error rate.
