"""Check the submission the way the graders will, from a fresh clone of the last commit.

    python scripts/verify.py [--keep] [--linux]

1. clone HEAD into a temp folder (uncommitted changes are not in the submission: warned)
2. parse dev with OPENAI_API_KEY removed -> must equal the committed parsed.json byte for byte
3. parse again -> must make 0 new model calls (idempotent)
4. data/check_format.py on the output
5. score dev and stress: 0 silent losses, 0 hallucinated lines, 0 confident wrong matches
6. the test suite, and one seed of the generated matcher test
7. hygiene: nothing key-shaped in any tracked file (ai-sessions/ included), repo under 50 MB
8. --linux (needs Docker): the same on Python 3.11 / Linux: tests, and byte-identical outputs

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
        # against the COMMITTED file (the clone's), never this working folder's: that is what a grader gets
        same = (clone / "out1.json").read_bytes() == (clone / "parsed.json").read_bytes()
        check("keyless output == committed parsed.json (byte for byte)", same)
        rep = json.loads((clone / "out2.report.json").read_text(encoding="utf-8"))["totals"]
        check("second run makes no new model calls", rep["model_calls"] == 0 and rep["fallbacks"] == 0,
              f"calls {rep['model_calls']}, fallbacks {rep['fallbacks']}, cache hits {rep['cache_hits']}/{rep['emails']}")
        check("cost reported per email", rep.get("avg_cost_usd_per_email", 0) > 0,
              f"${rep.get('avg_cost_usd_per_email')}/email, ${rep.get('cost_usd_if_uncached')} per dev inbox uncached")

        # 4. format
        f = run([PY, "data/check_format.py", "out1.json", "--emails", "data/dev/emails"], clone, env)
        check("check_format.py", f.returncode == 0, ((f.stdout + f.stderr).strip().splitlines() or ["(no output)"])[-1][:120])

        # 5. scores. The stress replay must also equal its committed output; dev must report the
        # matcher (a missing matcher line fails rather than silently skipping the check).
        for label, parsed, key, cache, committed in (
                ("dev", "out1.json", "data/dev/answer_key.json", None, None),
                ("stress", "st.json", "tests/stress/answer_key.json", "tests/stress/cache", "tests/stress/parsed.json")):
            if cache:
                r = run([PY, "-m", "rfq", "parse", "--emails", "tests/stress/emails", "--cache", cache, "--out", parsed], clone, env)
                check("parse stress without a key == committed output", r.returncode == 0 and
                      (clone / parsed).read_bytes() == (clone / committed).read_bytes(),
                      (r.stderr.strip().splitlines() or [""])[-1][:120])
            s = run([PY, "scripts/score.py", parsed, key], clone, env).stdout
            silent, halluc = metric(s, "silent losses"), metric(s, "hallucinated lines")
            check(f"{label}: 0 silent losses, 0 hallucinated lines", silent == "0" and halluc == "0",
                  f"classes {metric(s, 'classification')}; recall {metric(s, 'line recall')}; "
                  f"precision {metric(s, 'line precision')}; context {metric(s, 'context carried')}")
            if label == "dev":
                m = re.search(r"matcher: .+", s)
                check("dev: 0 confident wrong catalog matches", bool(m) and "confident wrong 0 " in m[0] + " ",
                      m[0].replace("matcher: ", "") if m else "no matcher results in the output")

        # report.html must build, and the committed one must be what the committed outputs give
        committed_report = (clone / "report.html").read_bytes() if (clone / "report.html").exists() else b""
        r = run([PY, "scripts/build_report.py"], clone, env)
        check("report.html builds, and the committed one is up to date",
              r.returncode == 0 and (clone / "report.html").read_bytes() == committed_report,
              "rebuilt page differs from the committed report.html: run scripts/build_report.py and commit"
              if r.returncode == 0 and (clone / "report.html").read_bytes() != committed_report
              else (r.stdout.strip() or r.stderr.strip()[-150:])[:120])

        # 6. tests
        t = run([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"], clone, env)
        check("test suite", t.returncode == 0, (t.stdout.strip().splitlines() or ["?"])[-1])
        z = run([PY, "scripts/matcher_fuzz.py", "--seed", "101"], clone, env).stdout
        check("generated matcher test (seed 101)", "confident wrong matches: 0" in z,
              (z.splitlines() or ["?"])[0])

        # 8 (optional, needs Docker). The graders may run Linux/WSL/Docker and Python 3.11: same
        # bytes there? (This caught an OS-dependent MIME type that broke a cache key.)
        if "--linux" in sys.argv:
            script = (
                "cp -r /src /w && cd /w && rm -rf .venv .env && "
                "python -m pip install -q -r requirements.txt -r requirements-dev.txt >/dev/null 2>&1 && "
                "python -m pytest -q -p no:cacheprovider >/tmp/t.txt 2>&1; tail -1 /tmp/t.txt; "
                "OPENAI_API_KEY= python -m rfq parse --emails data/dev/emails --out /tmp/d.json >/dev/null 2>&1; "
                "cmp -s /tmp/d.json parsed.json && echo DEV_SAME; "
                "OPENAI_API_KEY= python -m rfq parse --emails tests/stress/emails --cache tests/stress/cache "
                "--out /tmp/s.json >/dev/null 2>&1; cmp -s /tmp/s.json tests/stress/parsed.json && echo STRESS_SAME"
            )
            d = run(["docker", "run", "--rm", "-v", f"{clone}:/src:ro", "python:3.11-slim", "sh", "-c", script], clone,
                    {**os.environ, "MSYS_NO_PATHCONV": "1"})
            check("Python 3.11 on Linux: tests pass, dev and stress byte-identical",
                  "passed" in d.stdout and "failed" not in d.stdout and "DEV_SAME" in d.stdout and "STRESS_SAME" in d.stdout,
                  " ".join(d.stdout.split())[:120] or d.stderr.strip()[-120:])

        # 7. hygiene
        files = run(["git", "ls-files", "-z"], clone).stdout.split("\0")
        leaks = [p for p in files if p and (clone / p).is_file() and KEY.search((clone / p).read_bytes())]
        check("no API key in any tracked file", not leaks, ", ".join(leaks[:5]))
        # ...nor anywhere in the history that ships with the repo (a key removed later is still in git log -p)
        hist = subprocess.run(["git", "log", "-p", "--all", "--no-color", "--text"], cwd=clone, capture_output=True).stdout
        found = KEY.findall(hist)
        check("no API key anywhere in the git history", not found, f"{len(found)} key-shaped string(s)" if found else
              f"{len(hist) / 1e6:.0f} MB of history scanned")
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
