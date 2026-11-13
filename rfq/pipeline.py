"""Run the inbox: ingest -> model (cached) -> guards, per email in parallel; then thread links.

Every email is isolated: any exception for one email becomes a flagged record for that email
and the run carries on. The run always writes a complete parsed.json.
"""

from __future__ import annotations

import json
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .guards import fallback, finalize, missing_rows
from .ingest import Email, load_email
from .llm import LLM, ModelUnavailable
from .matcher import load_catalog, match_all
from .prompt import INSTRUCTIONS, PROMPT_VERSION, REQUEST_CLASSES, build_input
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
        _account(stats, res)
        record, warns = finalize(em, res.data, truncated)
        stats["model_reason"] = res.data.get("classification_reason")

        # Code checks completeness; the model gets one pointed second look (also cached).
        missed = missing_rows(em, record["line_items"]) if record["classification"] in REQUEST_CLASSES else {}
        if missed:
            listing = "\n".join(f"- from {f}: {r}" for f, rows in missed.items() for r in rows)
            stats["coverage_retry"] = listing
            note = (
                "CHECK: your previous answer did not include these numbered rows:\n" + listing +
                "\nRe-read the email. Return the complete answer again: include each of these rows "
                "if the customer is asking for it; leave a row out only if it is not requested "
                "(heading, crossed out, superseded, not among the accepted items)."
            )
            req2 = llm.request(INSTRUCTIONS, content + [{"type": "input_text", "text": note}], schema)
            try:
                res2 = llm.call(req2, label=f"{PROMPT_VERSION}:{em.id}:coverage")
                _account(stats, res2)
                record2, warns2 = finalize(em, res2.data, truncated)
                left = missing_rows(em, record2["line_items"])
                if record2["classification"] == record["classification"] and sum(map(len, left.values())) < sum(map(len, missed.values())):
                    record, warns, missed = record2, warns2, left
            except ModelUnavailable as e:
                warns.append(f"coverage retry failed: {e}")
            if missed:
                warns.append(f"model left out numbered rows after a second look (judged not requested): {missed}")
        stats["warnings"] += warns
    except ModelUnavailable as e:
        stats["fallback"] = str(e)
        record = fallback(em, str(e))
    except Exception as e:  # a bug of ours must not cost the customer their answer
        stats["fallback"] = f"internal error: {type(e).__name__}: {e}"
        stats["traceback"] = traceback.format_exc(limit=5)
        record = fallback(em, f"internal error: {type(e).__name__}")
    return em, record, stats


def _account(stats: dict, res) -> None:
    """Add one call's tokens and cost to the email's stats (a cache hit adds tokens, not cost)."""
    keys = stats.setdefault("cache_keys", [])
    keys.append(res.key)
    stats["cache_key"] = keys[0]
    stats["cached"] = res.cached if len(keys) == 1 else stats["cached"] and res.cached
    if stats["usage"] is None:
        stats["usage"] = dict(res.usage)
    else:
        for k, v in res.usage.items():
            stats["usage"][k] = stats["usage"].get(k, 0) + v
    stats["seconds"] = round(stats["seconds"] + res.seconds, 2)
    if not res.cached:
        stats["model_calls"] += 1
        stats["cost_usd"] = (stats["cost_usd"] or 0) + (res.cost_usd or 0)


def run(email_dir: Path, out_path: Path, llm: LLM, workers: int = 4, catalog: Path | None = None) -> dict:
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

    # catalog matching is code only (no model calls), so it runs on every replay as well
    matching = None
    if catalog is not None and catalog.is_file():
        try:
            match_all(out, load_catalog(catalog))
            matching = dict(Counter(ln["match"] for rec in out.values() for ln in rec["line_items"]))
        except Exception as e:  # optional; never lose the parse over it
            matching = {"error": f"{type(e).__name__}: {e}"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report = _report(stats, llm, time.monotonic() - t0)
    report["totals"]["catalog_matching"] = matching
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
