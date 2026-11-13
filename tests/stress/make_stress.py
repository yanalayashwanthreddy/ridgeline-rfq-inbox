"""Generate a stress inbox: situations the dev inbox doesn't show, with expected answers.

    python tests/stress/make_stress.py      -> tests/stress/emails/*.eml + tests/stress/answer_key.json

The expected answers were written BEFORE the system was run on these emails, so they test the
design rather than describe its output. Same answer-key format as data/dev, so
scripts/score.py works on it unchanged.
"""

from __future__ import annotations

import io
import json
import shutil
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "emails"
RL = "Ridgeline RFQ Desk <rfq@ridgeline-supply.example>"

emails: dict[str, bytes] = {}
key: dict[str, dict] = {}


def msg(eid, frm, subject, date, body, *, html=None, atts=(), headers=None, to=RL):
    m = EmailMessage()
    m["From"] = frm
    m["To"] = to
    m["Subject"] = subject
    m["Date"] = date
    m["Message-ID"] = f"<{eid.lower()}@stress.example>"
    for k, v in (headers or {}).items():
        m[k] = v
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    for name, mime, data in atts:
        mt, st = mime.split("/")
        m.add_attachment(data, maintype=mt, subtype=st, filename=name)
    emails[eid] = m.as_bytes()


def expect(eid, cls, lines=(), review=False, revises=None, dup=None):
    key[eid] = {
        "classification": cls, "revises": revises, "duplicate_of": dup, "needs_review": review,
        "line_items": [
            {"id": f"{eid}-{i}", "raw": raw, "qty": qty, "qty_tol": 0, "uom": uom, "source": src,
             "must_mention": must, "must_not_mention": mustnt}
            for i, (raw, qty, uom, src, must, mustnt) in enumerate(lines, 1)
        ],
    }


def L(raw, qty, uom="EA", src="body", must=(), mustnt=()):
    return (raw, qty, uom, src, list(must), list(mustnt))


# --- S01 out-of-office with no Auto-Submitted header (many real ones have none)
msg("S01", "Hank Morrow <hmorrow@morrowpiping.example>", "Automatic reply: Q-26-04490 pricing",
    "Mon, 02 Nov 2026 08:00:00 -0600",
    "I am out of the office until Monday, November 30 with limited access to email.\n"
    "For urgent orders please contact Lisa Tran at ltran@morrowpiping.example or 504-555-0110.\n\nHank Morrow")
expect("S01", "auto_reply")

# --- S02 items identified only by an earlier order -> review, keep the one readable line
msg("S02", "Bea Holloway <bholloway@crescentmech.example>", "repeat order + blinds",
    "Mon, 02 Nov 2026 09:10:00 -0600",
    "Hi Marcus,\n\nCan you quote the same material as our PO 7781 from August, but double all the "
    "quantities? Also add 10 ea 2\" 150# RF blind flange, A105.\n\nThanks,\nBea Holloway\nCrescent Mechanical")
expect("S02", "rfq", [L('10 ea 2" 150# RF blind flange, A105', 10, must=["blind", "a105"])], review=True)


# --- S03 Word file with tracked changes: a deleted row and a changed quantity
def tracked_docx() -> bytes:
    import docx
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    d = docx.Document()
    d.add_heading("Valves - 800# A105 socket weld", level=2)
    t = d.add_table(rows=4, cols=3)
    rows = [("Qty", "Size", "Item"), ("10", '1"', "Gate valve"), ("4", '3/4"', "Globe valve"), ("6", '1"', "Check valve, swing")]
    for r, vals in enumerate(rows):
        for c, v in enumerate(vals):
            t.cell(r, c).text = v
    # row 1 qty: 10 deleted, 12 inserted
    p = t.cell(1, 0).paragraphs[0]
    for r in list(p.runs):
        r._r.getparent().remove(r._r)
    p._p.append(parse_xml(f'<w:del {nsdecls("w")} w:id="1" w:author="Ana" w:date="2026-11-01T10:00:00Z"><w:r><w:delText>10</w:delText></w:r></w:del>'))
    p._p.append(parse_xml(f'<w:ins {nsdecls("w")} w:id="2" w:author="Ana" w:date="2026-11-01T10:00:00Z"><w:r><w:t>12</w:t></w:r></w:ins>'))
    # row 2 deleted entirely
    for c in range(3):
        p = t.cell(2, c).paragraphs[0]
        txt = p.text
        for r in list(p.runs):
            r._r.getparent().remove(r._r)
        p._p.append(parse_xml(f'<w:del {nsdecls("w")} w:id="{10 + c}" w:author="Ana" w:date="2026-11-01T10:00:00Z"><w:r><w:delText>{txt.replace(chr(34), "&quot;")}</w:delText></w:r></w:del>'))
    d.add_paragraph("Ship to Plant 3 receiving.")
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


msg("S03", "Ana Ferreira <aferreira@deltaforge.example>", "RFQ - valves for line 3",
    "Mon, 02 Nov 2026 10:00:00 -0600", "Please quote the attached (I left my edits tracked, sorry).\n\nAna",
    atts=[("Line3_Valves.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", tracked_docx())])
expect("S03", "rfq", [
    L('12 | 1" | Gate valve', 12, src="Line3_Valves.docx", must=["gate", "800", "a105", "socket weld|sw"]),
    L('6 | 1" | Check valve, swing', 6, src="Line3_Valves.docx", must=["check", "800", "a105", "socket weld|sw"]),
])

# --- S04 rep forwards a customer email inline and adds a line of their own
msg("S04", "Jim Pratt <jim.pratt@ridgeline-supply.example>", "Fwd: quote request - Mesa tie-in",
    "Mon, 02 Nov 2026 11:30:00 -0600",
    "RFQ desk - from Carla at Mesa. She also needs 8 sets of 4\" 300# spiral wound gaskets, "
    "316/graphite - please add those.\n\nJim\n\n---------- Forwarded message ---------\n"
    "From: Carla Ruiz <cruiz@mesafab.example>\nDate: Mon, Nov 2, 2026 at 9:02 AM\nSubject: quote request - Mesa tie-in\n"
    "To: Jim Pratt <jim.pratt@ridgeline-supply.example>\n\nJim,\n\nPlease quote:\n"
    "- 6 ea 4\" 300# RF weld neck flange, A105, SCH 80 bore\n- 2 ea 4\" 300# RF blind flange, A105\n\nThanks\nCarla")
expect("S04", "rfq", [
    L('- 6 ea 4" 300# RF weld neck flange, A105, SCH 80 bore', 6, must=["weld neck", "300#"]),
    L('- 2 ea 4" 300# RF blind flange, A105', 2, must=["blind", "300#"]),
    L('8 sets of 4" 300# spiral wound gaskets, 316/graphite', 8, "SET", must=["spiral wound", "300#"]),
])


# --- S05 supplier invoice with item lines -> other
def text_pdf(lines: list[str], pages: int = 1, per_page=None) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    chunks = [lines] if per_page is None else [lines[i:i + per_page] for i in range(0, len(lines), per_page)]
    for chunk in chunks[:pages] if per_page is None else chunks:
        pg = doc.new_page()
        y = 72
        for ln in chunk:
            pg.insert_text((54, y), ln, fontsize=10)
            y += 16
    return doc.tobytes()


msg("S05", "Accounts Receivable <ar@bayoustainless.example>", "Invoice INV-88412",
    "Mon, 02 Nov 2026 12:00:00 -0600",
    "Please find attached invoice INV-88412 for your PO RL-PO-5531. Amount due $4,212.50, net 30.\n\nBayou Stainless AR",
    atts=[("INV-88412.pdf", "application/pdf", text_pdf([
        "BAYOU STAINLESS SUPPLY - INVOICE INV-88412", "Bill to: Ridgeline Industrial Supply   PO: RL-PO-5531",
        "QTY   DESCRIPTION                              PRICE     AMOUNT",
        "40    2\" 316L SW 3000# 90 elbow                 11.80     472.00",
        "30    2\" 316L SW 3000# coupling                  8.95     268.50",
        "TOTAL DUE 4,212.50"]))])
expect("S05", "other")

# --- S06 "we went with someone else", quoting our quote
msg("S06", "Rick Dunn <rdunn@sabinerefit.example>", "Re: Quote Q-26-04512",
    "Mon, 02 Nov 2026 13:00:00 -0600",
    "Thanks for turning this around fast. We ended up going with another supplier on this one, "
    "maybe next time.\n\nRick\n\n> Quote Q-26-04512\n> 1. 12 EA 3\" 150# RF slip-on flange, A105 - $21.10\n"
    "> 2. 60 FT 3\" SCH 40 A53B ERW pipe, PE - $7.95/ft\n", headers={"In-Reply-To": "<q26-04512@ridgeline-supply.example>"})
expect("S06", "other")

# --- S07 status inquiry with no quote number
msg("S07", "Pat Greer <pgreer@greerboiler.example>", "boiler room RFQ",
    "Mon, 02 Nov 2026 14:00:00 -0600",
    "Hi, just checking you got the RFQ I sent Monday for the boiler room job? Haven't seen a quote yet "
    "and we need to order by Thursday.\n\nPat")
expect("S07", "status_inquiry")


# --- S08 spreadsheet: title default, a struck row, a hidden stale row, an override, a total
def xlsx_s08() -> bytes:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Valves"
    ws["A1"] = "LINE 3 REBUILD - ALL VALVES 800# A105 SW UNLESS NOTED"
    ws.merge_cells("A1:D1")
    ws.append(["Item", "Qty", "Size", "Description"])
    ws.append([1, 6, '1"', "Gate valve"])
    ws.append([2, 3, '1"', "Globe valve"])
    for c in ws[4]:
        c.font = Font(strike=True)
    ws.append([3, 9, '3/4"', "Gate valve (old qty)"])
    ws.row_dimensions[5].hidden = True
    ws.append([4, 4, '3/4"', "Ball valve, 3-pc, 316 SS, 1000 WOG, threaded"])
    ws.append([5, 2, '1"', "Check valve, piston"])
    ws.append([None, "TOTAL", 15])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


s08 = xlsx_s08()
msg("S08", "Omar Haddad <ohaddad@haddadind.example>", "RFQ - line 3 valves", "Mon, 02 Nov 2026 15:00:00 -0600",
    "Quote attached list please.\nOmar", atts=[("Line3.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", s08)])
expect("S08", "rfq", [
    L('1 | 6 | 1" | Gate valve', 6, src="Line3.xlsx", must=["gate", "800", "a105", "socket weld|sw"]),
    L('4 | 4 | 3/4" | Ball valve, 3-pc, 316 SS, 1000 WOG, threaded', 4, src="Line3.xlsx", must=["ball", "316", "1000"], mustnt=["a105", "800"]),
    L('5 | 2 | 1" | Check valve, piston', 2, src="Line3.xlsx", must=["check", "800", "a105"]),
])

# --- S09 truncated (corrupt) spreadsheet
msg("S09", "Lena Fox <lfox@foxcontrols.example>", "BOM for pricing", "Mon, 02 Nov 2026 16:00:00 -0600",
    "BOM attached, need pricing by Friday.\nLena",
    atts=[("BOM.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", s08[: len(s08) // 2])])
expect("S09", "rfq", review=True)

# --- S10 Spanish-language request
msg("S10", "Jorge Salinas <jsalinas@salinasmx.example>", "Cotizacion - material planta Monterrey",
    "Tue, 03 Nov 2026 08:00:00 -0600",
    "Buenas tardes,\n\nFavor de cotizar lo siguiente:\n"
    "- 20 codos de 90 grados de 2\" cedula 40, soldables a tope (BW), A234 WPB\n"
    "- 10 tes rectas de 2\" cedula 40 BW, A234 WPB\n"
    "- 4 bridas cuello soldable de 2\" 150# RF, A105\n\nSaludos,\nJorge Salinas")
expect("S10", "rfq", [
    L('- 20 codos de 90 grados de 2" cedula 40, soldables a tope (BW), A234 WPB', 20, must=["bw|butt weld", "a234|wpb"]),
    L('- 10 tes rectas de 2" cedula 40 BW, A234 WPB', 10, must=["bw|butt weld"]),
    L('- 4 bridas cuello soldable de 2" 150# RF, A105', 4, must=["150#", "a105"]),
])

# --- S11 purchase order in the body; S13 the identical message delivered twice
s11_body = ("Please process PO 4471 for the following, ship best way:\n\n"
            "1) 24 ea 2\" SCH 80 A106B SMLS pipe nipple x 6\" long, TBE\n"
            "2) 12 ea 2\" 3000# forged threaded union, A105\n\nThanks,\nDee Walsh\nWalsh Process")
msg("S11", "Dee Walsh <dwalsh@walshprocess.example>", "PO 4471", "Tue, 03 Nov 2026 09:00:00 -0600", s11_body)
expect("S11", "purchase_order", [
    L('1) 24 ea 2" SCH 80 A106B SMLS pipe nipple x 6" long, TBE', 24, must=["nipple", "sch 80"]),
    L('2) 12 ea 2" 3000# forged threaded union, A105', 12, must=["union", "3000"]),
])
emails["S13"] = emails["S11"]  # byte-identical redelivery (same Message-ID, same Date)
expect("S13", "purchase_order", dup="S11")

# --- S12a original rfq, S12b terse revision ("make #3 twelve and kill #5")
s12a = ("Please quote for the cooling water job:\n\n1) 8 ea 6\" 150# RF slip-on flange, A105\n"
        "2) 8 ea 6\" 150# spiral wound gasket, 304/graphite\n3) 4 ea 6\" butterfly valve, lug, lever\n"
        "4) 64 ea 3/4\" x 4-1/2\" B7 stud bolt w/ 2 hvy hex nuts\n5) 2 ea 6\" Y-strainer, 150# flanged\n\nThanks\nNoel")
msg("S12A", "Noel Brandt <nbrandt@brandtmech.example>", "RFQ - cooling water", "Tue, 03 Nov 2026 10:00:00 -0600", s12a)
expect("S12A", "rfq", [
    L('1) 8 ea 6" 150# RF slip-on flange, A105', 8, must=["slip"]),
    L('2) 8 ea 6" 150# spiral wound gasket, 304/graphite', 8, must=["spiral wound"]),
    L('3) 4 ea 6" butterfly valve, lug, lever', 4, must=["butterfly"]),
    L('4) 64 ea 3/4" x 4-1/2" B7 stud bolt w/ 2 hvy hex nuts', 64, must=["stud"]),
    L('5) 2 ea 6" Y-strainer, 150# flanged', 2, must=["strainer"]),
])
msg("S12B", "Noel Brandt <nbrandt@brandtmech.example>", "Re: RFQ - cooling water", "Tue, 03 Nov 2026 15:00:00 -0600",
    "make #3 twelve and kill #5. rest is fine\n\nNoel\n\n" + "\n".join("> " + x for x in s12a.splitlines()),
    headers={"In-Reply-To": "<s12a@stress.example>", "References": "<s12a@stress.example>"})
expect("S12B", "rfq_revision", [
    L('1) 8 ea 6" 150# RF slip-on flange, A105', 8, must=["slip"]),
    L('2) 8 ea 6" 150# spiral wound gasket, 304/graphite', 8, must=["spiral wound"]),
    L('3) 4 ea 6" butterfly valve, lug, lever', 12, must=["butterfly"]),
    L('4) 64 ea 3/4" x 4-1/2" B7 stud bolt w/ 2 hvy hex nuts', 64, must=["stud"]),
], revises="S12A")

# --- S14 vendor quote as a PDF answering our request
msg("S14", "Quotes <quotes@gulfcoastvalve.example>", "Quotation GCV-55120 for Ridgeline",
    "Tue, 03 Nov 2026 16:00:00 -0600", "Attached is our quotation per your request of 11/2. Valid 30 days.\n\nGulf Coast Valve",
    atts=[("GCV-55120.pdf", "application/pdf", text_pdf([
        "GULF COAST VALVE - QUOTATION GCV-55120", "To: Ridgeline Industrial Supply, attn Jenna Okafor",
        "QTY  DESCRIPTION                                UNIT     LEAD",
        "12   2\" 800# forged gate valve, A105, NPT      $96.00   stock",
        "8    2\" 800# forged globe valve, A105, NPT     $121.00  2 wks"]))])
expect("S14", "vendor_quote")


# --- S15 phone photo of a typed list, rotated 90 degrees
def rotated_photo() -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", (900, 420), (245, 243, 236))
    dr = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=30)
    for i, ln in enumerate(["QTY   ITEM", "10   3/4\" 150# RF slip-on flange, A105", "10   3/4\" 150# spiral wound gasket",
                            "40   1/2\" x 3\" B7 stud w/ 2 nuts"]):
        dr.text((40, 40 + i * 80), ln, fill=(20, 20, 20), font=f)
    im = im.rotate(90, expand=True)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


msg("S15", "Ty Brooks <tbrooks@brooksweld.example>", "pic of list", "Wed, 04 Nov 2026 07:30:00 -0600",
    "price these pls\n\nSent from my phone", atts=[("IMG_0091.jpg", "image/jpeg", rotated_photo())])
expect("S15", "rfq", [
    L('10   3/4" 150# RF slip-on flange, A105', 10, src="IMG_0091.jpg", must=["slip"]),
    L('10   3/4" 150# spiral wound gasket', 10, src="IMG_0091.jpg", must=["spiral wound"]),
    L('40   1/2" x 3" B7 stud w/ 2 nuts', 40, src="IMG_0091.jpg", must=["stud"]),
])

# --- S16 newsletter without a Precedence header
msg("S16", "Valve World News <news@valveworldnews.example>", "November spotlight: new 3-pc ball valve line",
    "Wed, 04 Nov 2026 06:00:00 -0600",
    "NOVEMBER SPOTLIGHT\n\nOur new 3-piece ball valves are in stock:\n"
    "1/2\" 316 SS 3-pc ball valve, 1000 WOG, threaded - $38.50\n1\" 316 SS 3-pc ball valve, 1000 WOG, threaded - $61.20\n\n"
    "Order today! Call 800-555-0199.\nTo unsubscribe click here: https://valveworldnews.example/u?x=1")
expect("S16", "other")

# --- S17 HTML table with a struck-through row
msg("S17", "Mia Chen <mchen@chenhvac.example>", "RFQ - mech room", "Wed, 04 Nov 2026 08:00:00 -0600",
    "Qty Item\n12 1\" copper 90 elbow, wrot, CxC\n6 1\" copper tee, wrot\n4 1\" ball valve, bronze, solder\n",
    html="<p>Please quote:</p><table><tr><th>Qty</th><th>Item</th></tr>"
         "<tr><td>12</td><td>1\" copper 90 elbow, wrot, CxC</td></tr>"
         "<tr><td><s>6</s></td><td><s>1\" copper tee, wrot</s></td></tr>"
         "<tr><td>4</td><td>1\" ball valve, bronze, solder</td></tr></table><p>Mia</p>")
expect("S17", "rfq", [
    L('12 | 1" copper 90 elbow, wrot, CxC', 12, must=["copper"]),
    L('4 | 1" ball valve, bronze, solder', 4, must=["ball", "bronze"]),
])

# --- S18 a long PDF (35 pages, 3 rows each): read in two windows (pages 1-30, 31-35); all 105 lines,
#     no review needed. (Until the windowed reader this expected review=True: only 30 pages were read.)
rows = [f"{i} | {(i % 9) + 1} | EA | {['1/2', '3/4', '1', '1-1/2', '2'][i % 5]}\" | 150# RF slip-on flange, A105, item {i}"
        for i in range(1, 106)]
msg("S18", "Doc Control <doccontrol@bigproject.example>", "RFQ BP-900 full package", "Wed, 04 Nov 2026 09:00:00 -0600",
    "Full RFQ package attached (35 pages). Please quote all items.",
    atts=[("BP-900.pdf", "application/pdf", text_pdf(rows, per_page=3))])
expect("S18", "rfq", [L(r, int(r.split(" | ")[1]), src="BP-900.pdf", must=["slip"]) for r in rows])


# --- S19 a zip holding the BOM spreadsheet (the sheet's heading carries the spec)
def zipped_bom() -> bytes:
    import zipfile

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(["ALL FLANGES 300# RF A105 UNLESS NOTED"])
    ws.append(["ITEM", "QTY", "SIZE", "DESCRIPTION"])
    ws.append([1, 8, '4"', "weld neck flange"])
    ws.append([2, 8, '4"', "blind flange"])
    ws.append([3, 16, '4"', "spiral wound gasket, 316/graphite"])
    x = io.BytesIO()
    wb.save(x)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("Pump 3 tie-in/P3_BOM.xlsx", x.getvalue())
        f.writestr("Pump 3 tie-in/readme.txt", "BOM for pump 3 tie-in. Drawings to follow.")
    return z.getvalue()


msg("S19", "Ana Ruiz <aruiz@deltaprocess.example>", "RFQ pump 3 tie-in", "Thu, 05 Nov 2026 08:10:00 -0600",
    "Hi, BOM is in the zip. Please quote.\n\nAna", atts=[("P3_tiein.zip", "application/zip", zipped_bom())])
expect("S19", "rfq", [
    L('1 | 8 | 4" | weld neck flange', 8, src="P3_BOM.xlsx", must=["weld neck", "300#", "rf|raised face", "A105"]),
    L('2 | 8 | 4" | blind flange', 8, src="P3_BOM.xlsx", must=["blind", "300#", "A105"]),
    L('3 | 16 | 4" | spiral wound gasket, 316/graphite', 16, src="P3_BOM.xlsx", must=["spiral wound", "300#"],
      mustnt=["A105"]),
])


def phone_list(lines, size=(1000, 460)):
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", size, (246, 244, 238))
    dr = ImageDraw.Draw(im)
    f = ImageFont.load_default(size=32)
    for i, ln in enumerate(lines):
        dr.text((40, 40 + i * 85), ln, fill=(25, 25, 25), font=f)
    return im


# --- S20 a phone photo stored sideways with an EXIF rotation tag (pixels rotated, tag says "turn 90")
def exif_sideways() -> bytes:
    from PIL import Image

    im = phone_list(["QTY  ITEM", "6   2\" 3000# A105 threaded union", "12  2\" 3000# A105 threaded 90 elbow",
                     "4   2\" 800# A105 threaded gate valve"]).transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90 CW to display": undoes the ROTATE_90 (CCW) above
    b = io.BytesIO()
    im.save(b, "JPEG", quality=82, exif=exif.tobytes())
    return b.getvalue()


msg("S20", "Rob Kline <rkline@klinemech.example>", "need these", "Thu, 05 Nov 2026 09:20:00 -0600",
    "quote pls\n\nSent from my iPhone", atts=[("IMG_5521.jpg", "image/jpeg", exif_sideways())])
expect("S20", "rfq", [
    L('6   2" 3000# A105 threaded union', 6, src="IMG_5521.jpg", must=["union"]),
    L('12  2" 3000# A105 threaded 90 elbow', 12, src="IMG_5521.jpg", must=["90", "elbow|ell"]),
    L('4   2" 800# A105 threaded gate valve', 4, src="IMG_5521.jpg", must=["gate"]),
])


# --- S21 an iPhone HEIC photo of a list
def heic_photo() -> bytes:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    im = phone_list(["QTY  ITEM", "20  1\" SCH 80 A106B seamless pipe, PE, 21 ft", "10  1\" 3000# A105 SW 90 elbow"])
    b = io.BytesIO()
    im.save(b, "HEIF", quality=80)
    return b.getvalue()


msg("S21", "Jen Ortiz <jortiz@ortizfab.example>", "pipe + ells", "Thu, 05 Nov 2026 10:05:00 -0600",
    "Can you price this?\n\nJen", atts=[("IMG_7730.HEIC", "image/heic", heic_photo())])
expect("S21", "rfq", [
    L('20  1" SCH 80 A106B seamless pipe, PE, 21 ft', 20, src="IMG_7730.HEIC", must=["seamless|smls", "SCH 80"]),
    L('10  1" 3000# A105 SW 90 elbow', 10, src="IMG_7730.HEIC", must=["90", "socket weld|sw"]),
])


# --- S22 a terse revision of a request that is NOT in this inbox and NOT quoted: positions only.
#     Nothing in it is identifiable, so: revision, no lines, a person must find the original.
msg("S22", "Dale Pruitt <dpruitt@pruittind.example>", "Re: RFQ pump skid 7", "Fri, 06 Nov 2026 08:00:00 -0600",
    "Make #2 eight instead of six and kill #4. Rest stays the same.\n\nDale")
expect("S22", "rfq_revision", [], review=True)

# --- S23 the same kind of terse revision, original not in the inbox, but quoted below the reply:
#     the complete order can be rebuilt from the quote (#3 -> 12, #4 removed).
s23_orig = ("Please quote:\n1. 6 ea 3\" 300# RF weld neck flange, A105\n2. 6 ea 3\" 300# spiral wound gasket, 316/graphite\n"
            "3. 10 ea 3\" SCH 80 A234 WPB 90 LR elbow, BW\n4. 4 ea 3\" SCH 80 A234 WPB tee, BW\n\nThanks,\nLena")
msg("S23", "Lena Marsh <lmarsh@marshfab.example>", "RE: RFQ heat exchanger tie-in", "Fri, 06 Nov 2026 09:30:00 -0600",
    "make #3 twelve and kill #4 please\n\nLena\n\nOn Mon, Nov 2, 2026 at 7:10 AM Lena Marsh <lmarsh@marshfab.example> wrote:\n"
    + "\n".join("> " + x for x in s23_orig.splitlines()))
expect("S23", "rfq_revision", [
    L('1. 6 ea 3" 300# RF weld neck flange, A105', 6, must=["weld neck", "300#"]),
    L('2. 6 ea 3" 300# spiral wound gasket, 316/graphite', 6, must=["spiral wound"]),
    L('3. 10 ea 3" SCH 80 A234 WPB 90 LR elbow, BW', 12, must=["90", "SCH 80"]),
])


if __name__ == "__main__":
    import sys

    # Existing emails are never rewritten: MIME boundaries are random, so a rewrite would change
    # every attachment email's bytes and invalidate its cached model answer. --fresh rebuilds all.
    fresh = "--fresh" in sys.argv
    if fresh and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    new = [eid for eid in emails if fresh or not (OUT / f"{eid}.eml").exists()]
    for eid in new:
        (OUT / f"{eid}.eml").write_bytes(emails[eid])
    (HERE / "answer_key.json").write_text(json.dumps({"version": "stress-1", "emails": key}, indent=1), encoding="utf-8")
    print(f"wrote {len(new)} new email(s) to {OUT}: {', '.join(new) or 'none'}")
