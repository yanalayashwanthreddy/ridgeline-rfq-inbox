"""Ingest: one .eml -> an `Email` whose every part is something a model can read.

This layer is deterministic code, never a model. Its job is to turn each attachment into
text (or images) *without losing the structure that carries meaning*:

  * spreadsheets: sheet names, hidden sheets/rows/columns, strikethrough, merged headings
  * Word: headings and tables in document order, strikethrough, tracked deletions dropped
  * PDFs: rows rebuilt from word positions; scanned pages rendered to images;
          strikeout annotations and lines drawn through text marked
  * images: verified, downscaled, re-encoded
  * forwarded / bounced messages (message/rfc822): unpacked recursively

Anything that cannot be read becomes a Part with kind="unreadable" and a note saying why.
Nothing in here raises on a bad attachment: one broken file must never take down the run.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sys
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

PDF_WINDOW_PAGES = 30       # pages per model call; a longer PDF is read in several windows
PDF_WINDOW_IMAGES = 16      # scanned pages per window (a call takes at most 20 pictures)
MAX_PDF_PAGES = 300         # beyond this we read the first pages and flag the rest
MAX_SHEET_ROWS = 1500       # per sheet
MAX_IMAGE_SIDE = 2000       # px; larger images are downscaled before going to the model
MIN_IMAGE_SIDE = 16         # tracking pixels / spacers are dropped
SCAN_TEXT_CHARS = 25        # a PDF page with less text than this but with images is a scan

STRIKE_L, STRIKE_R = "~~", "~~"   # how struck-through text is shown to the model


# --------------------------------------------------------------------------- data model


@dataclass
class Image:
    label: str          # what the model is told this picture is, e.g. "IMG_4172.jpeg"
    data: bytes
    mime: str           # image/png or image/jpeg


@dataclass
class Part:
    filename: str
    mime: str
    size: int
    sha256: str
    inline: bool = False
    content_id: str | None = None
    kind: str = "text"  # text | image | unreadable | skipped
    text: str = ""
    images: list[Image] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # spreadsheet rows as "a | b | c", split by whether the sender could see them; lets code
    # (not the model) enforce "never take a line from a hidden sheet or row"
    hidden_rows: list[str] = field(default_factory=list)
    visible_rows: list[str] = field(default_factory=list)
    # a long PDF: `text`/`images` hold the first window; each later window is read by its own
    # model call: {"pages": "31-60", "text": ..., "images": [...]}
    windows: list[dict] = field(default_factory=list)

    @property
    def all_text(self) -> str:
        """Every page, for code checks (snapping raw, grounding, coverage) across all windows."""
        return "\n".join([self.text, *(w["text"] for w in self.windows)]).strip()


@dataclass
class Email:
    id: str
    sender: str = ""
    to: str = ""
    cc: str = ""
    date: str = ""
    timestamp: float | None = None
    subject: str = ""
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    auto_submitted: str = ""          # RFC 3834: anything but "no" means machine-generated
    precedence: str = ""              # "bulk" / "list" / "junk": mass mailings
    body: str = ""
    body_source: str = "text/plain"   # or text/html when we had to read the HTML
    parts: list[Part] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- entry point


def load_email(path: Path) -> Email:
    raw = path.read_bytes()
    em = Email(id=path.stem)
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as e:  # pragma: no cover - the stdlib parser is very forgiving
        em.errors.append(f"could not parse message: {e}")
        em.body = raw.decode("utf-8", "replace")
        return em

    em.sender = _hdr(msg, "from")
    em.to = _hdr(msg, "to")
    em.cc = _hdr(msg, "cc")
    em.date = _hdr(msg, "date")
    em.subject = _hdr(msg, "subject")
    em.message_id = _hdr(msg, "message-id") or None
    em.in_reply_to = _hdr(msg, "in-reply-to") or None
    em.references = _hdr(msg, "references").split()
    em.auto_submitted = _hdr(msg, "auto-submitted").lower()
    em.precedence = _hdr(msg, "precedence").lower()
    try:
        em.timestamp = parsedate_to_datetime(em.date).timestamp() if em.date else None
    except Exception:
        em.errors.append(f"unparseable Date header: {em.date!r}")

    try:
        em.body, em.body_source, em.parts = _walk(msg)
    except Exception as e:
        em.errors.append(f"could not walk MIME structure: {type(e).__name__}: {e}")
    return em


def _hdr(msg, name: str) -> str:
    try:
        v = msg.get(name)
        return str(v).strip() if v is not None else ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- MIME walk


def _walk(msg: EmailMessage) -> tuple[str, str, list[Part]]:
    """Return (body text, which body part we used, attachments)."""
    plain = html = None
    parts: list[Part] = []
    stack = [msg]
    while stack:
        part = stack.pop(0)
        ct = part.get_content_type()
        if ct == "message/rfc822":
            parts.extend(_attached_message(part, len(parts)))
            continue
        if part.is_multipart():
            stack[0:0] = list(part.iter_parts())
            continue
        disp = part.get_content_disposition()
        is_attachment = disp in ("attachment", "inline") and (
            part.get_filename() or ct.split("/")[0] != "text"
        )
        if is_attachment:
            parts.extend(_attachment(part, len(parts)))
        elif ct == "text/plain" and plain is None:
            plain = _text(part)
        elif ct == "text/html" and html is None:
            html = _text(part)

    # Prefer text/plain, but read the HTML when there is no plain part or when the HTML
    # carries meaning the plain alternative loses (struck-through rows).
    if html is not None and (plain is None or _html_has_strike(html)):
        return html_to_text(html), "text/html", parts
    return plain or "", "text/plain", parts


def _text(part) -> str:
    try:
        return part.get_content()
    except Exception:
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


def _attached_message(part, n: int) -> list[Part]:
    """A forwarded or bounced message attached whole: unpack it, including its attachments."""
    name = part.get_filename() or f"attached-{n + 1}.eml"
    try:
        inner = part.get_payload(0) if part.is_multipart() else None
        data = inner.as_bytes() if inner is not None else (part.get_payload(decode=True) or b"")
        if inner is None:
            inner = BytesParser(policy=policy.default).parsebytes(data)
    except Exception as e:
        return [_unreadable(name, "message/rfc822", b"", f"attached message could not be parsed: {e}")]
    p = _new_part(name, "message/rfc822", data, part)
    body, _, inner_parts = _walk(inner)
    hdrs = "\n".join(
        f"{h}: {_hdr(inner, h)}" for h in ("From", "To", "Date", "Subject") if _hdr(inner, h)
    )
    p.text = f"{hdrs}\n\n{body}".strip()
    for ip in inner_parts:
        ip.notes.append(f"found inside attached message {name}")
    return [p, *inner_parts]


def _new_part(name: str, mime: str, data: bytes, part=None) -> Part:
    return Part(
        filename=name,
        mime=mime,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        inline=bool(part is not None and part.get_content_disposition() == "inline"),
        content_id=((part.get("Content-ID") or "").strip("<> ") or None) if part is not None else None,
    )


def _unreadable(name: str, mime: str, data: bytes, why: str, part=None) -> Part:
    p = _new_part(name, mime, data, part)
    p.kind = "unreadable"
    p.notes.append(why)
    return p


# --------------------------------------------------------------------------- attachments


def _attachment(part, n: int) -> list[Part]:
    """One MIME attachment -> its Part, plus a Part per file inside it if it is an archive."""
    ct = part.get_content_type()
    name = part.get_filename() or f"attachment-{n + 1}{_guess_ext(ct)}"
    try:
        data = part.get_payload(decode=True) or b""
    except Exception as e:
        return [_unreadable(name, ct, b"", f"attachment payload could not be decoded: {e}", part)]
    return _from_bytes(name, ct, data, part)


# a zip is a stack of attachments; these bound what one attachment can unpack into ("zip bomb")
MAX_ARCHIVE_FILES = 50
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 2


def _from_bytes(name: str, ct: str, data: bytes, part=None, depth: int = 0, inside: str | None = None) -> list[Part]:
    p = _new_part(name, ct, data, part)
    if inside:
        p.notes.append(f"found inside {inside}")
    if not data:
        p.kind = "unreadable"
        p.notes.append("attachment is empty (0 bytes)")
        return [p]
    try:
        if _is_archive(name, ct, data):
            return [p, *_zip(p, data, depth)]
        _read_into(p, name, ct, data)
    except Exception as e:  # any library blowing up on a malformed file lands here
        p.kind = "unreadable"
        p.text = ""
        p.images = []
        p.notes.append(f"could not open {name}: {type(e).__name__}: {e}")
    return [p]


def _read_into(p: Part, name: str, ct: str, data: bytes) -> None:
    ext = Path(name).suffix.lower()
    magic = _sniff(data)
    if magic == "pdf" or ext == ".pdf":
        return _pdf(p, data)
    if magic == "image" or ct.startswith("image/"):
        return _image(p, name, data)
    if magic == "zip":
        # Office files are zips: decide by what's inside, not by a name that may be wrong or missing
        office = _ooxml_kind(data)
        if office == "xlsx" or (office is None and (ext in (".xlsx", ".xlsm") or "spreadsheetml" in ct)):
            return _xlsx(p, data)
        if office == "docx" or (office is None and (ext == ".docx" or "wordprocessingml" in ct)):
            return _docx(p, data)
    if magic == "ole" and ext not in (".doc", ".msg"):
        # legacy .xls (also when misnamed); xlrd raises if this compound file is not a workbook
        try:
            return _xls(p, data)
        except Exception as e:
            if ext == ".xls" or "ms-excel" in ct:
                raise
            raise ValueError(f"legacy binary Office file ({ext or ct}) is not a workbook and is not read: {e}")
    if ext in (".csv", ".tsv") or ct in ("text/csv", "text/tab-separated-values"):
        return _csv(p, data, "\t" if ext == ".tsv" else ",")
    if ext in (".htm", ".html") or ct == "text/html":
        p.text = html_to_text(_decode(data))
        return
    if ext in (".txt", ".eml") or ct.startswith("text/"):
        p.text = _decode(data)
        return
    if magic == "ole" or ext in (".doc", ".msg"):
        # legacy Word and Outlook files: not read (no way to test a reader here); flagged instead
        raise ValueError(f"legacy binary Office/Outlook file ({ext or ct}) is not read automatically")
    raise ValueError(f"unsupported attachment type {ct} ({ext or 'no extension'})")


def _sniff(data: bytes) -> str | None:
    if data[:4] == b"%PDF" or b"%PDF" in data[:1024]:
        return "pdf"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "ole"  # legacy Office / Outlook compound file
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or data[:4] in (b"GIF8", b"II*\x00", b"MM\x00*") or data[:2] == b"BM" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
        return "image"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif"):
        return "image"  # iPhone HEIC / HEIF / AVIF
    return None


def _ooxml_kind(data: bytes) -> str | None:
    import zipfile

    try:
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except zipfile.BadZipFile:
        return None
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "word/document.xml" in names:
        return "docx"
    return None


def _is_archive(name: str, ct: str, data: bytes) -> bool:
    if _sniff(data) != "zip":
        return False
    return _ooxml_kind(data) is None and (
        Path(name).suffix.lower() in (".zip", "") or "zip" in ct or ct == "application/octet-stream")


def _zip(p: Part, data: bytes, depth: int) -> list[Part]:
    """Unpack a zip into one Part per file. The archive's own Part just lists what it held."""
    import zipfile

    p.kind = "skipped"
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = [i for i in zf.infolist() if not i.is_dir()
             and not i.filename.startswith("__MACOSX/") and Path(i.filename).name not in (".DS_Store", "Thumbs.db")]
    p.notes.append(f"zip archive with {len(infos)} file(s); each follows as its own attachment")
    if depth >= MAX_ARCHIVE_DEPTH:
        p.kind = "unreadable"
        p.notes.append(f"archive nested more than {MAX_ARCHIVE_DEPTH} deep; not unpacked")
        return []
    if len(infos) > MAX_ARCHIVE_FILES or sum(i.file_size for i in infos) > MAX_ARCHIVE_BYTES:
        p.kind = "unreadable"
        p.notes.append(f"archive too large to unpack safely ({len(infos)} files, "
                       f"{sum(i.file_size for i in infos):,} bytes uncompressed)")
        return []
    names = [Path(i.filename).name for i in infos]
    out = []
    for info, base in zip(infos, names):
        member = base if names.count(base) == 1 else info.filename  # keep paths only to disambiguate
        if info.flag_bits & 0x1:
            out.append(_unreadable(member, "application/octet-stream", b"",
                                   f"inside {p.filename}: password-protected, cannot be opened"))
            continue
        try:
            body = zf.read(info)
        except Exception as e:
            out.append(_unreadable(member, "application/octet-stream", b"", f"inside {p.filename}: could not extract: {e}"))
            continue
        out.extend(_from_bytes(member, _mime_for(member), body, depth=depth + 1, inside=p.filename))
    return out


def _mime_for(name: str) -> str:
    import mimetypes

    return mimetypes.guess_type(name)[0] or "application/octet-stream"




def _guess_ext(ct: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg", "application/pdf": ".pdf", "text/csv": ".csv"}.get(ct, "")


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


# ---- spreadsheets


def _xlsx(p: Part, data: bytes) -> None:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    out = []
    for ws in wb.worksheets:
        head = f"=== Sheet: {ws.title!r}"
        if ws.sheet_state != "visible":
            head += f"  [{ws.sheet_state.upper()} SHEET]"
            p.notes.append(f"sheet {ws.title!r} is {ws.sheet_state}")
        out.append(head)
        merged = [str(r) for r in ws.merged_cells.ranges]
        if merged:
            out.append(f"(merged cells: {', '.join(merged)})")
        hidden_cols = {k for k, d in ws.column_dimensions.items() if d.hidden}
        rows = 0
        for row in ws.iter_rows():
            cells = []
            for c in row:
                if c.value is None:
                    continue
                v = _cell(c.value)
                if c.font is not None and c.font.strike:
                    v = f"{STRIKE_L}{v}{STRIKE_R}"
                if c.column_letter in hidden_cols:
                    v = f"[hidden column] {v}"
                if c.comment is not None:
                    v += f" [cell comment: {c.comment.text.strip()}]"
                cells.append(v)
            if not cells:
                continue
            r = row[0].row
            row_text = " | ".join(cells)
            line = f"R{r}: {row_text}"
            if ws.row_dimensions[r].hidden:
                line += "   [HIDDEN ROW]"
            if ws.sheet_state != "visible" or ws.row_dimensions[r].hidden:
                p.hidden_rows.append(row_text)
            else:
                p.visible_rows.append(row_text)
            out.append(line)
            rows += 1
            if rows >= MAX_SHEET_ROWS:
                p.notes.append(f"sheet {ws.title!r} truncated at {MAX_SHEET_ROWS} rows")
                out.append(f"[... truncated at {MAX_SHEET_ROWS} rows]")
                break
        if hidden_cols:
            p.notes.append(f"sheet {ws.title!r} has hidden columns {sorted(hidden_cols)}")
        out.append("")
    p.text = "\n".join(out).strip()


def _xls(p: Part, data: bytes) -> None:
    """Legacy Excel 97-2003, rendered exactly like _xlsx: sheet state, merges, strike, hidden rows."""
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data, formatting_info=True)
        fmt = True
    except Exception:  # some writers produce files whose formatting records xlrd rejects
        book = xlrd.open_workbook(file_contents=data)
        fmt = False
        p.notes.append("formatting (strikethrough, hidden rows) could not be read from this .xls")
    out = []
    for sh in book.sheets():
        state = {0: "visible", 1: "hidden", 2: "veryHidden"}.get(sh.visibility, "visible")
        head = f"=== Sheet: {sh.name!r}"
        if state != "visible":
            head += f"  [{state.upper()} SHEET]"
            p.notes.append(f"sheet {sh.name!r} is {state}")
        out.append(head)
        if fmt and sh.merged_cells:
            out.append("(merged cells: " + ", ".join(
                f"{xlrd.formula.cellnameabs(r0, c0).replace('$', '')}:{xlrd.formula.cellnameabs(r1 - 1, c1 - 1).replace('$', '')}"
                for r0, r1, c0, c1 in sh.merged_cells) + ")")
        hidden_cols = {c for c, info in (sh.colinfo_map.items() if fmt else []) if info.hidden}
        rows = 0
        for r in range(sh.nrows):
            cells = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    continue
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = xlrd.xldate.xldate_as_datetime(v, book.datemode)
                v = _cell(v)
                if not v:
                    continue
                if fmt and book.font_list[book.xf_list[sh.cell_xf_index(r, c)].font_index].struck_out:
                    v = f"{STRIKE_L}{v}{STRIKE_R}"
                if c in hidden_cols:
                    v = f"[hidden column] {v}"
                cells.append(v)
            if not cells:
                continue
            row_text = " | ".join(cells)
            hidden_row = fmt and r in sh.rowinfo_map and sh.rowinfo_map[r].hidden
            line = f"R{r + 1}: {row_text}" + ("   [HIDDEN ROW]" if hidden_row else "")
            (p.hidden_rows if state != "visible" or hidden_row else p.visible_rows).append(row_text)
            out.append(line)
            rows += 1
            if rows >= MAX_SHEET_ROWS:
                p.notes.append(f"sheet {sh.name!r} truncated at {MAX_SHEET_ROWS} rows")
                out.append(f"[... truncated at {MAX_SHEET_ROWS} rows]")
                break
        if hidden_cols:
            p.notes.append(f"sheet {sh.name!r} has hidden columns {sorted(hidden_cols)}")
        out.append("")
    p.text = "\n".join(out).strip()


def _cell(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v).strip()


def _csv(p: Part, data: bytes, delim: str) -> None:
    text = _decode(data)
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delim))
        p.text = "\n".join(" | ".join(c.strip() for c in r) for r in rows if any(c.strip() for c in r))
    except csv.Error:
        p.text = text


# ---- Word


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(p: Part, data: bytes) -> None:
    import docx

    doc = docx.Document(io.BytesIO(data))
    out = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.replace(_W, "")
        if tag == "p":
            text = _w_par_text(child)
            if not text.strip():
                continue
            style = _w_style(child)
            if style and (style.lower().startswith("heading") or style.lower() == "title"):
                text = f"# {text}"
            out.append(text)
        elif tag == "tbl":
            out.append("[table]")
            for tr in child.iter(f"{_W}tr"):
                cells = []
                for tc in tr.iterchildren(f"{_W}tc"):
                    cells.append(" ".join(_w_par_text(pp) for pp in tc.iter(f"{_W}p")).strip())
                if any(cells):
                    out.append(" | ".join(cells))
            out.append("[/table]")
    if any(STRIKE_L in line for line in out):
        p.notes.append("document contains struck-through text")
    p.text = "\n".join(out)


def _w_style(par) -> str | None:
    ps = par.find(f"{_W}pPr/{_W}pStyle")
    return ps.get(f"{_W}val") if ps is not None else None


def _w_par_text(par) -> str:
    """Paragraph text, keeping tracked insertions, dropping tracked deletions, marking strikes."""
    pieces = []
    for r in par.iter(f"{_W}r"):
        if any(a.tag == f"{_W}del" for a in r.iterancestors()):
            continue
        t = "".join(
            (x.text or "") if x.tag == f"{_W}t" else ("\t" if x.tag == f"{_W}tab" else "")
            for x in r.iterchildren()
        )
        if not t:
            continue
        rpr = r.find(f"{_W}rPr")
        if rpr is not None and any(
            (e := rpr.find(f"{_W}{s}")) is not None and e.get(f"{_W}val") not in ("0", "false")
            for s in ("strike", "dstrike")
        ):
            t = f"{STRIKE_L}{t}{STRIKE_R}"
        pieces.append(t)
    return "".join(pieces).replace(f"{STRIKE_R}{STRIKE_L}", "")


# ---- PDF


def _pdf(p: Part, data: bytes) -> None:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.needs_pass and not doc.authenticate(""):
        p.kind = "unreadable"
        p.notes.append("PDF is password-protected and no password is available")
        return
    n = doc.page_count
    if n > MAX_PDF_PAGES:
        p.notes.append(f"PDF has {n} pages; only the first {MAX_PDF_PAGES} were read")
    pages = []  # (page no, lines, images)
    for i in range(min(n, MAX_PDF_PAGES)):
        page = doc[i]
        text = _pdf_page_lines(page)
        img_area = _image_area_ratio(page)
        lines, imgs = [], []
        if len(text.strip()) < SCAN_TEXT_CHARS and img_area > 0 or img_area > 0.4:
            pix = page.get_pixmap(dpi=150)
            label = f"{p.filename} page {i + 1}"
            try:  # a scan is a photo: JPEG is ~5x smaller than PNG at the same legibility
                img, mime = pix.tobytes("jpg", jpg_quality=85), "image/jpeg"
            except Exception:
                img, mime = pix.tobytes("png"), "image/png"
            imgs.append(Image(label, img, mime))
            lines.append(f"--- page {i + 1}: scanned / pictured content, see image '{label}'")
            if text.strip():
                lines.append(text)
        else:
            lines += [f"--- page {i + 1}", text]
        pages.append((i + 1, lines, imgs))

    # windows: up to PDF_WINDOW_PAGES pages and PDF_WINDOW_IMAGES scanned pages each
    groups: list[list] = [[]]
    for pg in pages:
        g = groups[-1]
        if g and (len(g) >= PDF_WINDOW_PAGES or sum(len(x[2]) for x in g) + len(pg[2]) > PDF_WINDOW_IMAGES):
            groups.append(g := [])
        g.append(pg)
    first, rest = groups[0], groups[1:]
    out = [ln for _, lines, _ in first for ln in lines]
    p.images = [im for _, _, imgs in first for im in imgs]
    for g in rest:
        p.windows.append({"pages": f"{g[0][0]}-{g[-1][0]}",
                          "text": "\n".join(ln for _, lines, _ in g for ln in lines).strip(),
                          "images": [im for _, _, imgs in g for im in imgs]})
    n_scans = sum(len(imgs) for _, _, imgs in pages)
    if n_scans:
        p.notes.append(f"{n_scans} page(s) are scans, sent as images")
    if any(STRIKE_L in ln for _, lines, _ in pages for ln in lines):
        p.notes.append("PDF contains struck-through text")
    if rest:
        p.notes.append(f"PDF has {len(pages)} pages, read in {len(groups)} parts: this part is pages "
                       f"{first[0][0]}-{first[-1][0]}; " + ", ".join(w["pages"] for w in p.windows)
                       + " are read by separate calls and merged by the system, so they are not "
                       "missing: don't flag the email for review because of them")
    p.text = "\n".join(out).strip()
    if not p.text and not p.images:
        p.kind = "unreadable"
        p.notes.append("PDF contains no text and no images")


def _image_area_ratio(page) -> float:
    area = abs(page.rect)
    if not area:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        covered += abs(page.rect & info["bbox"]) if "bbox" in info else 0
    return min(covered / area, 1.0)


def _pdf_page_lines(page) -> str:
    """Rebuild visual rows from word boxes so a table row reads as one line: a | b | c."""
    words = page.get_text("words")  # x0, y0, x1, y1, word, block, line, wordno
    if not words:
        return ""
    struck = _pdf_strike_boxes(page)
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines: list[list] = []
    for w in words:
        yc = (w[1] + w[3]) / 2
        if lines and abs(lines[-1][0] - yc) <= max(2.5, (w[3] - w[1]) * 0.35):
            lines[-1][1].append(w)
        else:
            lines.append([yc, [w]])
    out = []
    for _, ws in lines:
        ws.sort(key=lambda w: w[0])
        s, prev = "", None
        for w in ws:
            token = w[4]
            if any(_strikes(b, w) for b in struck):
                token = f"{STRIKE_L}{token}{STRIKE_R}"
            if prev is not None:
                char_w = (prev[2] - prev[0]) / max(len(prev[4]), 1)
                s += " | " if w[0] - prev[2] > max(char_w * 2.2, 8) else " "
            s += token
            prev = w
        out.append(s.replace(f"{STRIKE_R} {STRIKE_L}", " "))
    return "\n".join(out)


def _pdf_strike_boxes(page) -> list:
    """Strikeout annotations, plus thin horizontal lines drawn through the middle of text."""
    boxes = []
    for a in page.annots() or []:
        if a.type[1] == "StrikeOut":
            boxes.append(("annot", a.rect))
    try:
        for d in page.get_drawings():
            for item in d.get("items", []):
                if item[0] == "l":
                    p1, p2 = item[1], item[2]
                    if abs(p1.y - p2.y) < 1.0 and abs(p2.x - p1.x) > 15:
                        boxes.append(("line", (min(p1.x, p2.x), p1.y, max(p1.x, p2.x))))
                elif item[0] == "re":
                    r = item[1]
                    if r.height < 1.5 and r.width > 15:
                        boxes.append(("line", (r.x0, (r.y0 + r.y1) / 2, r.x1)))
    except Exception:
        pass
    return boxes


def _strikes(box, w) -> bool:
    kind, b = box
    x0, y0, x1, y1 = w[:4]
    if kind == "annot":
        return b.x0 <= (x0 + x1) / 2 <= b.x1 and b.y0 <= (y0 + y1) / 2 <= b.y1
    lx0, ly, lx1 = b
    h = y1 - y0
    # a strike line crosses the middle band of the word; table borders sit at top/bottom
    return lx0 <= (x0 + x1) / 2 <= lx1 and y0 + 0.3 * h <= ly <= y1 - 0.3 * h


# ---- images


def _image(p: Part, name: str, data: bytes) -> None:
    from PIL import Image as PILImage
    from PIL import ImageOps

    try:  # iPhone photos arrive as HEIC; the opener is optional so a missing wheel never breaks ingest
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except Exception:
        pass

    im = PILImage.open(io.BytesIO(data))
    im.load()  # forces a full decode: truncated/corrupt files fail here, not at the model
    frames = getattr(im, "n_frames", 1)
    if max(im.size) < MIN_IMAGE_SIDE:
        p.kind = "skipped"
        p.notes.append(f"image is {im.size[0]}x{im.size[1]} px (spacer/tracking pixel), ignored")
        return
    p.kind = "image"
    fmt = (im.format or "").upper()
    if fmt == "MPO":  # iPhone "JPEG" with a second (depth) frame: one photo, not two pages
        frames = 1
    orientation = im.getexif().get(0x0112, 1) if frames == 1 else 1
    rotated = orientation not in (1, None)

    # 1. a JPEG/PNG the model accepts, upright and small enough: send the original bytes
    #    (re-encoding a phone JPEG as PNG makes it ~6x bigger for no gain)
    if frames == 1 and not rotated and fmt in ("JPEG", "PNG") and max(im.size) <= MAX_IMAGE_SIDE:
        p.images.append(Image(name, data, "image/jpeg" if fmt == "JPEG" else "image/png"))
        p.notes.append(f"image {im.size[0]}x{im.size[1]} px")
        return

    # 2. a single photo that needs work: sideways (a phone stores it with a rotation tag, which
    #    we apply so the model reads it upright), too big, or a format the model may not take
    #    (iPhone HEIC). Photos stay JPEG; drawings and screenshots stay PNG.
    if frames == 1:
        if rotated:
            im = ImageOps.exif_transpose(im)
            p.notes.append(f"rotated upright from the camera's orientation tag ({orientation})")
        photo = fmt in ("JPEG", "MPO", "HEIF", "HEIC", "AVIF", "WEBP")
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG" if photo else "PNG", **({"quality": 88} if photo else {}))
        p.images.append(Image(name, buf.getvalue(), "image/jpeg" if photo else "image/png"))
        p.notes.append(f"{fmt} image {im.size[0]}x{im.size[1]} px" + (", converted to JPEG" if photo and fmt != "JPEG" else ""))
        return

    # 3. several frames: a multi-page TIFF fax, one image per page
    for f in range(min(frames, 10)):  # multi-page TIFF (faxes)
        if frames > 1:
            im.seek(f)
        buf = io.BytesIO()
        fr = im.convert("RGB") if im.mode not in ("RGB", "L") else im.copy()
        fr.save(buf, "PNG")
        label = name if frames == 1 else f"{name} page {f + 1}"
        img_bytes, mime = _shrink(buf.getvalue(), "image/png")
        p.images.append(Image(label, img_bytes, mime))
    p.notes.append(f"image {im.size[0]}x{im.size[1]} px")


def _shrink(png: bytes, mime: str) -> tuple[bytes, str]:
    from PIL import Image as PILImage

    im = PILImage.open(io.BytesIO(png))
    if max(im.size) <= MAX_IMAGE_SIDE:
        return png, mime
    im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue(), "image/png"


# --------------------------------------------------------------------------- HTML -> text


_STRIKE_TAGS = {"s", "strike", "del"}
_BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "blockquote", "hr"}


def _html_has_strike(html: str) -> bool:
    return bool(re.search(r"<(s|strike|del)\b|line-through", html, re.I))


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.strike_stack: list[bool] = []
        self.cell_open = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script", "head", "title"):
            self.skip += 1
        if tag in _BLOCK_TAGS:
            self.out.append("\n")
        if tag in ("td", "th"):
            if self.cell_open:
                self.out.append(" | ")
            self.cell_open = True
        if tag == "tr":
            self.cell_open = False
        if tag == "blockquote":
            self.out.append("> ")
        if tag == "img":
            src = a.get("src") or ""
            alt = a.get("alt") or ""
            self.out.append(f"[image: {src.removeprefix('cid:')}{' ' + alt if alt else ''}]")
        struck = tag in _STRIKE_TAGS or "line-through" in (a.get("style") or "").replace(" ", "")
        if tag not in ("br", "img", "hr", "meta", "link", "input"):
            self.strike_stack.append(struck)
            if struck:
                self.out.append(STRIKE_L)

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self.skip = max(0, self.skip - 1)
        if tag not in ("br", "img", "hr", "meta", "link", "input") and self.strike_stack:
            if self.strike_stack.pop():
                self.out.append(STRIKE_R)
        if tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    h = _HTMLText()
    try:
        h.feed(html)
        h.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(h.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.replace(f"{STRIKE_L}{STRIKE_R}", "").strip()


# --------------------------------------------------------------------------- debug view


def render(em: Email) -> str:
    """Human-readable dump of exactly what downstream steps will see."""
    lines = [
        f"### {em.id}",
        f"From: {em.sender}",
        f"To: {em.to}" + (f"   Cc: {em.cc}" if em.cc else ""),
        f"Date: {em.date}",
        f"Subject: {em.subject}",
        f"Message-ID: {em.message_id}   In-Reply-To: {em.in_reply_to}",
        *([f"Auto-Submitted: {em.auto_submitted}"] if em.auto_submitted else []),
        *([f"Precedence: {em.precedence}"] if em.precedence else []),
        f"(body read from {em.body_source})",
        "",
        em.body.strip(),
    ]
    for p in em.parts:
        lines += ["", f"--- ATTACHMENT {p.filename!r} ({p.mime}, {p.size} bytes, kind={p.kind}"
                  + (", inline" if p.inline else "") + ")"]
        lines += [f"    note: {n}" for n in p.notes]
        if p.text:
            lines.append(p.text)
        lines += [f"    [IMAGE: {im.label}, {len(im.data)} bytes]" for im in p.images]
    if em.errors:
        lines += ["", *[f"!! {e}" for e in em.errors]]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for arg in sys.argv[1:]:
        target = Path(arg)
        for f in sorted(target.glob("*.eml")) if target.is_dir() else [target]:
            print(render(load_email(f)))
            print("\n" + "=" * 100 + "\n")
