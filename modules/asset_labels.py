"""Read-only, millimetre-accurate vector PDF asset labels.

Layout choices and geometry mirror static/asset-label-layout.js. Barcodes keep
the original M:/U: identity; vendor C numbers are secondary item metadata.
"""
from io import BytesIO
from math import isfinite
import json
from pathlib import Path

from flask import Blueprint, request, send_file, jsonify, abort
from common import BASE_DIR, get_db, login_required
from modules.assets import Invalid, row

bp = Blueprint("asset_labels", __name__)


@bp.get("/vendor/pdfjs/<path:resource>")
def pdf_resource(resource):
    """Expose only pinned public PDF.js assets, never the client source tree."""
    frontend = Path(BASE_DIR) / "clients" / "frontend"
    locked = json.loads((frontend / "vendor-lock.json").read_text(encoding="utf-8"))["files"]
    name = "pdfjs/" + resource
    root = (frontend / "vendor" / "pdfjs").resolve()
    target = (root / resource).resolve()
    if name not in locked or not target.is_relative_to(root) or not target.is_file():
        abort(404)
    return send_file(target, mimetype="text/javascript" if resource.endswith(".mjs") else None, max_age=86400)


def _clamp(value, low, high):
    return max(low, min(high, value))


def label_geometry(width, height, preferred="auto"):
    """Return rectangles measured from the label's top-left, in millimetres."""
    w, h = float(width), float(height)
    if not isfinite(w) or not isfinite(h) or not 15 <= w <= 190 or not 10 <= h <= 270:
        raise Invalid("标签宽度 15–190 mm，高度 10–270 mm")
    if preferred not in ("auto", "wide", "compact"):
        raise Invalid("标签布局无效")
    mode = "wide" if preferred == "auto" and w / h >= 1.5 and w >= 36 and h >= 20 else (
        "compact" if preferred == "auto" else preferred
    )
    pad = _clamp(min(w, h) * .045, .75, 1.6)
    gap = _clamp(min(w, h) * .035, .55, 1.3)
    iw, ih = w - 2 * pad, h - 2 * pad
    if mode == "wide":
        side = min(ih * .86, iw * .49)
        qr = (w - pad - side, (h - side) / 2, side, side)
        left_w, top_h = qr[0] - gap - pad, ih * .58
        text = (pad, pad, left_w, top_h)
        barcode = (pad, pad + top_h + gap, left_w, ih - top_h - gap)
    else:
        top_h, side = ih * .63, min(ih * .63, iw * .44)
        qr = (w - pad - side, pad, side, side)
        text = (pad, pad, iw - side - gap, top_h)
        barcode = (pad, pad + top_h + gap, iw, ih - top_h - gap)
    return {"mode": mode, "text": text, "qr": qr, "barcode": barcode}


def _wrap_text(text, size, max_width, stretch, metrics, font, max_lines=2):
    """Split at character boundaries. Clip only when the print area is too small."""
    result, line = [], ""
    for char in str(text or ""):
        if line and metrics.stringWidth(line + char, font, size) * stretch > max_width:
            result.append(line)
            line = ""
        line += char
        if len(result) >= max_lines:
            break
    if line and len(result) < max_lines:
        result.append(line)
    if len(result) == max_lines:
        consumed = sum(map(len, result))
        if consumed < len(str(text or "")):
            tail = result[-1]
            while tail and metrics.stringWidth(tail + "…", font, size) * stretch > max_width:
                tail = tail[:-1]
            result[-1] = tail + "…" if tail else ""
    return result


def _fit_text(text, size, max_width, stretch, metrics, font):
    line = str(text or "")
    while line and metrics.stringWidth(line, font, size) * stretch > max_width:
        line = line[:-1]
    return line


def draw_label(doc, code, name, serial, w, h, layout, font, font_width):
    """Draw text, QR and vector Code128 without stretching the overall design."""
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.graphics.barcode import code128, qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderPDF

    boxes = label_geometry(w, h, layout)
    tx, ty, tw, th = boxes["text"]
    horizontal = font_width / 100
    title_pt = max(4, min(font, th / (25.4 / 72) / 3.35))
    title_mm = title_pt * 25.4 / 72
    second_pt = max(4, title_pt * .77)
    second_mm = second_pt * 25.4 / 72
    font_name = "STSong-Light"

    def line_at(value, size, baseline_mm):
        doc.saveState()
        doc.translate(tx * mm, (h - baseline_mm) * mm)
        doc.scale(horizontal, 1)
        doc.setFont(font_name, size)
        doc.drawString(0, 0, _fit_text(value, size, tw * mm, horizontal, pdfmetrics, font_name))
        doc.restoreState()

    cursor = ty + title_mm * .88
    lines = _wrap_text(name, title_pt, tw * mm, horizontal, pdfmetrics, font_name)
    for line in lines:
        line_at(line, title_pt, cursor)
        cursor += title_mm * 1.12
    for line in (code, serial):
        if line and cursor + second_mm * .1 <= ty + th:
            line_at(line, second_pt, cursor)
            cursor += second_mm * 1.16

    qx, qy, qw, qh = boxes["qr"]
    widget = qr.QrCodeWidget(code)
    bounds = widget.getBounds()
    side = min(qw, qh) * mm
    graphic = Drawing(side, side, transform=[
        side / (bounds[2] - bounds[0]), 0, 0, side / (bounds[3] - bounds[1]), 0, 0
    ])
    graphic.add(widget)
    renderPDF.draw(graphic, doc, qx * mm, (h - qy - qh) * mm)

    bx, by, bw, bh = boxes["barcode"]
    caption_pt = max(4.5, min(8, font * .65))
    caption_h = caption_pt * 25.4 / 72
    bar_h = max(1, bh - caption_h - .3)
    barcode = code128.Code128(code, barHeight=bar_h * mm, barWidth=.23 * mm, quiet=True)
    scale_x = min(1.8, bw * mm / barcode.width)
    actual_width = barcode.width * scale_x
    doc.saveState()
    doc.translate(bx * mm + (bw * mm - actual_width) / 2, (h - by - bar_h) * mm)
    doc.scale(scale_x, 1)
    barcode.drawOn(doc, 0, 0)
    doc.restoreState()
    doc.setFont(font_name, caption_pt)
    doc.drawCentredString((bx + bw / 2) * mm, (h - by - bh + .2) * mm, code)


def build_labels_pdf(records, w, h, paper="thermal", layout="auto", font=11, font_width=88):
    """Vector PDF with unchanged database identities and no printer-side scaling."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    label_geometry(w, h, layout)
    if paper not in ("thermal", "a4"):
        raise Invalid("纸张类型无效")
    if not isfinite(font) or not 6 <= font <= 18 or not isfinite(font_width) or not 70 <= font_width <= 110:
        raise Invalid("字体参数无效")
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    output = BytesIO()
    page_w, page_h = (210, 297) if paper == "a4" else (w, h)
    doc = canvas.Canvas(output, pagesize=(page_w * mm, page_h * mm))
    cols = int(190 // w) if paper == "a4" else 1
    rows = int(277 // h) if paper == "a4" else 1
    count = cols * rows
    for i, (code, name, serial) in enumerate(records):
        position = i % count
        if i and position == 0:
            doc.showPage()
        x = (10 if paper == "a4" else 0) + (position % cols) * w
        y = page_h - (10 if paper == "a4" else 0) - (position // cols + 1) * h
        doc.saveState()
        doc.translate(x * mm, y * mm)
        draw_label(doc, code, name, serial, w, h, layout, font, font_width)
        doc.restoreState()
    doc.save()
    output.seek(0)
    return output


@bp.get("/api/assets/labels.pdf")
@login_required
def labels():
    try:
        ids = request.args.get("ids", "").split(",")
        if not 1 <= len(ids) <= 100 or any(not x.isascii() or not x.isdigit() or int(x) < 1 for x in ids):
            raise Invalid("请选择 1 至 100 张标签")
        kind = request.args.get("kind", "unit")
        if kind not in ("unit", "item"):
            raise Invalid("标签类型无效")
        try:
            w = float(request.args.get("width", "50"))
            h = float(request.args.get("height", "30"))
            font = float(request.args.get("font", "11"))
            font_width = float(request.args.get("font_width", "88"))
        except ValueError:
            raise Invalid("标签尺寸或字体参数无效") from None
        paper = request.args.get("paper", "thermal")
        layout = request.args.get("layout", "auto")
        label_geometry(w, h, layout)
        db = get_db()
        records = []
        for identity in dict.fromkeys(map(int, ids)):
            unit = row(db, "asset_units", identity) if kind == "unit" else None
            item = row(db, "asset_items", unit["item_id"] if unit else identity)
            records.append((
                ("U:" if unit else "M:") + str(identity),
                item["name"],
                unit["serial"] if unit else item["specification"],
            ))
        output = build_labels_pdf(records, w, h, paper, layout, font, font_width)
        response = send_file(output, mimetype="application/pdf", as_attachment=True, download_name="asset-labels.pdf")
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except Invalid as error:
        return jsonify(ok=False, error=error.message), error.status
