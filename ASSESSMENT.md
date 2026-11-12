# The RFQ Inbox — a four-day problem

> Read *The situation*, then open the twenty-four dev emails, then come back for the rest. This
> document is short on purpose: most of what matters here is *your* judgment, not ours.

## The situation

You have just joined **Ridgeline Industrial Supply**, a distributor of pipe, fittings, flanges,
valves and bolting. Ridgeline's inside sales reps spend most of their day on one inbox:
customers asking for prices. On a good day that is a tidy list in the body of an email. Most
days it's a spreadsheet with "ALL PIPE SCH 40" in a merged cell above the rows that need it, a
phone photo of a list a foreman wrote on a clipboard, a scan of a scan, a reply that says
"make #3 twelve and kill #5" to an email from last week, a supplier's quote that looks exactly
like a customer's request, and a bounce notification with somebody's whole order attached.

Every one of those has to become a clean list of what the customer wants — or a flag saying a
human needs to look — before anyone can quote it. A missed email is a customer who never
hears back. A line read wrong is a wrong quote.

**Your job: build the thing that reads this inbox.** For every email, decide what kind of email
it is, and pull out exactly the items being asked for, each one self-contained — with the
schedule, material or rating that the customer wrote somewhere *other* than on that line.

You will need a model to do it — several of these emails are photos and scans, and most of
the rest hide their meaning in headings, notes and threads. Calling a model is the easy part.
What we are assessing is **how you put it to work**: what it decides and what your code checks,
how you know it's right, what it costs, and what happens when it's wrong, slow, or down.

## What you are given

```
data/
  README.md            the contract: the command, the output format, how we grade — read it in full
  eml_to_json.py       optional helper: .eml → JSON + the attachment files
  check_format.py      validates your output's shape (it scores nothing)
  catalog.csv          what Ridgeline stocks — for the optional matcher
  dev/
    emails/*.eml       24 inbox emails
    answer_key.json    what a careful rep made of each one — your ground truth
```

There is also a **held-out inbox** of ~40 emails from the same company — the same kinds of
customers, documents and surprises, none of the same strings, and a few situations dev doesn't
show you. We run your submission against it. Work that passes dev by fitting it will not
survive that; work built from understanding *why* dev is hard will. All data is synthetic.

## What to build

One command, exactly as `data/README.md` defines it:

```
parse --emails <dir> --out parsed.json
```

Per email: a **classification** (seven kinds), the **line items** (verbatim text,
self-contained description, quantity, unit, where it came from), and a **needs-review** flag
for when you can't — or shouldn't — answer alone.

**Build it like it will run on the real inbox tomorrow.** Four properties are required, and we
test each of them:

1. **Replayable.** On the dev inbox it runs with **no API key** — record or cache model
   responses and commit them.
2. **Idempotent and cheap to re-run.** Running it twice on the same inbox makes no new model
   calls the second time.
3. **One bad email never takes down the run.** Some attachments are broken on purpose; some
   things are unreadable for anyone. Those come back flagged, and the run carries on.
4. **You know what it cost.** Tell us the model calls, tokens and dollars per email on dev.

## Optional — if you have time left

Never required, never penalised when skipped. If you build the matcher we run its numbers too,
but both parts are weighed in the interview, not in your score. Both are good places to show
judgment the core can't.

- **The matcher, designed (and built if you like).** Each extracted line eventually has to
  become a catalog part — or a decision that Ridgeline doesn't stock it. The catalog does not
  carry everything customers ask for, and the closest part is often one attribute off (right
  size, wrong schedule). Write down how you'd decide: what you normalise, which attributes
  must agree, how you find candidates, when the answer is **not in catalog** and when it is
  "ask the customer", where a model helps and what it must never decide on its own, and how
  you'd measure it. A page in `DECISIONS.md` or a separate `MATCHER.md`. If you build it, add
  the optional `sku` / `match` fields `data/README.md` defines — `dev/answer_key.json` has
  the answers for dev.
- **A small web app** that walks one email through your system: the email and its
  attachments, the lines you extracted and where each came from, and — if you built the
  matcher — each line's catalog match or not-in-catalog decision, which a rep can accept or
  overturn. Clarity is worth something; polish is worth nothing.

## What to hand in

A git repository — zipped, on Google Drive, or on GitHub (see *Logistics*) — containing:

1. **The code**, with `RUNNING.md`: one setup step from a fresh clone or unzip to a working
   `parse`, the model(s) you use, and what a run costs.
2. **`parsed.json` for dev**, regenerated by your command.
3. **`DECISIONS.md`** — at most two pages. These four headed sections are **required**:
   - *Something in the data that surprised you* — one paragraph.
   - *Something in this brief or the README you disagree with, and what you did about it.*
     We mean it; the README is not gospel.
   - *Your predicted held-out numbers, and the two kinds of email you expect to get wrong.*
     We will compare.
   - *How this runs for real.* Three thousand emails a day arrive by webhook. The model
     provider is down for two hours. A customer sends a 300-page PDF. The same email is
     delivered twice. What happens in your design — and which of those did you actually
     build?

   Plus whatever else belongs in a design memo: where the model decides and where code
   decides, how you measured it, what you rejected, what you know is broken, what's next.
   Be specific to *this* data.
4. **`AI_USAGE.md`** — at most one page, how you used AI coding agents (Claude Code, Codex,
   Cursor, Copilot — whatever you use for real work), with these headings:
   - *Setup* — which tools and models; instruction files, subagents, skills, MCP servers.
   - *How you worked* — one paragraph: how you split the work, planned, tested and reviewed.
   - *Three moments* — the agent was wrong and how you caught it; you overruled it and why;
     it saved you real time. Each points at a commit hash **and** the session it happened in.
5. **`ai-sessions/`** — the raw session logs of your coding agents for this project, unedited
   except for redacted secrets: Claude Code — the `.jsonl` files under
   `~/.claude/projects/<your-project-folder>/` (or `/export` from each session); Codex CLI —
   `~/.codex/sessions/`; Cursor, Copilot and others — export or copy each chat. Include every
   instruction you gave the agents (CLAUDE.md, AGENTS.md, rules files, subagent or skill
   definitions, saved prompts). We read **how you directed the tools** — what you asked for,
   what you checked, what you threw away — not how much of the code you typed. If your tool
   can't export, say so in `AI_USAGE.md` and bring the history to the interview. **Before you
   send:** search the folder for `sk-` and redact any key (agents often echo `.env` files into
   tool output), and include only this project's instruction files — nothing personal or
   belonging to an employer.
6. **A screen recording, five minutes or less** — one email end to end through your system,
   the two decisions you'd defend first, and one moment from your agent sessions where you
   changed the agent's course. Production value is worth nothing. Link it in
   `RUNNING.md` or commit it (keep the repo under 50 MB).
7. **Commit history as it happened** — a zip must include the `.git` folder; a GitHub repo
   carries it already. Don't squash or tidy it for us.

## Rules

- **Time.** The package arrives **Thursday 12 November**; submission is due **Sunday
  15 November, 23:59 CST** — and the moment you're done is the moment to send it. We expect **8–12 hours of work** and grade for that budget. Past 16
  hours: stop and write. Tell us in `DECISIONS.md` roughly how long you spent. If this window
  doesn't work for you, say so and we'll move it.
- **Stack.** Anything that runs for us: we run submissions on **Windows — natively, in Docker,
  or under WSL** — with Python 3.11+, Node 20 and Docker available. If your stack assumes
  Linux, ship a Dockerfile; either way, say what we need in `RUNNING.md`.
- **Model access.** The email this package came with includes an **OpenAI API key with a USD
  20 cap**. It is for your `parse` command — run your coding agents on your own accounts. Read
  it from `OPENAI_API_KEY` and name the model(s) in `RUNNING.md`. We run the held-out inbox
  **with an OpenAI key only**, so your default path must use OpenAI; another provider is
  welcome as an option you switch on, at your own cost. If the key is missing or a call fails,
  your system degrades the way you designed it to — it never crashes.
- **Don't fit the dev set.** The held-out inbox has the same *kinds* of surprises, not the same
  strings.
- **No outbound side effects.** Nothing in your submission sends email or calls anything except
  the model provider.

## How we read it

We run `parse` on the held-out inbox **before we read a word you wrote** — once with our key,
then again with the key removed (property 2 says that second run should match, from your
cache). Then `DECISIONS.md`, the recording, the code, the commits, `AI_USAGE.md` and your
agent sessions. Then a 60-minute conversation: you show us the two emails you find most
interesting, predict what your system will do with an email you've never seen before we run
it, defend two decisions, and change something live — with whatever tools you normally use.
We watch where you look first and what test you name.

Two pages that name three real properties of this data beat a feature you rushed.

## Logistics

- **Submit:** reply to the email this package came with, carrying **one** of: a GitHub
  repository link (if it's private, ask in your reply and we'll tell you who to add); a Google
  Drive link to the zip (project folder including `.git`, fixtures committed); or the zip
  attached — but mail servers often reject zips containing scripts and cap attachments around
  25 MB, so prefer a link. Code and recording both.
- **Your code stays yours.** We use it only to evaluate you and delete it when the process ends.
- **Questions:** reply on the same email thread. We answer **08:00–17:00 CST every day,
  weekend included**, usually within a few hours. We answer questions about the data and the
  format; we won't tell you what to build.