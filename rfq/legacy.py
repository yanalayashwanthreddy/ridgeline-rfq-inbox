"""Legacy Microsoft formats inside an OLE compound file: Outlook .msg and Word 97-2003 .doc.

Both readers were checked against independent tools (extract-msg, antiword) on real files from
Apache POI's test data (tests/fixtures/legacy/, see NOTICE there).

.msg  The message's own text (subject, from, to, date, body) becomes the Part's text; each
      attachment becomes its own Part, read by the normal readers. An Outlook message attached
      inside the message is unpacked the same way, recursively.
.doc  The text is rebuilt from the document's piece table (the order Word stores text in), with
      table rows as "a | b | c". Character formatting is NOT read, so a struck-through line or an
      unaccepted tracked deletion looks like ordinary text: the Part says so, and the email is
      flagged for review (lines are still extracted).
"""

from __future__ import annotations

import io
import re
import struct
from datetime import datetime, timedelta, timezone

SMTP = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
DOC_NO_FORMATTING ="legacy .doc read as plain text: strikethrough and tracked deletions are not visible"


# --------------------------------------------------------------------------- Outlook .msg


def is_msg(data: bytes) -> bool:
    try:
        import olefile

        ole = olefile.OleFileIO(io.BytesIO(data))
        return ole.exists("__properties_version1.0") and (ole.exists("__substg1.0_0037001F") or ole.exists("__substg1.0_0037001E")
                                                          or ole.exists("__substg1.0_1000001F") or ole.exists("__substg1.0_1000001E")
                                                          or any(e[0].startswith("__attach_version1.0_") for e in ole.listdir()))
    except Exception:
        return False


def _str(ole, prefix: list[str], tag: str) -> str:
    for kind, enc in (("001F", "utf-16-le"), ("001E", "cp1252")):
        path = prefix + [f"__substg1.0_{tag}{kind}"]
        if ole.exists(path):
            return ole.openstream(path).read().decode(enc, "replace").rstrip("\x00").strip()
    return ""


def _bin(ole, prefix: list[str], tag: str) -> bytes | None:
    path = prefix + [f"__substg1.0_{tag}0102"]
    return ole.openstream(path).read() if ole.exists(path) else None


def _time(ole, prefix: list[str], header: int) -> str:
    """PR_CLIENT_SUBMIT_TIME (0x0039) or PR_MESSAGE_DELIVERY_TIME (0x0E06) from the property stream."""
    path = prefix + ["__properties_version1.0"]
    if not ole.exists(path):
        return ""
    raw = ole.openstream(path).read()
    found = {}
    for off in range(header, len(raw) - 15, 16):
        tag, _flags, value = struct.unpack_from("<IIQ", raw, off)
        if tag in (0x00390040, 0x0E060040) and value:
            found[tag] = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value // 10)
    t = found.get(0x00390040) or found.get(0x0E060040)
    return t.strftime("%a, %d %b %Y %H:%M:%S +0000") if t else ""


def read_msg(data: bytes, html_to_text) -> tuple[str, list[tuple[str, str, bytes | None, str | None]]]:
    """-> (the message as text, [(attachment name, mime, bytes, or None + text of an embedded message)])."""
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    return _msg_storage(ole, [], 32, html_to_text)


def _msg_storage(ole, prefix: list[str], header: int, html_to_text):
    sender = _str(ole, prefix, "0C1A")
    addr = _str(ole, prefix, "5D01") or _str(ole, prefix, "0C1F")
    # Exchange often stores the sender as an internal X.500 name; the internet address is then
    # only in the original transport headers kept inside the .msg (PR_TRANSPORT_MESSAGE_HEADERS)
    hdrs = _str(ole, prefix, "007D")
    if not SMTP.fullmatch(addr) and hdrs:
        m = re.search(r"^From:\s*(.+(?:\n[ \t].+)*)", hdrs, re.M | re.I)
        found = SMTP.search(m[1]) if m else None
        addr = found[0] if found else addr
    if not SMTP.fullmatch(addr):  # an Exchange X.500 name ("/O=.../CN=KEVIN.ROAST@BEN") is no address
        addr = ""
    head = [
        ("From", f"{sender} <{addr}>" if sender and addr else sender or addr),
        ("To", _str(ole, prefix, "0E04")),
        ("Cc", _str(ole, prefix, "0E03")),
        ("Date", _time(ole, prefix, header)),
        ("Subject", _str(ole, prefix, "0037")),
    ]
    body = _str(ole, prefix, "1000")
    html = _bin(ole, prefix, "1013")
    if html and (not body or re.search(rb"<(s|strike|del)\b|line-through", html, re.I)):
        body = html_to_text(html.decode("utf-8", "replace") if html[:3] == b"\xef\xbb\xbf" or b"charset=utf-8" in html[:2000].lower()
                            else html.decode("cp1252", "replace"))
    text = "\n".join(f"{k}: {v}" for k, v in head if v) + "\n\n" + body.strip()

    atts = []
    stems = sorted({e[len(prefix)] for e in ole.listdir() if len(e) > len(prefix) and e[:len(prefix)] == prefix
                    and e[len(prefix)].startswith("__attach_version1.0_")})
    for stem in stems:
        ap = prefix + [stem]
        name = _str(ole, ap, "3707") or _str(ole, ap, "3704") or _str(ole, ap, "3001") or f"{stem[-8:]}.bin"
        mime = _str(ole, ap, "370E") or "application/octet-stream"
        if ole.exists(ap + ["__substg1.0_3701000D"]):  # an Outlook message attached inside this one
            inner_text, inner_atts = _msg_storage(ole, ap + ["__substg1.0_3701000D"], 24, html_to_text)
            subj = re.search(r"^Subject: (.+)$", inner_text, re.M)
            atts.append(((name if name.lower().endswith(".msg") else f"{subj[1] if subj else name}.msg"),
                         "application/vnd.ms-outlook", None, inner_text))
            atts.extend(inner_atts)
            continue
        blob = _bin(ole, ap, "3701")
        if blob is not None:
            atts.append((name, mime, blob, None))
    return text, atts


# --------------------------------------------------------------------------- Word 97-2003 .doc


def is_doc(data: bytes) -> bool:
    try:
        import olefile

        return olefile.OleFileIO(io.BytesIO(data)).exists("WordDocument")
    except Exception:
        return False


def read_doc(data: bytes) -> str:
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    wd = ole.openstream("WordDocument").read()
    if struct.unpack_from("<H", wd, 0)[0] != 0xA5EC:
        raise ValueError("not a Word 97-2003 document")
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    if flags & 0x0100:
        raise ValueError("the .doc is password-protected (encrypted)")
    table = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()

    # FIB: FibBase (32 bytes), then the variable-length fibRgW, fibRgLw and fibRgFcLcb arrays
    pos = 32
    csw = struct.unpack_from("<H", wd, pos)[0]
    pos += 2 + csw * 2
    cslw = struct.unpack_from("<H", wd, pos)[0]
    ccp_text = struct.unpack_from("<i", wd, pos + 2 + 3 * 4)[0]  # FibRgLw97.ccpText: main-document length
    pos += 2 + cslw * 4
    pos += 2  # cbRgFcLcb
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, pos + 33 * 8)  # FibRgFcLcb97.fcClx / lcbClx

    # Clx: skip Prc (0x01) records, then the Pcdt (0x02) holding the piece table
    clx = table[fc_clx:fc_clx + lcb_clx]
    i = 0
    while i < len(clx) and clx[i] == 0x01:
        i += 3 + struct.unpack_from("<h", clx, i + 1)[0]
    if i >= len(clx) or clx[i] != 0x02:
        raise ValueError("no piece table in this .doc")
    lcb = struct.unpack_from("<I", clx, i + 1)[0]
    plc = clx[i + 5:i + 5 + lcb]
    n = (lcb - 4) // 12
    cps = struct.unpack_from(f"<{n + 1}i", plc, 0)
    out = []
    for k in range(n):
        fc = struct.unpack_from("<I", plc, 4 * (n + 1) + 8 * k + 2)[0]
        length = cps[k + 1] - cps[k]
        if fc & 0x40000000:  # compressed: one byte per character, cp1252
            off = (fc & 0x3FFFFFFF) // 2
            out.append(wd[off:off + length].decode("cp1252", "replace"))
        else:
            off = fc & 0x3FFFFFFF
            out.append(wd[off:off + 2 * length].decode("utf-16-le", "replace"))
    return _clean_doc_text("".join(out)[:max(ccp_text, 0)])


def _clean_doc_text(t: str) -> str:
    t = re.sub(r"\x13[^\x13\x14\x15]*\x14", "", t)  # field code -> keep the field's shown result
    t = re.sub(r"\x13[^\x13\x14\x15]*\x15", "", t)  # field with no result
    t = t.replace("\x15", "")
    t = t.replace("\x07\x07", "\x07\n")  # end of a table row
    t = t.replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    t = t.replace("\x1e", "-").replace("\x1f", "").replace("\xa0", " ")
    t = re.sub(r"[\x00-\x06\x08\x0e-\x12\x16-\x1d]", "", t)
    lines = []
    for line in t.split("\n"):
        if "\x07" in line:  # table cells
            cells = [c.strip() for c in line.split("\x07")]
            while cells and not cells[-1]:
                cells.pop()
            line = " | ".join(cells)
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
