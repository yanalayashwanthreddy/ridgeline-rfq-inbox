"""What the model sees: the instructions, one email laid out as text + images, and the schema.

The instructions state *principles* (scope of a heading, overrides beat defaults, quoted history
is not a request) rather than dev-set strings: the held-out inbox has the same kinds of
surprises with none of the same text.

Bump PROMPT_VERSION whenever the instructions or the layout change: it is part of the cache key.
"""

from __future__ import annotations

from .ingest import STRIKE_L, Email

PROMPT_VERSION = "v1"

CLASSES = ["rfq", "rfq_revision", "purchase_order", "vendor_quote", "status_inquiry", "auto_reply", "other"]
REQUEST_CLASSES = {"rfq", "rfq_revision", "purchase_order"}
UOMS = ["EA", "FT", "M", "IN", "BX", "PK", "PR", "DZ", "RL", "LB", "SET", "GAL", "BAG", "CS"]

MAX_TEXT_CHARS = 150_000   # ~40k tokens of text per email; beyond that we truncate and flag
MAX_IMAGES = 20            # pictures per email; beyond that we truncate and flag

INSTRUCTIONS = f"""\
You read emails arriving in the RFQ inbox of Ridgeline Industrial Supply (rfq@ridgeline-supply.example), \
a distributor of pipe, fittings, flanges, valves, gaskets, bolting and related industrial supplies. \
Ridgeline's own staff write from @ridgeline-supply.example. Ridgeline's quotes are numbered Q-26-....

For ONE email you decide what the current message does, and you list exactly the items being \
asked for, each one self-contained.

# How the email is shown to you
- Headers, then the body, then each attachment converted to text or shown as images.
- Spreadsheet rows appear as "R12: a | b | c". "R12:" is only the row number, not content.
- [HIDDEN SHEET] / [HIDDEN ROW] were not visible to the sender's reader: old revisions or \
working data. Never take items from them.
- Text wrapped in {STRIKE_L} {STRIKE_L} is struck through (crossed out): it is NOT requested. In \
photos and scans, a line with a line drawn through it is crossed out too.
- A note such as "password-protected" or "could not open" means nobody can read that attachment.

# classification: exactly one. Judge what the CURRENT message does, not the subject line and \
not the quoted history.
- rfq: asks Ridgeline to price or check availability of items.
- rfq_revision: changes a request the same customer made earlier in the thread (quantities, \
additions, removals).
- purchase_order: commits to buy: an attached PO, "please process", "go ahead with items 1-3", \
"release the order".
- vendor_quote: a supplier quoting or answering Ridgeline (typically replying to a Ridgeline \
employee's request for pricing). It can look exactly like an item list; it is still a vendor_quote.
- status_inquiry: asks about an existing quote or order (update? ETA? tracking? did you get my \
RFQ?) without asking for new items.
- auto_reply: machine-generated: out-of-office, delivery failure / bounce, read receipt, ticket \
acknowledgement. A bounce is an auto_reply even when the bounced message contains items.
- other: anything else: marketing, promotions and price sheets sent to many recipients, \
invoices, remittances, statements, job applications, internal chatter, "we went with someone \
else", thank-you notes.
When a Ridgeline employee forwards a customer's email, classify it as the customer's email, and \
read both the forwarded content and the employee's own note.

# line_items: only for rfq, rfq_revision and purchase_order; otherwise return []
Counts: what the customer asks for in the current message, its attachments and pasted pictures, \
and customer content forwarded by a Ridgeline employee.
Does not count: items in quoted earlier messages (they were handled when they arrived), \
headings, notes, totals, prices, signatures, logos, crossed-out lines, hidden sheets or rows, \
superseded revisions of a document.
Two cases where quoted text DOES supply the lines:
- rfq_revision: return the COMPLETE order as the customer now intends it: every line of the \
earlier request that still stands (taken from the quoted text), with the changes applied, \
removed lines left out, and added lines included. For a changed line, raw is the new text if \
the customer wrote one, otherwise the original line.
- purchase_order accepting items by reference ("go ahead with items 1, 2 and 4" of a quote \
quoted below): return exactly the accepted items, taken from the quoted quote.

Each line item:
- raw: the line's text exactly as it appears where you found it. A table row is its cells \
joined with " | " in order, as shown (without the "R12:" label). For a picture or scan, your \
faithful transcription of that line, ditto marks included. Do not correct or expand it.
- description: the item self-contained, as a sales rep would type it into a quote without \
re-reading the email: the line's own words plus every heading, section title, note, column \
header, sheet name, form field (Material:, Rating:, Schedule:), subject line or earlier line \
that applies to it, and nothing that does not apply to it.
  * Scope: a note under a section heading applies only to that section. "All pipe SCH 40" \
applies to pipe, not to the fittings or valves.
  * Overrides beat defaults: under "ALL FITTINGS SCH 40 UNLESS NOTED", a line that says SCH 80 \
is SCH 80 and must not mention SCH 40.
  * Items the customer adds in the email body are not covered by an attached form's header \
fields unless the body says so.
  * Resolve references: "same size", ditto marks, "same as above" -> write out what they mean.
  * Name the item type when the line only implies it (a row in a STUD BOLTS section is a stud \
bolt; "WN 150# RF" in a flange list is a weld neck flange).
  * For a photo of a nameplate or an existing part, describe the part from what the picture \
shows (type, size, rating/class, body material, trim, ends, maker and model).
  * Never invent a spec the email does not give.
- qty: the quantity the customer asked for, as stated. Spelled-out numbers count ("twelve" = \
12). Vague amounts ("a couple", "some", "enough for the job") -> null. A size, length, \
schedule, rating, part number, line number or price is never a quantity. When the item is in a \
picture and the quantity is in the text ("quote 3 of these"), use that quantity.
- uom: the unit the customer stated, one of {" ".join(UOMS)}, or null when no unit is stated. \
joints / jts / lengths / pieces / pcs / ea / each -> EA; feet / ft -> FT; sets -> SET; rolls -> \
RL; boxes -> BX; pairs -> PR. Never convert (200 ft stays 200 FT; 12 jts stays 12 EA).
- source: "body", or the exact filename of the attachment or pasted image the line came from. \
Lines from a forwarded customer email's text are "body".

# needs_review: only for rfq, rfq_revision and purchase_order (false for everything else)
true when the email asks for something that cannot be fully read or resolved: the attachment \
it points at is missing, unreadable or password-protected, or is not the list it claims to be \
(e.g. "BOM attached" but the only attachment is a logo); part of a list is illegible; or items \
are identified only by something not in the email ("same as our last order", "the usual", \
"per the drawing" with no drawing). Then give a short review_reason a sales rep can act on, \
and still return every line you could read.
false otherwise. Vague quantities or missing delivery details are not a reason to flag.
"""


def build_input(em: Email) -> tuple[list[dict], dict, list[str]]:
    """Return (content parts for the user message, JSON schema, truncation notes)."""
    truncated: list[str] = []
    head = [
        f"From: {em.sender}",
        f"To: {em.to}",
        *([f"Cc: {em.cc}"] if em.cc else []),
        f"Date: {em.date}",
        f"Subject: {em.subject}",
        *([f"Auto-Submitted: {em.auto_submitted}"] if em.auto_submitted else []),
        *([f"Precedence: {em.precedence}"] if em.precedence else []),
    ]
    budget = MAX_TEXT_CHARS
    body = em.body.strip() or "(empty body)"
    if len(body) > budget // 2:
        body = body[: budget // 2] + "\n[... body truncated]"
        truncated.append("email body was too long and was truncated")
    budget -= len(body)
    content: list[dict] = [{"type": "input_text", "text": "\n".join(head) + "\n\n=== BODY ===\n" + body}]

    n_images = 0
    names = ["body"]
    for i, p in enumerate(em.parts, 1):
        names.append(p.filename)
        meta = f"=== ATTACHMENT {i}: {p.filename!r} ({p.mime}"
        meta += ", pasted inline in the body" if p.inline else ""
        meta += f", {p.kind})"
        notes = "".join(f"\nnote: {n}" for n in p.notes)
        text = p.text
        if len(text) > budget:
            text = text[: max(budget, 0)] + "\n[... truncated]"
            truncated.append(f"{p.filename} was too long and was truncated")
        budget -= len(text)
        content.append({"type": "input_text", "text": f"{meta}{notes}\n{text}".rstrip()})
        for im in p.images:
            if n_images >= MAX_IMAGES:
                truncated.append(f"more than {MAX_IMAGES} pictures; {im.label} and later were not read")
                break
            n_images += 1
            content.append({"type": "input_text", "text": f"[picture: {im.label}]"})
            content.append({"type": "input_image", "image_url": _data_url(im.mime, im.data), "detail": "high"})
    return content, schema(names), sorted(set(truncated))


def _data_url(mime: str, data: bytes) -> str:
    import base64

    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def schema(sources: list[str]) -> dict:
    """Strict JSON schema. `source` is an enum of this email's real parts, so the model cannot
    name a file that doesn't exist."""
    sources = list(dict.fromkeys(sources))
    nullable_str = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["classification_reason", "classification", "needs_review", "review_reason", "line_items"],
        "properties": {
            "classification_reason": {"type": "string", "description": "one sentence: what the current message does"},
            "classification": {"type": "string", "enum": CLASSES},
            "needs_review": {"type": "boolean"},
            "review_reason": nullable_str,
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["raw", "description", "qty", "uom", "source"],
                    "properties": {
                        "raw": {"type": "string"},
                        "description": {"type": "string"},
                        "qty": {"type": ["number", "null"]},
                        "uom": {"type": ["string", "null"], "enum": [*UOMS, None]},
                        "source": {"type": "string", "enum": sources},
                    },
                },
            },
        },
    }
