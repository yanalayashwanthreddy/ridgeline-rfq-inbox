"""python -m rfq parse --emails <dir> --out parsed.json"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import ROOT, api_key
from .llm import LLM
from .pipeline import run

DEFAULT_MODEL = "gpt-5.4-mini-2026-03-17"
DEFAULT_EFFORT = "medium"  # bake-off on dev: low skimmed rules (ditto marks), gpt-5.5 cost 4.6x for no gain


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="rfq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="parse every .eml in a directory")
    p.add_argument("--emails", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", default=os.environ.get("RFQ_MODEL", DEFAULT_MODEL))
    p.add_argument("--effort", default=os.environ.get("RFQ_EFFORT", DEFAULT_EFFORT), choices=["none", "minimal", "low", "medium", "high"])
    p.add_argument("--cache", type=Path, default=ROOT / "cache")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--offline", action="store_true", help="never call the model; cache only")
    a = ap.parse_args(argv)

    if not a.emails.is_dir():
        print(f"--emails {a.emails} is not a directory", file=sys.stderr)
        return 2
    key = None if a.offline else api_key()
    if key is None:
        print("no OPENAI_API_KEY: replaying from cache; uncached emails will be flagged for review", file=sys.stderr)
    llm = LLM(a.model, a.effort, a.cache, key)
    rep = run(a.emails, a.out, llm, a.workers)
    t = rep["totals"]
    print(
        f"{t['emails']} emails -> {a.out} | model {rep['model']} ({rep['effort']}) | "
        f"new calls {t['model_calls']}, cache hits {t['cache_hits']}, fallbacks {t['fallbacks']} | "
        f"this run ${t['cost_usd_this_run']:.4f}, full inbox uncached ${t['cost_usd_if_uncached']:.4f} "
        f"(${t['avg_cost_usd_per_email']:.4f}/email, ~{t['avg_tokens_per_email']} tokens/email) | {t['wall_seconds']}s",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
