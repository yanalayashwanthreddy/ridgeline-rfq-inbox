"""Copy this project's Claude Code session logs into ai-sessions/, redacting secrets.

    python scripts/export_sessions.py [--src <~/.claude/projects/<folder>>]

Redacted: anything shaped like an OpenAI key (sk-...), the employer email domain, and the
Claude account email. Everything else is copied unedited. Re-run before submitting so the
folder holds the full, final sessions. Exits non-zero if anything key-shaped survives.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = Path.home() / ".claude" / "projects" / "c--Users-YashwanthYanala-Desktop-project"

RULES = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-REDACTED"),
    (re.compile(r"[A-Za-z0-9._%+\-]*@?firstdistributors(?:\.com)?", re.I), "[employer redacted]"),
    (re.compile(r"[A-Za-z0-9._%+\-]*saivikyath[A-Za-z0-9._%+\-]*(?:@[A-Za-z0-9.\-]+)?", re.I), "[account email redacted]"),
]
KEYLIKE = re.compile(r"sk-(?!REDACTED)[A-Za-z0-9_\-]{8,}")


def redact(text: str) -> tuple[str, int]:
    n = 0
    for pat, rep in RULES:
        text, k = pat.subn(rep, text)
        n += k
    return text, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=ROOT / "ai-sessions")
    a = ap.parse_args()
    if a.out.exists():
        shutil.rmtree(a.out)
    total, files = 0, 0
    for f in sorted(a.src.rglob("*")):
        if not f.is_file():
            continue
        dest = a.out / f.relative_to(a.src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = f.read_bytes().decode("utf-8", errors="surrogateescape")
        text, n = redact(text)
        dest.write_bytes(text.encode("utf-8", errors="surrogateescape"))
        total, files = total + n, files + 1
    left = [p for p in a.out.rglob("*") if p.is_file() and KEYLIKE.search(p.read_text("utf-8", errors="replace"))]
    print(f"{files} files -> {a.out} | {total} redactions | key-shaped strings left: {len(left)}")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
