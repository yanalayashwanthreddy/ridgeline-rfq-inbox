# AI_USAGE

## Setup

- **Claude Code** in the VS Code extension, model **Claude Opus 5.5**, in one long session
  (`ai-sessions/92594963-78dc-4559-ba5d-13415136fa1e.jsonl`, continued across a context
  compaction).
- **No CLAUDE.md, custom subagents or saved prompts.** The only standing instruction is Claude
  Code's auto-memory file for this project (`ai-sessions/memory/`). It records the deadline, that I
  wanted the *why* explained at each step, and that the API key must never be printed.
- **Tools:** the agent's shell, file and web-search tools; Docker for a throwaway Python sandbox
  before Python was installed; the claude.ai Artifact tool for the hosted quote-desk page.
  No other MCP servers were used for this project.
- **The parser's own model** (OpenAI `gpt-5.4-mini`) ran on the assignment key. The agent never
  saw the key: it lives in a git-ignored `.env`.

## How you worked

I used the agent as a pair: it read the package, proposed the design, and explained each choice
before building it, and I made the calls: the git identity, keeping the key in `.env`, "model
extracts, code checks" with one call per email, and choosing the model by measurement
(I picked `gpt-5.4-mini` at medium after a three-way bake-off on cost against accuracy). Work went in small commits: ingest, then
the pipeline, then prompt versions. A scorer that implements the README's grading was written
early, and every change was judged by its numbers, not by eye. To avoid fitting dev, I asked for a
stress inbox of situations dev doesn't show, with its answers written *before* the system ran on
it. I reviewed results in a local viewer that shows one email end to end, and a hosted
quote-desk page that shows every extracted line.

## Three moments

1. **The agent was wrong, and a check caught it.** To stop Windows from rewriting the data
   files' line endings, the agent wrote a `.gitattributes` (`80f0d99`) in which the `*.json` /
   `*.py` text rules came *after* `data/**` and won, so the package's README and helper scripts
   would still have been converted. A byte-for-byte comparison of the committed data against the original
   package flagged the mismatches, and the rules were reordered (`22d7885`). This was verified
   at 0 mismatches. Lesson: "it's configured" is a claim; a byte check is evidence.
   Session `92594963`.
2. **I overruled it.** The agent's first commit used the git identity already configured on the
   machine, which was my work email. The brief says nothing belonging to an employer may go in,
   so I chose a personal identity. The agent set it repo-locally and re-authored the history
   (`7d55037` is the first commit under the personal identity). Session `92594963`.
3. **It saved real time.** The 19-email stress inbox, with its generator and expected answers
   (`85a86ef`), took minutes rather than an evening. It found two real bugs that dev scored as
   perfect: a repeat of a past order classified as a revision, and a purchase order whose second
   line was silently dropped. That led to the coverage check, which asks the model once more when
   a numbered row is missing. Session `92594963`.
