"""Score parsed.json against an answer key. Logic lives in rfq/score.py (the viewer reuses it).

    python scripts/score.py parsed.json [data/dev/answer_key.json] [-v]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rfq.score import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
