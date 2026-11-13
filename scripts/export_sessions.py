"""Copy this project's Claude Code session logs into ai-sessions/, redacting secrets.

    python scripts/export_sessions.py [--src <~/.claude/projects/<folder>>]

Copies the session .jsonl files, their tool-result files, and memory/ (Claude Code's auto-memory
for this project: the only standing instruction the agent had, which the brief asks for).
Redacted: anything shaped like an OpenAI key (sk-...), the employer's name and email domain,
and the Claude account email. Everything else is copied unedited. Re-run before submitting so the
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

# (?<![A-Za-z0-9]) so words like "task-notification" or "disk-cleanup" are left alone
KEY = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}")
# Names are found by the keyword itself, then widened by hand to the surrounding email address.
# (A regex that starts with an open-ended [chars]* is quadratic on the logs' long base64 images.)
NAMES = [
    (re.compile(r"first[\s_\-]?distributors(?:\.com)?", re.I), "[employer redacted]"),
    (re.compile(r"saivikyath[A-Za-z0-9._%+\-]*(?:@[A-Za-z0-9.\-]+)?", re.I), "[account email redacted]"),
]
HOST = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
LOCAL = HOST | set("_%+")
KEYLIKE = re.compile(r"(?<![A-Za-z0-9])sk-(?!REDACTED)[A-Za-z0-9_\-]{8,}")
LEFTOVER = re.compile(r"first[\s_\-]?distributors|saivikyath", re.I)


def _widen(text: str, start: int) -> int:
    """Move start left over the rest of an email address: host labels, then '@' and the user."""
    i = start
    while i > 0 and text[i - 1] in HOST and start - i < 200:
        i -= 1
    if i > 0 and text[i - 1] == "@":
        i -= 1
        while i > 0 and text[i - 1] in LOCAL and start - i < 300:
            i -= 1
    return i


def redact(text: str) -> tuple[str, int]:
    text, n = KEY.subn("sk-REDACTED", text)
    for pat, rep in NAMES:
        out, last, k = [], 0, 0
        for m in pat.finditer(text):
            s = _widen(text, m.start())
            if s < last:
                s = last
            out += [text[last:s], rep]
            last, k = m.end(), k + 1
        text, n = "".join(out) + text[last:], n + k
    return text, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=ROOT / "ai-sessions")
    a = ap.parse_args()
    # check everything before deleting anything: a wrong --src must not wipe the last good export,
    # and --out must be this repo's ai-sessions folder, never a parent or a typo
    logs = list(a.src.glob("*.jsonl")) if a.src.is_dir() else []
    if not logs:
        sys.exit(f"no session logs (*.jsonl) in {a.src}; nothing deleted")
    if a.out.resolve().name != "ai-sessions" or a.out.resolve().parent != ROOT:
        sys.exit(f"--out must be {ROOT / 'ai-sessions'}; nothing deleted")
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
    left = [p for p in a.out.rglob("*") if p.is_file()
            and (KEYLIKE.search(t := p.read_text("utf-8", errors="replace")) or LEFTOVER.search(t))]
    print(f"{files} files -> {a.out} | {total} redactions | files with anything left to redact: {len(left)}")
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
