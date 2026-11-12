"""List the OpenAI models this key can use (a free metadata call). Prints model ids only."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import OpenAI  # noqa: E402

from rfq.config import api_key  # noqa: E402

key = api_key()
if not key:
    sys.exit("No OPENAI_API_KEY in the environment or .env")
ids = sorted(m.id for m in OpenAI(api_key=key).models.list())
print(f"{len(ids)} models")
for i in ids:
    if not any(x in i for x in ("tts", "whisper", "dall-e", "embedding", "audio", "realtime", "transcribe", "moderation", "image", "search", "davinci", "babbage")):
        print(" ", i)
