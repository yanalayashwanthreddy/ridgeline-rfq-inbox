# RUNNING

Windows, native Python 3.11+ (developed and tested on 3.12.10). No Docker needed.

## Setup (one step, from a fresh clone or unzip)

```powershell
python -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt
```

(macOS/Linux/WSL: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`)

## Run

```powershell
.venv\Scripts\python -m rfq parse --emails data\dev\emails --out parsed.json
```

- **With a key:** set `OPENAI_API_KEY` in the environment (or put `OPENAI_API_KEY=...` in a
  git-ignored `.env` in the repo root). New emails call the model; every answer is saved to
  `cache\`.
- **Without a key:** answers are replayed from `cache\`. On dev this reproduces the committed
  `parsed.json` byte for byte, with zero calls (checked from a fresh clone). An email with no
  cached answer is not dropped: it is classified from its headers and flagged `needs_review`.
- **A second run** on the same inbox makes no new model calls (the cache key is a sha256 of the
  model, effort, prompt and the email's full content).
- Every run also writes `parsed.report.json`: per-email calls, tokens, cost, cache hits,
  warnings and the model's one-line reason for its classification, plus totals.

Format check: `.venv\Scripts\python data\check_format.py parsed.json --emails data\dev\emails`
(24 emails, 0 problems).

**Check everything at once** (about 15 s, offline, no key needed):

```powershell
.venv\Scripts\python scripts\verify.py
```

It clones the last commit into a temp folder and checks it the way a grader would:
- the keyless replay is byte-identical to `parsed.json`, and a second run makes 0 calls;
- format check;
- dev and stress scores: 0 silent losses, 0 hallucinated lines, 0 confident wrong matches;
- the tests and a generated matcher test;
- no API key in any tracked file, and the repo is under 50 MB.

Exit code 0 means everything passed.

## Model

`gpt-5.4-mini-2026-03-17` (a pinned snapshot, so a re-run can't drift), reasoning effort
`medium`, OpenAI Responses API with a strict JSON schema. One call per email, plus at most one
retry when code finds a numbered table row that no extracted line covers (1 of 24 dev emails).
Override with `--model` / `--effort` or `RFQ_MODEL` / `RFQ_EFFORT`. Other options: `--offline`
(never call the model), `--workers N` (default 4), `--cache DIR`.

**Catalog matching** (the optional matcher, [MATCHER.md](MATCHER.md)) runs inside `parse` against
`data\catalog.csv` and adds `sku` / `match` / `match_reason` to every line. It is code only:
no model calls, $0, and the keyless replay includes it. `--catalog FILE` to point elsewhere,
`--no-match` to skip.

## Cost (dev inbox, 24 emails, from `parsed.report.json`)

| | |
|---|---|
| model calls | 25 (24 + 1 coverage retry) |
| tokens per email | ~3,940 on average (input + output, incl. reasoning) |
| cost per email | **$0.0072** average |
| full inbox, uncached | **$0.17** |
| re-run / replay | $0.00 |
| latency per call | median 4.9 s, worst 24 s |

Prices used ($ per 1M tokens, `rfq/llm.py`): input 0.75, cached input 0.075, output 4.50.

## Other tools

```powershell
.venv\Scripts\python scripts\score.py parsed.json data\dev\answer_key.json      # score vs the key
.venv\Scripts\python -m rfq.viewer --emails data\dev\emails --parsed parsed.json --key data\dev\answer_key.json
                                                                   # local web view, one email end to end
.venv\Scripts\python -m pip install -r requirements-dev.txt; .venv\Scripts\python -m pytest -q   # 68 tests
.venv\Scripts\python -m rfq parse --emails tests\stress\emails --cache tests\stress\cache --out tests\stress\parsed.json
.venv\Scripts\python scripts\score.py tests\stress\parsed.json tests\stress\answer_key.json   # stress inbox (22 emails)
```

The viewer listens on `localhost:8765` and makes no outbound calls. Its only write is a rep's
accept/overturn of a catalog match, saved to `parsed.rep_decisions.json` (git-ignored), never
into `parsed.json`.

## Screen recording

Link: _to be added_
