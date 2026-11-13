"""Attachment types the dev inbox doesn't show, generated here: zip archives (nested, bombs,
passwords), Office files with a wrong or missing extension, legacy .xls, iPhone HEIC photos,
sideways phone photos, oversized photos, and the legacy types we deliberately don't read.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from PIL import Image as PILImage

from rfq.ingest import MAX_ARCHIVE_FILES, MAX_IMAGE_SIDE, STRIKE_L, load_email
from tests.test_ingest import _eml

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_bytes() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "BOM"
    ws.append(["Item", "Qty", "Description"])
    ws.append([1, 12, '2" 90 elbow SCH 40 BW'])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
    return buf.getvalue()


def _jpeg(w=400, h=200, orientation=None) -> bytes:
    im = PILImage.new("RGB", (w, h), "white")
    buf = io.BytesIO()
    exif = PILImage.Exif()
    if orientation:
        exif[0x0112] = orientation
    im.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _parts(tmp_path, attachments):
    return {p.filename: p for p in load_email(_eml(tmp_path, attachments)).parts}


# ---- zip


def test_zip_members_become_their_own_attachments(tmp_path):
    z = _zip({"drawings/BOM.xlsx": _xlsx_bytes(), "notes.csv": b"Qty,Item\n4,2\" cap\n", "__MACOSX/._BOM.xlsx": b"junk"})
    parts = _parts(tmp_path, [("job.zip", "application/zip", z)])
    assert set(parts) == {"job.zip", "BOM.xlsx", "notes.csv"}
    assert parts["job.zip"].kind == "skipped"
    assert '12 | 2" 90 elbow SCH 40 BW' in parts["BOM.xlsx"].text
    assert "found inside job.zip" in parts["BOM.xlsx"].notes


def test_zip_inside_zip_is_unpacked_to_a_limit(tmp_path):
    inner = _zip({"list.csv": b"Qty,Item\n1,tee\n"})
    parts = _parts(tmp_path, [("outer.zip", "application/zip", _zip({"inner.zip": inner}))])
    assert "list.csv" in parts and parts["list.csv"].kind == "text"
    deep = _zip({"a.zip": _zip({"b.zip": _zip({"c.csv": b"x,y\n1,2\n"})})})
    parts = _parts(tmp_path, [("deep.zip", "application/zip", deep)])
    assert "c.csv" not in parts and any(p.kind == "unreadable" for p in parts.values())


def test_zip_bomb_is_refused_not_unpacked(tmp_path):
    z = _zip({f"f{i}.txt": b"x" for i in range(MAX_ARCHIVE_FILES + 1)})
    parts = _parts(tmp_path, [("many.zip", "application/zip", z)])
    assert list(parts) == ["many.zip"] and parts["many.zip"].kind == "unreadable"


def test_password_protected_zip_member_is_flagged(tmp_path):
    z = bytearray(_zip({"secret.xlsx": _xlsx_bytes()}))
    # set the "encrypted" flag bit in the local and central headers (zipfile can't write encryption)
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = z.find(sig)
        z[i + off] |= 0x01
    parts = _parts(tmp_path, [("locked.zip", "application/zip", bytes(z))])
    assert parts["secret.xlsx"].kind == "unreadable"
    assert "password" in " ".join(parts["secret.xlsx"].notes)


def test_office_file_with_wrong_or_no_extension_is_read_by_content(tmp_path):
    parts = _parts(tmp_path, [("BOM", "application/octet-stream", _xlsx_bytes()),
                              ("export.dat", "application/octet-stream", _xlsx_bytes())])
    assert all('2" 90 elbow' in p.text for p in parts.values())


# ---- legacy Excel


def test_legacy_xls_keeps_hidden_sheets_rows_and_strike(tmp_path):
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Rev C")
    struck = xlwt.easyxf("font: struck_out on")
    ws.write(0, 0, "ALL PIPE SCH 40")
    for c, v in enumerate([1, 10, '2" pipe']):
        ws.write(1, c, v)
    for c, v in enumerate([2, 4, '3" pipe']):
        ws.write(2, c, v, struck)
    for c, v in enumerate([3, 6, '4" pipe']):
        ws.write(3, c, v)
    ws.row(3).hidden = True
    old = wb.add_sheet("Rev B")
    old.write(0, 0, '6" pipe')
    old.visibility = 1
    buf = io.BytesIO()
    wb.save(buf)
    (p,) = _parts(tmp_path, [("bom.xls", "application/vnd.ms-excel", buf.getvalue())]).values()
    assert p.kind == "text"
    assert '1 | 10 | 2" pipe' in p.visible_rows
    assert f'{STRIKE_L}3" pipe' in p.text
    assert "[HIDDEN ROW]" in p.text and '3 | 6 | 4" pipe' in p.hidden_rows
    assert "[HIDDEN SHEET]" in p.text and '6" pipe' in p.hidden_rows


# ---- photos


def test_iphone_heic_photo_is_read(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    buf = io.BytesIO()
    PILImage.new("RGB", (640, 480), "white").save(buf, "HEIF")
    (p,) = _parts(tmp_path, [("IMG_1234.HEIC", "image/heic", buf.getvalue())]).values()
    assert p.kind == "image" and p.images[0].mime == "image/jpeg"


def test_sideways_phone_photo_is_turned_upright(tmp_path):
    (p,) = _parts(tmp_path, [("IMG_2.jpg", "image/jpeg", _jpeg(400, 200, orientation=6))]).values()
    im = PILImage.open(io.BytesIO(p.images[0].data))
    assert im.size == (200, 400), "orientation 6 = rotate 90: width and height swap"
    assert any("rotated upright" in n for n in p.notes)


def test_upright_small_jpeg_is_sent_byte_for_byte(tmp_path):
    data = _jpeg()
    (p,) = _parts(tmp_path, [("IMG_3.jpg", "image/jpeg", data)]).values()
    assert p.images[0].data == data  # unchanged bytes keep the replay cache valid


def test_oversized_photo_is_downscaled_and_stays_jpeg(tmp_path):
    (p,) = _parts(tmp_path, [("IMG_4.jpg", "image/jpeg", _jpeg(4032, 3024))]).values()
    im = PILImage.open(io.BytesIO(p.images[0].data))
    assert max(im.size) <= MAX_IMAGE_SIDE and p.images[0].mime == "image/jpeg"


# ---- types deliberately not read: flagged, with a reason


@pytest.mark.parametrize("name, mime", [("spec.doc", "application/msword"), ("fwd.msg", "application/vnd.ms-outlook")])
def test_legacy_word_and_outlook_files_are_flagged(tmp_path, name, mime):
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600
    (p,) = _parts(tmp_path, [(name, mime, ole)]).values()
    assert p.kind == "unreadable" and "not read automatically" in " ".join(p.notes)
