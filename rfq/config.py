"""Settings. The API key comes from the environment, or from a git-ignored .env file.

A real environment variable always wins over .env, so the graders' OPENAI_API_KEY is used
as-is. No key is not an error: the run replays from the cache and flags anything uncached.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Minimal KEY=VALUE reader (no dependency). Never overrides a variable already set."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def api_key() -> str | None:
    load_dotenv()
    return os.environ.get("OPENAI_API_KEY") or None
