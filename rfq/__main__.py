"""python -m rfq parse --emails <dir> --out parsed.json      (the assignment's command: one inbox)
python -m rfq serve  --state <dir> [--once]                  (production shape: a stream, see service.py)
python -m rfq status --state <dir>
python -m rfq export --state <dir> --out parsed.json"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import ROOT, api_key
from .llm import LLM
from .pipeline import run

DEFAULT_MODEL = "gpt-5.4-mini-2026-03-17"
DEFAULT_EFFORT = "medium"  # bake-off on dev: low skimmed rules (ditto marks), gpt-5.5 cost 4.6x for no gain


def _model_args(p) -> None:
    p.add_argument("--model", default=os.environ.get("RFQ_MODEL", DEFAULT_MODEL))
    p.add_argument("--effort", default=os.environ.get("RFQ_EFFORT", DEFAULT_EFFORT), choices=["none", "minimal", "low", "medium", "high"])
    p.add_argument("--cache", type=Path, default=ROOT / "cache")
    p.add_argument("--offline", action="store_true", help="never call the model; cache only")
    p.add_argument("--catalog", type=Path, default=ROOT / "data" / "catalog.csv",
                   help="catalog for sku/match fields (code only, no model calls)")
    p.add_argument("--no-match", action="store_true", help="skip catalog matching")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="rfq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse", help="parse every .eml in a directory")
    p.add_argument("--emails", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    _model_args(p)
    s = sub.add_parser("serve", help="process emails dropped in <state>/intake/, with a queue and history")
    s.add_argument("--state", type=Path, required=True)
    s.add_argument("--once", action="store_true", help="process what is there now, then exit")
    s.add_argument("--poll", type=float, default=5.0, help="seconds between intake checks")
    _model_args(s)
    st = sub.add_parser("status", help="queue health of a service state folder")
    st.add_argument("--state", type=Path, required=True)
    ex = sub.add_parser("export", help="every finished result of a service, as one parsed.json")
    ex.add_argument("--state", type=Path, required=True)
    ex.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)

    if a.cmd in ("status", "export"):
        from .service import Service

        if not (a.state / "rfq.db").exists():
            print(f"no service state in {a.state}", file=sys.stderr)
            return 2
        svc = Service(a.state, LLM(DEFAULT_MODEL, DEFAULT_EFFORT, ROOT / "cache", None), None)
        if a.cmd == "status":
            print(json.dumps(svc.status(), indent=2))
        else:
            print(f"{svc.export(a.out)} email(s) -> {a.out}", file=sys.stderr)
        return 0

    key = None if a.offline else api_key()
    if key is None:
        print("no OPENAI_API_KEY: replaying from cache; uncached emails will be flagged for review"
              if a.cmd == "parse" else "no OPENAI_API_KEY: replaying from cache; uncached emails wait in the queue",
              file=sys.stderr)
    llm = LLM(a.model, a.effort, a.cache, key)
    catalog = None if a.no_match else a.catalog

    if a.cmd == "serve":
        from .service import Service

        try:
            Service(a.state, llm, catalog).serve(once=a.once, poll=a.poll)
        except KeyboardInterrupt:
            pass
        return 0

    if not a.emails.is_dir():
        print(f"--emails {a.emails} is not a directory", file=sys.stderr)
        return 2
    rep = run(a.emails, a.out, llm, a.workers, catalog)
    t = rep["totals"]
    if t.get("catalog_matching"):
        print(f"catalog matching: {t['catalog_matching']}", file=sys.stderr)
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
