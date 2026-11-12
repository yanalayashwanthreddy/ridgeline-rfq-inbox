"""Keep only the cache entries a run used (its .report.json lists them); delete the rest.

    python scripts/prune_cache.py parsed.report.json [cache_dir]

Experiments (other prompts, models, efforts) leave entries no current config can hit.
"""

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
cache = Path(sys.argv[2] if len(sys.argv) > 2 else "cache")
keep = {s.get("cache_key") for s in report["emails"].values()} - {None}
missing = [e for e, s in report["emails"].items() if not s.get("cache_key")]
if missing:
    sys.exit(f"refusing to prune: emails without a cache entry in this report: {missing}")
gone = [p for p in cache.glob("*.json") if p.stem not in keep]
for p in gone:
    p.unlink()
print(f"kept {len(keep)}, removed {len(gone)}")
