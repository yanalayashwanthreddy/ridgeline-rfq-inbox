"""Keep only the cache entries a run used (its .report.json lists them); delete the rest.

    python scripts/prune_cache.py parsed.report.json cache
    python scripts/prune_cache.py tests/stress/parsed.report.json tests/stress/cache

Experiments (other prompts, models, efforts) leave entries no current config can hit.
The cache dir is required, and every key the report needs must already be in it: a report
from another run (another cache) is refused instead of emptying this one.
"""

import json
import sys
from pathlib import Path

if len(sys.argv) != 3:
    sys.exit(__doc__)
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
cache = Path(sys.argv[2])
keep = {k for s in report["emails"].values() for k in (s.get("cache_keys") or [s.get("cache_key")])} - {None}
missing = [e for e, s in report["emails"].items() if not s.get("cache_key")]
if missing:
    sys.exit(f"refusing to prune: emails without a cache entry in this report: {missing}")
have = {p.stem for p in cache.glob("*.json")}
if not keep <= have:
    sys.exit(f"refusing to prune: {len(keep - have)} of the report's {len(keep)} entries are not in {cache}; "
             "is this the right report for this cache?")
gone = [p for p in cache.glob("*.json") if p.stem not in keep]
for p in gone:
    p.unlink()
print(f"kept {len(keep)}, removed {len(gone)}")
