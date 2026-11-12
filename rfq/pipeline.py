"""Run the inbox: ingest -> model (cached) -> guards, per email in parallel; then thread links.

Every email is isolated: any exception for one email becomes a flagged record for that email
and the run carries on. The run always writes a complete parsed.json.
"""

from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .guards import fallback, finalize
from .ingest import Email, load_email
from .llm import LLM, ModelUnavailable
from .prompt import INSTRUCTIONS, PROMPT_VERSION, build_input
from .threads import link


def process_one(path: Path, llm: LLM) -> tuple[Email, dict, dict]:
    stats = {"model_calls": 0, "cached": False, "usage": None, "cost_usd": 0.0, "seconds": 0.0, "warnings": [], "fallback": None}
    em = Email(id=path.stem)
    try:
        em = load_email(path)
        stats["warnings"] += em.errors
        content, schema, truncated = build_input(em)
        req = llm.request(INSTRUCTIONS, content, schema)
        res = llm.call(req, label=f"{PROMPT_VERSION}:{em.id}")
        stats.update(cached=res.cached, usage=res.usage, seconds=round(res.seconds, 2))
        if not res.cached:
            stats["model_calls"] = 1
            stats["cost_usd"] = res.cost_usd
        record, warns = finalize(em, res.data, truncated)
        stats["warnings"] += warns
        stats["model_reason"] = res.data.get("classification_reason")
    except ModelUnavailable as e:
        stats["fallback"] = str(e)
        record = fallback(em, str(e))
    except Exception as e:  # a bug of ours must not cost the customer their answer
        stats["fallback"] = f"internal error: {type(e).__name__}: {e}"
        stats["traceback"] = traceback.format_exc(limit=5)
        record = fallback(em, f"internal error: {type(e).__name__}")
    return em, record, stats


def run(email_dir: Path, out_path: Path, llm: LLM, workers: int = 4) -> dict:
    paths = sorted(email_dir.glob("*.eml"))
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(lambda p: process_one(p, llm), paths))

    emails = {em.id: em for em, _, _ in results}
    out = {em.id: rec for em, rec, _ in results}
    stats = {em.id: st for em, _, st in results}
    try:
        link(emails, out)
    except Exception as e:  # links are a nice-to-have; never lose the per-email answers over them
        for st in stats.values():
            st["warnings"].append(f"thread linking failed: {e}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = _report(stats, llm, time.monotonic() - t0)
    out_path.with_name(out_path.stem + ".report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def _report(stats: dict, llm: LLM, secs: float) -> dict:
    tot = {"emails": len(stats), "model_calls": 0, "cache_hits": 0, "fallbacks": 0,
           "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0,
           "cost_usd_this_run": 0.0, "cost_usd_if_uncached": 0.0}
    for st in stats.values():
        tot["model_calls"] += st["model_calls"]
        tot["cache_hits"] += int(st["cached"])
        tot["fallbacks"] += int(bool(st["fallback"]))
        if st["usage"]:
            for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"):
                tot[k] += st["usage"][k]
            from .llm import cost_usd
            tot["cost_usd_if_uncached"] += cost_usd(llm.model, st["usage"]) or 0.0
        tot["cost_usd_this_run"] += st["cost_usd"] or 0.0
    n = max(1, sum(1 for st in stats.values() if st["usage"]))
    tot["avg_cost_usd_per_email"] = round(tot["cost_usd_if_uncached"] / n, 5)
    tot["avg_tokens_per_email"] = round((tot["input_tokens"] + tot["output_tokens"]) / n)
    tot["cost_usd_this_run"] = round(tot["cost_usd_this_run"], 5)
    tot["cost_usd_if_uncached"] = round(tot["cost_usd_if_uncached"], 5)
    tot["wall_seconds"] = round(secs, 1)
    return {"model": llm.model, "effort": llm.effort, "prompt_version": PROMPT_VERSION, "totals": tot, "emails": stats}
