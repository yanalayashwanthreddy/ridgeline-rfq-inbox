# DECISIONS

**Time spent:** about 8 hours.

## Who decides what

`ingest` (code) → **one model call per email** (`gpt-5.4-mini-2026-03-17`, effort medium, strict
JSON schema) → `guards` (code) → `threads` (code) → `matcher` (code, [MATCHER.md](MATCHER.md)).

- **Code reads the files**, keeping the structure that carries meaning:
  - hidden sheets and rows, merged headings and struck cells (xlsx/xls);
  - tracked deletions (docx);
  - PDF rows rebuilt from word positions, with scans sent as images;
  - zips and Outlook `.msg` unpacked;
  - photos turned upright.

  A broken part becomes `unreadable` with a reason. It never raises.
- **The model judges.** It decides the class, which lines are requests, and each line's
  self-contained description (heading scope, overrides beat defaults, quoted history is not a
  request). The prompt states principles; its one example is invented, not a dev row.
- **Code checks every fact it can.**
  - Struck and hidden lines are dropped, whatever the model said.
  - Non-requests carry no lines.
  - A request with no lines, or an unreadable part, is `needs_review`.
  - `raw` is snapped to its source row, because the model's copy varies run to run.
  - A numbered row no line covers gets one more call naming it; that caught a dropped PO line
    in E015.
  - Lines read from a picture get a second, independent reading, and a disagreement is flagged.

**Measured, not guessed.** A scorer implements the README's grading. The 25-email **stress
inbox** holds situations dev lacks, each written with its answer **before** its first run: tracked
changes, Spanish, "make #3 twelve and kill #5", a redelivery, a 35-page PDF, a BOM in a zip, an
iPhone HEIC photo, a handwritten list. Two answers were adjusted later, both recorded with them.

| | classes | lines recall · precision | silent | hallucinated | context | review P/R |
|---|---|---|---|---|---|---|
| dev | 24/24 | 210/210 · 210/210 | 0 | 0 | 172/173 | 100 / 100% |
| stress | 25/25 | 148/148 · 148/148 | 0 | 0 | 147/148 | 100 / 100% |

`scripts/verify.py --linux` re-checks this from a fresh clone, on Windows and on Python 3.11 /
Linux. That run caught 3.12-only syntax that wouldn't start on 3.11, and an OS-dependent MIME type
that broke one cache key on Linux.

**Rejected:**
- gpt-5.5: 4.6× the cost, and *worse* on context (it "corrected" `A53B` to `A53 Grade B`).
- Two calls per email: double the cost for the same reading.
- `.doc`/`.msg` readers, at first, as untestable. They were built once real Apache POI files
  gave fixtures, checked against antiword and extract-msg. A `.doc`'s strikethrough isn't
  visible, so those emails are still flagged.

## Something in the data that surprised you

The most request-shaped text in the inbox is usually not a request.
- E013's bounce carries a perfect numbered list of valves: it is *our own* RFQ to a supplier.
- E011 is a supplier's price list.
- E005's `RevB` workbook hides a sheet `Rev A (old)`. Load every sheet and you quote both
  revisions; trust the filename and you call it a revision with no earlier email.

The real requests hide: in a merged cell two rows up (`ALL FITTINGS BW SCH 40 A234 WPB UNLESS
NOTED`, one row overriding to SCH 80), or on a nameplate photo. Most accuracy came from *ingest*
marking that structure, not from the prompt.

And **dev agrees with you too easily.** The matcher scored 210/210 on dev. A test generated from
the catalog, with answers from the catalog table rather than my logic, found **113 confident wrong
matches** (grades matched too loosely, `A-105 class 150` read as class 105).

## Something in the brief or README you disagree with

The README allows `needs_review` only on requests. But the brief's costliest failure is "a
customer who never hears back", and some non-requests need a person: our quote bouncing, a
customer asking where their quote is, a lost deal to close.

**What I did:** kept the contract exactly (it is scored) and added a separate field, `attention`.
It's a one-line action decided by code from the class plus evidence:
- "our email to sales@… ('Quote request…') was not delivered: fix the address and resend";
- "customer is waiting for an answer about Q-26-04417";
- "close Q-26-04512 as lost".

Marketing and out-of-office get null, because an action list that cries wolf stops being read. It
matched what I wrote down beforehand on all 10 non-requests, and `check_format.py` accepts it.

## Predicted held-out numbers, and two kinds of email I expect to get wrong

Stress, not dev, is the guide. For ~40 held-out emails:

| silent | halluc. | lost/phantom | classes | recall / precision | qty · uom · source | context | review P/R | matcher wrong · NIC |
|---|---|---|---|---|---|---|---|---|
| **0** | **2–5** | **1–2** | **~90%** | **~90 / 95%** | **~95 · 97 · 97%** | **~85%** | **~80 / 90%** | **0–2 · ~85%** |

Silent is 0 because code flags any request with no lines. Review recall beats precision because I
flag on doubt.

1. **Photos and scans.** Two readings are compared, and a different count or quantity flags the
   line ("read as 6 and as 8"). What remains is a misreading both readings share, and a picture
   `raw` that pairs badly with the key's own reading.
2. **The rfq/revision boundary.** A "kill #5" whose original is neither in the inbox nor quoted
   is flagged; a quoted original is rebuilt (stress S22/S23). The judgement remains: is Rev B /
   a resend / "also add" a new rfq or a revision? I chose "a revision changes an *earlier email*".

## How this runs for real

**Built:** two ways to drive one pipeline. `parse` takes an inbox at a time (the assignment's
command). `serve` ([rfq/service.py](rfq/service.py)) is the production shape: the webhook's
stand-in is an intake folder, and a SQLite queue tracks each email (queued -> done, or retry ->
dead). History is kept for thread linking. The dev inbox through `serve` gives a `parsed.json`
byte-identical to `parse`'s. Also built: a content-addressed cache, a circuit breaker, windowed
long PDFs, a per-email cost report, and CI ([.github/workflows/ci.yml](.github/workflows/ci.yml):
tests and `verify.py` on Ubuntu and Windows, Python 3.11 and 3.12). **Not built:** the HTTP
endpoint itself (any webhook that writes a file into the intake folder works), and leases for
several workers sharing one queue.

- **3,000 emails a day.** $0.0083 per email measured (about 4,430 tokens), so about **$25/day**.
  Median latency is 4.9 s, so ~2 emails a minute needs one worker.
- **Provider down for two hours.** `serve` holds each email in the queue and retries with backoff
  (1 min ... 2 h, ~4 h in all). Only then does it go to the dead-letter folder, flagged for a
  person, because 250 emails dumped on a desk is worse than a 2-hour delay. (`parse`, run once,
  falls back to headers and flags the rest; the next run re-calls exactly those.)
- **A 300-page PDF.** Built: windows of 30 pages (16 if scanned), one cached call each, ~$0.30.
  Every window also gets page 1 and the previous window's end as context, and a failed window
  flags review. Stress S18: 105/105 rows, where the old 30-page cap lost 15. Not done: skipping
  spec-book pages that hold no items.
- **The same email delivered twice.** Built. `serve` recognises a redelivery (same bytes or the
  same Message-ID) at intake and never processes it twice. A customer who *sends* the request again
  gets `duplicate_of`, found in history across days.

## Known broken / next

- Picture `raw` varies between runs (E023). The cache makes it stable, not right.
- `parse` links threads only within one inbox (`serve` uses its history).
- The matcher reads only our `description`, so an upstream context miss becomes a match miss.
- Next: rep overturns as the matcher's live error rate; `attention` as the desk's queue.
