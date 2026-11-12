#!/usr/bin/env python3
"""Optional helper: .eml -> JSON + attachment files.  Use it, port it, or ignore it.  Stdlib only.

    python eml_to_json.py dev/emails/E001.eml                      # one email -> stdout
    python eml_to_json.py dev/emails --out inbox.json --files att  # all emails; attachments saved

Shape: {id, headers{from,to,cc,date,subject,message_id,in_reply_to,references},
        text, html, attachments:[{filename, mime, size, inline, content_id, path}]}
`path` is set only with --files (bytes written to <files>/<id>/<filename>).  A forwarded or
bounced message attached as message/rfc822 is saved as a .eml file.  Reading what is INSIDE an
attachment (Excel, PDF, scans, photos ...) is up to you.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

HEADERS = ("from", "to", "cc", "date", "subject", "message-id", "in-reply-to", "references")


def _text(part) -> str:
    try:
        return part.get_content()
    except Exception:  # unknown charset / bad transfer encoding
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\- ]+", "_", name).strip() or "unnamed"


def eml_to_json(path: Path, files: Path | None = None) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    out = {
        "id": path.stem,
        "headers": {h.replace("-", "_"): msg.get(h) for h in HEADERS},
        "text": None,
        "html": None,
        "attachments": [],
    }
    stack = [msg]
    while stack:
        part = stack.pop(0)
        ct = part.get_content_type()
        if ct == "message/rfc822":
            data = (
                part.get_payload(0).as_bytes()
                if part.is_multipart()
                else part.get_payload(decode=True)
            )
            _add(out, part, data, files, default=f"attached-{len(out['attachments']) + 1}.eml")
            continue
        if part.is_multipart():
            stack[0:0] = list(part.iter_parts())
            continue
        disp = part.get_content_disposition()
        if disp in ("attachment", "inline") and (part.get_filename() or ct.split("/")[0] != "text"):
            _add(out, part, part.get_payload(decode=True) or b"", files)
        elif ct == "text/plain" and out["text"] is None:
            out["text"] = _text(part)
        elif ct == "text/html" and out["html"] is None:
            out["html"] = _text(part)
    return out


def _add(out, part, data: bytes, files: Path | None, default: str = "unnamed") -> None:
    name = part.get_filename() or default
    item = {
        "filename": name,
        "mime": part.get_content_type(),
        "size": len(data),
        "inline": part.get_content_disposition() == "inline",
        "content_id": (part.get("Content-ID") or "").strip("<>") or None,
        "path": None,
    }
    if files:
        p = files / out["id"] / _safe(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        item["path"] = str(p)
    out["attachments"].append(item)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("path", type=Path, help=".eml file or a directory of them")
    ap.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    ap.add_argument("--files", type=Path, help="save attachment bytes under this directory")
    a = ap.parse_args(argv)
    if a.path.is_dir():
        data = {p.stem: eml_to_json(p, a.files) for p in sorted(a.path.glob("*.eml"))}
    else:
        data = eml_to_json(a.path, a.files)
    s = json.dumps(data, indent=2, ensure_ascii=False)
    if a.out:
        a.out.write_text(s + "\n", encoding="utf-8")
    else:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
