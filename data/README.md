# data/ — the files, the contract, the grading

```
dev/emails/*.eml       the inbox: 24 RFC-822 emails (E001.eml …) — plain text, HTML, Excel,
                       CSV, Word, text PDFs, scanned PDFs, phone photos, pasted screenshots, threads
dev/answer_key.json    what a careful inside sales rep (ISR) made of each email — your ground truth
check_format.py        validates the shape of your output (it scores nothing)
catalog.csv            what Ridgeline stocks — used only by the OPTIONAL matcher (see the end)
eml_to_json.py         optional: .eml → JSON + the attachment files. Use it, port it, or ignore it —
                       unpacking MIME is not what we grade; reading what is inside the attachments is.
```

A **held-out inbox** from the same company — same kinds of customers, documents and surprises,
none of the same strings — is not shipped; your submission is run against it.

## The command (this is how we run you, so the shape is mandatory)

```
<your command> parse --emails <dir> --out parsed.json
```

It reads every `.eml` in `<dir>` and writes one JSON file. Check it with
`python check_format.py parsed.json --emails <dir>` (exit 0 = shape ok).

## parsed.json

One object keyed by the `.eml` filename stem. **Every email in the directory must appear.**

```json
{
  "E003": {
    "classification": "rfq",
    "revises": null,
    "duplicate_of": null,
    "needs_review": false,
    "review_reason": null,
    "line_items": [
      {
        "raw": "2\" x 21' - 12 lengths",
        "description": "2\" pipe, SCH 40 A53B ERW, threaded & coupled, 21' lengths",
        "qty": 12,
        "uom": "EA",
        "source": "body"
      }
    ]
  }
}
```

### classification — exactly one per email

| value | the **current** message … | line_items |
|---|---|---|
| `rfq` | asks us to price or check availability of items | the items |
| `rfq_revision` | changes a request made earlier in the same thread (quantities, additions, removals) | the **complete** order as now intended; `revises` = the earlier email's id if it is in the inbox, else null |
| `purchase_order` | commits to buy — a PO, "please process", "go ahead with items 1–3" | the items ordered |
| `vendor_quote` | is a supplier quoting or answering **us** | `[]` |
| `status_inquiry` | asks about an existing quote or order (update? ETA? tracking?) | `[]` |
| `auto_reply` | is machine-generated: out-of-office, bounce, receipt | `[]` |
| `other` | anything else: marketing, invoices and remittances, job applications, internal chatter, "we went with someone else" | `[]` |

Classify what the current message *does* — not the subject line, not the quoted history.

### line_items — one per thing the customer is asking for

- **What counts.** Items in the current message, its attachments and pasted pictures, and —
  when one of our own reps forwards a customer's email — in the forwarded content. Not items
  in quoted earlier messages (they were handled when they arrived), not headings, notes,
  totals, prices, signatures, crossed-out lines, or old revisions of a document.
- **`raw`** — the line's text as it appears where you found it (a table row's cells on one
  line; for a picture or scan, your reading of it). We pair your lines with ours on these
  tokens, so keep it close to the source.
- **`description`** — the item **self-contained**, as an ISR would type it into a quote
  without re-reading the email: the line's own words plus every heading, note, column
  header, sheet name, form field, subject line or earlier line that applies to it — and
  nothing that doesn't apply to it. Never invent a spec the email doesn't give.
- **`qty` / `uom`** — as the customer stated it, not converted. A number spelled out is a
  number; a vague amount ("a couple", "enough for the job") is `null`. A size, schedule,
  length, rating, part number or price is never a quantity. `uom` is one of `EA FT M IN BX
  PK PR DZ RL LB SET GAL BAG CS`, or `null` when no unit is stated (we read `null` as `EA`).
- **`source`** — `"body"`, or the filename of the attachment / pasted image the line came from.

### Threads, duplicates, forwards

- A reply that changes an earlier request → `rfq_revision` with the **complete** order as the
  customer now intends it; the earlier email keeps its own lines as they were on arrival.
- The same request sent twice → **the email with the later `Date:` is the duplicate**: same
  classification as the original, `duplicate_of` = the original's id, `line_items: []`.
- Anything **from** `@ridgeline-supply.example` is one of our own reps, usually forwarding a
  customer's email; classify it as the customer's email, and read the rep's own note too.

### needs_review — "a human has to look at this before anyone quotes it"

`true` when the email asks for something you cannot fully read or resolve: the attachment it
points at is missing or can't be opened, or the items themselves are only identified by
something that isn't in the email ("same as our last order, but double it"). Put the reason in `review_reason` (required when `true`) and still
return whatever lines you *could* read. Only for `rfq`, `rfq_revision` and `purchase_order`.

## Optional: matching to the catalog

If you build the optional matcher, add three fields to each line item:

```json
{"raw": "...", "description": "...", "qty": 12, "uom": "EA", "source": "body",
 "sku": "RL-12345", "match": "matched", "match_reason": "2\" 150# RF weld neck A105: one part fits"}
```

- `match` is one of `matched` (exactly one catalog part is what the customer asked for —
  `sku` is it), `not_in_catalog` (we don't stock it — `sku: null`), or `ambiguous` (more than
  one stocked part fits what the customer wrote and they'd have to choose — `sku: null`,
  optional `candidates: [skus]`).
- `match_reason` — one short line a rep can check.
- **Not in catalog is a decision, not a failure.** Ridgeline doesn't stock everything
  customers ask for, and the nearest catalog part is often one attribute away — right size and
  type, wrong schedule, rating or material. Quoting that part is a wrong quote.
- `catalog.csv` columns: `sku, description, category, product, size, spec` (schedule or
  pressure class)`, material, ends, extra, uom`. Descriptions are abbreviated the way an ERP
  prints them.

## World facts

- Ridgeline Industrial Supply sells pipe, fittings, flanges, valves, gaskets, bolting and
  related industrial supplies. The RFQ inbox is `rfq@ridgeline-supply.example`.
- Our quotes are numbered `Q-26-…`. Customers' RFQ and PO numbers are theirs.
- A supplier answering *our* request for pricing is a `vendor_quote`, however much it looks
  like a list of items.

## What we grade

Your `parse` command on the held-out inbox, against our key, **before we read anything you wrote**.

**Headline numbers** — the expensive failures, in this order:
1. **Silent losses** — an email that needed someone to act (it has items, or needs review)
   came back with no lines and no review flag. That customer never hears back.
2. **Hallucinated lines** — lines that aren't requests: quoted history, headings, prices,
   signatures, crossed-out or superseded rows.
3. **Lost and phantom requests** — a request classified as something else (lost), or a
   vendor quote / bounce / newsletter classified as a request (phantom).

Then: classification accuracy, line recall and precision, and on paired lines — `qty`,
`uom`, `source`, and **context carried** (does the `description` mention what the key's
`must_mention` lists, and none of its `must_not_mention`?). Plus `revises` / `duplicate_of`
links and `needs_review` precision/recall.

**Optional matcher**, if present: confident wrong matches (you named a part; the answer was a
different part, *not in catalog*, or *ambiguous*), not-in-catalog recall, and match accuracy.

**`answer_key.json` fields beyond the schema:** `id` (`<email>-<n>`); `qty_tol` (relative
tolerance on qty: `0` exact, `null` = unscored); `must_mention` (phrases the description must
contain; `"a|b"` = either; case, spaces and punctuation ignored, and common spellings of the
same spec count as equal — Sch/Schedule, 150#/150 lb/Class 150, BW/butt weld, threaded/NPT,
SMLS/seamless, galv/galvanized, and the catalog's abbreviations ELL, VLV, FLG, GSKT, SPW, MI,
CS, FP, RED, CPLG); `must_not_mention` (phrases
it must not contain — a default that this line overrides, or a note that belongs to another
section); `sku` / `match` / `candidates` (the catalog answer for the optional matcher).
