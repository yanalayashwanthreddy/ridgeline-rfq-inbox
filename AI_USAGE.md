# AI_USAGE

## Setup

- **Claude Code** in the VS Code extension, model **Claude Opus 5.5**, in one long session
  (`ai-sessions/92594963-78dc-4559-ba5d-13415136fa1e.jsonl`, continued across context
  compactions). The `subagents/` folder holds the one subagent: Claude Code's built-in
  **code-review** skill, which I ran over the helper scripts.
- **No CLAUDE.md, custom subagents or saved prompts.** The only standing instruction is Claude
  Code's auto-memory file for this project (`ai-sessions/memory/`). It records the deadline, that I
  wanted the *why* explained at each step, and that the API key must never be printed.
- **Tools:**
  - the agent's shell, file and web-search tools;
  - Docker, for a throwaway Python sandbox before Python was installed;
  - the claude.ai Artifact tool, for a hosted quote-desk page.

  No other MCP servers were used.
- **The parser's model** (OpenAI `gpt-5.4-mini`) ran on the assignment key. The agent never saw
  the key: it lives in a git-ignored `.env`.

## How you worked

I used the agent as a pair. It proposed each step and explained why, and I made the calls: the
git identity, the key in `.env`, "model extracts, code checks" with one call per email, choosing
the model by a measured bake-off, and what to work on next. Each time it laid out options, I
chose the order.

Work went in small commits. A scorer implementing the README's grading came early, and every
change was judged by its numbers. To avoid fitting dev, I approved a stress inbox whose answers
were written before their first run (two adjusted later, both recorded), and later chose a generated test for the matcher. I asked for a
code review of the helper scripts. It found real problems, and they were fixed (`1d06114`): the
session-log export was rewriting the word "task-notification" and could delete `ai-sessions/`.
`scripts/verify.py` re-checks the submission from a fresh clone, and with `--linux` on Python 3.11
in Docker. That run found code that would not start on 3.11 and a cache miss that only happened
on Linux; both were fixed (`d50b9ec`). I reviewed results in a local
viewer that walks one email through the system.

## Three moments

1. **The agent was wrong, and a test it didn't control caught it.** The agent's matcher
   (`b2e3d3c`) scored a perfect 210/210 on dev. Of the next steps it offered, I chose to run a
   test generated from the whole catalog first, with answers taken from the catalog table rather
   than from the matcher's logic. It found **113 confident wrong matches**, the costliest failure
   in the brief:
   - stainless grades were matched too loosely (a valve asked for in WP316L got the F316L valve);
   - `A-105 class 150` was read as class 105;
   - WNRF/SORF/3M weren't known.

   They were fixed (`b1f695d`), and now 0 come back in ~19,700 generated lines. Lesson: a perfect
   dev score was measuring how well we had fitted dev. Session `92594963`.
2. **I overruled it.** The agent's first commit used the git identity already on the machine,
   which was my work email. The brief says nothing belonging to an employer may go in, so I
   chose a personal identity, and the agent re-authored the history (`7d55037` is the first commit
   under it). Session `92594963`.
3. **It saved real time.** The stress inbox, with its generator and expected answers
   (`85a86ef`), took minutes, not an evening. It found two bugs that dev scored as perfect: a
   repeat of a past order classified as a revision, and a PO line silently dropped. That led to
   the coverage check. Session `92594963`.
