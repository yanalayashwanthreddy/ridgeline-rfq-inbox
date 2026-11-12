"""The one place that talks to the model, behind a content-addressed cache.

Cache key = sha256 of the *entire request* (model, reasoning effort, instructions, schema, and
the email content including image bytes). Consequences:
  * replay: a committed cache answers every dev email with no API key;
  * idempotent: running twice makes zero new calls the second time;
  * honest invalidation: change the prompt, the model or the input and the key changes;
  * the same email delivered twice (identical content) costs one call.

Failure policy: the OpenAI client retries transient errors (429/5xx/timeouts) with backoff.
After CIRCUIT_TRIP consecutive failures we stop calling for the rest of the run (provider is
down: fail fast and flag, don't spend 3 minutes per email timing out). Every failure surfaces
as ModelUnavailable, which the pipeline turns into a flagged email, never a crash.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# USD per 1M tokens: (input, cached input, output). Reasoning tokens bill as output.
# Source: third-party pricing tables, Nov 2026 -- verify against openai.com/api/pricing.
PRICES = {
    "gpt-5.4-mini-2026-03-17": (0.75, 0.075, 4.50),
    "gpt-5.5-2026-04-23": (5.00, 0.50, 30.00),
}

CIRCUIT_TRIP = 3
MAX_OUTPUT_TOKENS = 32_000


class ModelUnavailable(Exception):
    """No answer from the model: no key and no cache, provider down, or an unusable response."""


@dataclass
class CallResult:
    data: dict
    usage: dict
    cost_usd: float | None
    cached: bool
    seconds: float
    key: str = ""


class LLM:
    def __init__(self, model: str, effort: str, cache_dir: Path, api_key: str | None):
        self.model = model
        self.effort = effort
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = None
        if api_key:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key, max_retries=3, timeout=180)
        self._lock = threading.Lock()
        self._failures = 0

    def request(self, instructions: str, content: list[dict], schema: dict) -> dict:
        return {
            "model": self.model,
            "reasoning": {"effort": self.effort},
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "email_parse", "schema": schema, "strict": True}},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }

    @staticmethod
    def key(req: dict) -> str:
        return hashlib.sha256(json.dumps(req, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def call(self, req: dict, label: str = "") -> CallResult:
        key = self.key(req)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
                return CallResult(rec["response"], rec["usage"], rec.get("cost_usd"), True, 0.0, key)
            except (OSError, ValueError, KeyError):
                pass  # corrupt cache entry: fall through and re-ask
        if self.client is None:
            raise ModelUnavailable("no OPENAI_API_KEY set and no cached response for this email")
        with self._lock:
            if self._failures >= CIRCUIT_TRIP:
                raise ModelUnavailable(f"model provider failed {self._failures} times in a row; not calling it again this run")

        t0 = time.monotonic()
        try:
            resp = self.client.responses.create(**req)
            if getattr(resp, "status", "completed") != "completed":
                why = getattr(getattr(resp, "incomplete_details", None), "reason", None) or resp.status
                raise ModelUnavailable(f"model response incomplete: {why}")
            data = json.loads(resp.output_text)
        except ModelUnavailable:
            self._fail()
            raise
        except Exception as e:  # network, auth, rate limit after retries, refusal, bad JSON
            self._fail()
            raise ModelUnavailable(f"{type(e).__name__}: {e}") from e
        with self._lock:
            self._failures = 0
        secs = time.monotonic() - t0

        u = resp.usage
        usage = {
            "input_tokens": u.input_tokens,
            "cached_input_tokens": getattr(u.input_tokens_details, "cached_tokens", 0) or 0,
            "output_tokens": u.output_tokens,
            "reasoning_tokens": getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0,
        }
        cost = cost_usd(self.model, usage)
        rec = {
            "key": key,
            "label": label,
            "model": self.model,
            "effort": self.effort,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "response_id": getattr(resp, "id", None),
            "seconds": round(secs, 2),
            "usage": usage,
            "cost_usd": cost,
            "response": data,
        }
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash mid-write never leaves a half-written cache entry
        return CallResult(data, usage, cost, False, secs, key)

    def _fail(self) -> None:
        with self._lock:
            self._failures += 1


def cost_usd(model: str, usage: dict) -> float | None:
    p = PRICES.get(model)
    if not p:
        return None
    fresh = usage["input_tokens"] - usage["cached_input_tokens"]
    return round((fresh * p[0] + usage["cached_input_tokens"] * p[1] + usage["output_tokens"] * p[2]) / 1e6, 6)
