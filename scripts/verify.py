"""Check the submission the way the graders will, from a fresh clone of the last commit.

    python scripts/verify.py [--keep]

1. clone HEAD into a temp folder (uncommitted changes are not in the submission: warned)
2. parse dev with OPENAI_API_KEY removed -> must equal the committed parsed.json byte for byte
3. parse again -> must make 0 new model calls (idempotent)
4. data/check_format.py on the output
5. score dev and stress: 0 silent losses, 0 hallucinated lines, 0 confident wrong matches
6. the test suite, and one seed of the generated matcher test
7. hygiene: nothing key-shaped in any tracked file (ai-sessions/ included), repo under 50 MB

Exit code 0 only if every check passes. Uses this interpreter's installed packages.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
KEY = re.compile(rb"(?<![A-Za-z0-9])sk-(?!REDACTED)[A-Za-z0-9_\-]{20,}")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


def run(args: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")


def metric(out: str, label: str) -> str:
    m = re.search(rf"^{re.escape(label)}\s+(.+)$", out, re.M)
    return m[1].strip() if m else "?"


def main() -> int:
    keep = "--keep" in sys.argv
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"], ROOT).stdout.strip()
    head = run(["git", "rev-parse", "--short", "HEAD"], ROOT).stdout.strip()
    print(f"verifying commit {head}" + ("  (WARNING: uncommitted changes are not checked)" if dirty else ""))

    tmp = Path(tempfile.mkdtemp(prefix="rfq-verify-"))
    clone = tmp / "repo"
    try:
        c = run(["git", "clone", "-q", str(ROOT), str(clone)], tmp)
        if not check("fresh clone of HEAD", c.returncode == 0, c.stderr.strip()[:200]):
            return 1
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "RFQ_MODEL", "RFQ_EFFORT")}
        env["PYTHONIOENCODING"] = "utf-8"
        if (clone / ".env").exists():  # git-ignored; never in a clone, but be sure
            (clone / ".env").unlink()

        # 2-3. keyless replay, twice
        for n in (1, 2):
            r = run([PY, "-m", "rfq", "parse", "--emails", "data/dev/emails", "--out", f"out{n}.json"], clone, env)
            if not check(f"parse dev without a key (run {n})", r.returncode == 0, r.stderr.strip().splitlines()[-1][:150] if r.stderr.strip() else ""):
                return 1
        same = (clone / "out1.json").read_bytes() == (ROOT / "parsed.json").read_bytes()
        check("keyless output == committed parsed.json (byte for byte)", same)
        rep = json.loads((clone / "out2.report.json").read_text(encoding="utf-8"))["totals"]
        check("second run makes no new model calls", rep["model_calls"] == 0 and rep["fallbacks"] == 0,
              f"calls {rep['model_calls']}, fallbacks {rep['fallbacks']}, cache hits {rep['cache_hits']}/{rep['emails']}")
        check("cost reported per email", rep.get("avg_cost_usd_per_email", 0) > 0,
              f"${rep.get('avg_cost_usd_per_email')}/email, ${rep.get('cost_usd_if_uncached')} per dev inbox uncached")

        # 4. format
        f = run([PY, "data/check_format.py", "out1.json", "--emails", "data/dev/emails"], clone, env)
        check("check_format.py", f.returncode == 0, (f.stdout + f.stderr).strip().splitlines()[-1][:120])

        # 5. scores
        for label, parsed, key, cache in (("dev", "out1.json", "data/dev/answer_key.json", None),
                                          ("stress", "st.json", "tests/stress/answer_key.json", "tests/stress/cache")):
            if cache:
                run([PY, "-m", "rfq", "parse", "--emails", "tests/stress/emails", "--cache", cache, "--out", parsed], clone, env)
            s = run([PY, "scripts/score.py", parsed, key], clone, env).stdout
            silent, halluc = metric(s, "silent losses"), metric(s, "hallucinated lines")
            wrong = re.search(r"confident wrong (\d+)", s)
            check(f"{label}: 0 silent losses, 0 hallucinated lines", silent == "0" and halluc == "0",
                  f"classes {metric(s, 'classification')}; recall {metric(s, 'line recall')}; "
                  f"precision {metric(s, 'line precision')}; context {metric(s, 'context carried')}")
            if wrong:
                check(f"{label}: 0 confident wrong catalog matches", wrong[1] == "0",
                      re.search(r"matcher: .+", s)[0].replace("matcher: ", ""))

        # 6. tests
        t = run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], clone, env)
        check("test suite", t.returncode == 0, (t.stdout.strip().splitlines() or ["?"])[-1])
        z = run([PY, "scripts/matcher_fuzz.py", "--seed", "101"], clone, env).stdout
        check("generated matcher test (seed 101)", "confident wrong matches: 0" in z,
              (z.splitlines() or ["?"])[0])

        # 7. hygiene
        files = run(["git", "ls-files", "-z"], clone).stdout.split("\0")
        leaks = [p for p in files if p and (clone / p).is_file() and KEY.search((clone / p).read_bytes())]
        check("no API key in any tracked file", not leaks, ", ".join(leaks[:5]))
        size = sum((clone / p).stat().st_size for p in files if p and (clone / p).is_file())
        git_size = sum(f.stat().st_size for f in (clone / ".git").rglob("*") if f.is_file())
        check("repo under 50 MB", size + git_size < 50 * 1024 * 1024,
              f"{(size + git_size) / 1e6:.1f} MB incl. history")
    finally:
        if keep:
            print(f"clone kept at {clone}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
