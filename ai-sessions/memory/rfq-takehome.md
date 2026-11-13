---
name: rfq-takehome
description: "User is building the \"RFQ Inbox\" take-home (Ridgeline Industrial Supply email parser); due Sun 2026-11-15 23:59 CST"
metadata:
  node_type: memory
  type: project
  originSessionId: 92594963-78dc-4559-ba5d-13415136fa1e
  modified: 2026-11-12T19:33:26.176Z
---

Take-home assignment: build `parse --emails <dir> --out parsed.json` that classifies RFQ-inbox emails and extracts self-contained line items. Package lives at `C:\Users\YashwanthYanala\Downloads\candidate-package 1\candidate-package\` (ASSESSMENT.md, data/README.md, 24 dev .eml + answer_key.json). Must use OpenAI (key via OPENAI_API_KEY, $20 cap), replay from committed cache with no key, never crash per-email.

**Why:** Package arrived Thu 2026-11-12; due Sun 2026-11-15 23:59 CST; 8–12h budget. Graders read the raw Claude Code session logs (ai-sessions/) and want to see how the user *directed* the agent.

**How to apply:** User asked to learn the "what and why" as we go — explain decisions, let them make the calls (their decisions show up in the graded session logs). Never ask them to paste the API key into chat (logs get submitted). KEY LEAK TO REDACT: in session 92594963 (2026-11-12) a .env check accidentally printed the first ~60 chars of the OpenAI key — before submitting ai-sessions/, grep for `sk-` and redact. Key lives in project `.env` (git-ignored); never read/print that file. Machine had no Python/Node installed at start; Docker 29 and git are available.
