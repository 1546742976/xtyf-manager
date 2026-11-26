"""Read-only, millimetre-accurate vector PDF asset labels.

Layout choices and geometry mirror static/asset-label-layout.js. Unit labels
append the stable U: identity to their M: model code, independently of vendor SN.
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


def label_geometry(width, height, preferred="auto", *, show_text=True, show_qr=True, show_barcode=True):
    """Return rectangles measured from the label's top-left, in millimetres."""
    w, h = float(width), float(height)
    if not isfinite(w) or not isfinite(h) or not 6 <= w <= 190 or not 6 <= h <= 270:
        raise Invalid("标签宽度 6–190 mm，高度 6–270 mm")
    if preferred not in ("auto", "wide", "compact"):
        raise Invalid("标签布局无效")
    if any(type(value) is not bool for value in (show_text, show_qr, show_barcode)):
        raise Invalid("标签显示设置无效")
    if not any((show_text, show_qr, show_barcode)):
        raise Invalid("文字、二维码和条形码至少保留一项")
    mode = "wide" if preferred == "auto" and w / h >= 1.5 and w >= 36 and h >= 20 else (
        "compact" if preferred == "auto" else preferred
    )
    pad = _clamp(min(w, h) * .045, .75, 1.6)
    gap = _clamp(min(w, h) * .035, .55, 1.3)
    iw, ih = w - 2 * pad, h - 2 * pad
    text = qr = barcode = None
    if sum((show_text, show_qr, show_barcode)) == 1:
        if show_qr:
            side = min(iw, ih)
            qr = ((w - side) / 2, (h - side) / 2, side, side)
        elif show_text:
            text = (pad, pad, iw, ih)
        else:
            barcode = (pad, pad, iw, ih)
    elif not show_barcode:
        side = min(ih, iw * .52)
        qr = (w - pad - side, (h - side) / 2, side, side)
        text = (pad, pad, iw - side - gap, ih)
    elif not show_qr or not show_text:
        top_h = ih * (.60 if show_text else .68)
        barcode = (pad, pad + top_h + gap, iw, ih - top_h - gap)
        if show_text:
            text = (pad, pad, iw, top_h)
        else:
            side = min(iw, top_h)
            qr = ((w - side) / 2, pad + (top_h - side) / 2, side, side)
    elif mode == "wide":
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
    value = " ".join(str(text or "").split())
    for char in value:
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
        if consumed < len(value):
            tail = result[-1]
            while tail and metrics.stringWidth(tail + "…", font, size) * stretch > max_width:
                tail = tail[:-1]
            result[-1] = tail + "…" if tail else ""
    return result


def _text_lines(fields, width, height, stretch, auto_fit, metrics, font):
    """Fit all fields together; manual mode keeps requested sizes and clips safely.

    Fields contain (text, requested point size, overflow). Reserve one line for
    each remaining field so a long name cannot hide the model, SN or unit code.
    """
    fields = [(value, size, overflow) for value, size, overflow in fields if value]

    def measure(factor):
        return [(max(4, size * factor), _wrap_text(
            value, max(4, size * factor), width, stretch, metrics, font,
            max_lines=120 if overflow == "wrap" else 1,
        )) for value, size, overflow in fields]

    factor, measured = 1, measure(1)
    if auto_fit:
        for _ in range(50):
            if sum(size * 1.16 * len(lines) for size, lines in measured) <= height:
                break
            factor *= .94
            measured = measure(factor)
            if all(size <= 4 for size, _ in measured):
                break
    result, cursor = [], 0
    for i, (value, _, overflow) in enumerate(fields):
        size = measured[i][0]
        reserved = sum(other_size * 1.16 for other_size, _ in measured[i + 1:])
        room = max(0, int((height - cursor - reserved + 1e-7) / (size * 1.16)))
        # If even one line per field cannot fit, use the visible area in order.
        if room == 0:
            room = max(0, int((height - cursor + 1e-7) / (size * 1.16)))
        if not room:
            continue
        lines = _wrap_text(value, size, width, stretch, metrics, font,
                           max_lines=room if overflow == "wrap" else 1)
        for line in lines:
            result.append((line, size, cursor + size * .88))
            cursor += size * 1.16
    return result


def draw_label(doc, record, w, h, layout, font_width, options):
    """Draw text, QR and vector Code128 without stretching the overall design."""
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.graphics.barcode import code128, qr

    code, name, serial = record[:3]
    specification = record[3] if len(record) > 3 else ""
    boxes = label_geometry(w, h, layout, **options["visibility"])
    horizontal = font_width / 100
    font_name = "STSong-Light"
    if boxes["text"]:
        tx, ty, tw, th = boxes["text"]
        fields = [(value, options[key + "_font"], options[key + "_overflow"]) for key, value in (
            ("name", name), ("spec", specification), ("serial", "SN " + serial if serial else ""), ("code", code)
        )]
        doc.saveState()
        clip = doc.beginPath()
        clip.rect(tx * mm, (h - ty - th) * mm, tw * mm, th * mm)
        doc.clipPath(clip, stroke=0)
        for value, size, baseline in _text_lines(fields, tw * mm, th * mm, horizontal,
                                                options["auto_fit"], pdfmetrics, font_name):
            doc.saveState()
            doc.translate(tx * mm, (h - ty) * mm - baseline)
            doc.scale(horizontal, 1)
            doc.setFont(font_name, size)
            doc.drawString(0, 0, value)
            doc.restoreState()
        doc.restoreState()

    if boxes["qr"]:
        qx, qy, qw, qh = boxes["qr"]
        widget = qr.QrCodeWidget(code, barLevel="M")
        widget.qr.make()
        count, border = widget.qr.getModuleCount(), widget.barBorder
        cell = min(qw, qh) * mm / (count + 2 * border)
        # A single filled path prevents antialias seams between separately
        # painted rows from breaking finder patterns at certain preview scales.
        path = doc.beginPath()
        for r, cells in enumerate(widget.qr.modules):
            start = None
            for c, dark in enumerate([*cells, False]):
                if dark and start is None:
                    start = c
                elif not dark and start is not None:
                    path.rect(qx * mm + (start + border) * cell,
                              (h - qy - qh) * mm + (count + border - r - 1) * cell,
                              (c - start) * cell, cell)
                    start = None
        doc.saveState()
        doc.setFillColorRGB(0, 0, 0)
        doc.drawPath(path, stroke=0, fill=1)
        doc.restoreState()

    if boxes["barcode"]:
        bx, by, bw, bh = boxes["barcode"]
        barcode = code128.Code128(code, barHeight=bh * mm, barWidth=.23 * mm, quiet=True)
        scale_x = min(1.8, bw * mm / barcode.width)
        actual_width = barcode.width * scale_x
        doc.saveState()
        doc.translate(bx * mm + (bw * mm - actual_width) / 2, (h - by - bh) * mm)
        doc.scale(scale_x, 1)
        barcode.drawOn(doc, 0, 0)
        doc.restoreState()


def build_labels_pdf(records, w, h, paper="thermal", layout="auto", font=11, font_width=88, **settings):
    """Vector PDF with unchanged database identities and no printer-side scaling."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    visibility = {key: settings.get(key, True) for key in ("show_text", "show_qr", "show_barcode")}
    label_geometry(w, h, layout, **visibility)
    if paper not in ("thermal", "a4"):
        raise Invalid("纸张类型无效")
    if not isfinite(font) or not 6 <= font <= 18 or not isfinite(font_width) or not 70 <= font_width <= 110:
        raise Invalid("字体参数无效")
    options = {"visibility": visibility, "auto_fit": settings.get("auto_fit", True)}
    if type(options["auto_fit"]) is not bool:
        raise Invalid("自动字号设置无效")
    for key in ("name", "spec", "serial", "code"):
        size = settings.get(key + "_font", font if key == "name" else max(4, font * .77))
        overflow = settings.get(key + "_overflow", "wrap" if key in ("name", "spec") else "truncate")
        if not isinstance(size, (int, float)) or not isfinite(size) or not 4 <= size <= 36:
            raise Invalid("各项文字字号须为 4–36 pt")
        if overflow not in ("wrap", "truncate"):
            raise Invalid("过长文字处理方式无效")
        options[key + "_font"], options[key + "_overflow"] = size, overflow
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    output = BytesIO()
    page_w, page_h = (210, 297) if paper == "a4" else (w, h)
    doc = canvas.Canvas(output, pagesize=(page_w * mm, page_h * mm))
    cols = int(190 // w) if paper == "a4" else 1
    rows = int(277 // h) if paper == "a4" else 1
    count = cols * rows
    for i, record in enumerate(records):
        position = i % count
        if i and position == 0:
            doc.showPage()
        x = (10 if paper == "a4" else 0) + (position % cols) * w
        y = page_h - (10 if paper == "a4" else 0) - (position // cols + 1) * h
        doc.saveState()
        doc.translate(x * mm, y * mm)
        draw_label(doc, record, w, h, layout, font_width, options)
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
            settings = {key + "_font": float(request.args[key + "_font"])
                        for key in ("name", "spec", "serial", "code") if key + "_font" in request.args}
        except ValueError:
            raise Invalid("标签尺寸或字体参数无效") from None
        for key in ("name", "spec", "serial", "code"):
            if key + "_overflow" in request.args:
                settings[key + "_overflow"] = request.args[key + "_overflow"]
        for key in ("show_text", "show_qr", "show_barcode", "auto_fit"):
            value = request.args.get(key, "true")
            if value not in ("true", "false", "1", "0"):
                raise Invalid("标签显示或自动字号设置无效")
            settings[key] = value in ("true", "1")
        paper = request.args.get("paper", "thermal")
        layout = request.args.get("layout", "auto")
        label_geometry(w, h, layout)
        db = get_db()
        records = []
        for identity in dict.fromkeys(map(int, ids)):
            unit = row(db, "asset_units", identity) if kind == "unit" else None
            item = row(db, "asset_items", unit["item_id"] if unit else identity)
            records.append((
                f"M:{item['id']}/U:{identity}" if unit else f"M:{identity}",
                item["name"],
                unit["serial"] if unit else "",
                item["specification"],
            ))
        output = build_labels_pdf(records, w, h, paper, layout, font, font_width, **settings)
        response = send_file(output, mimetype="application/pdf", as_attachment=True, download_name="asset-labels.pdf")
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except Invalid as error:
        return jsonify(ok=False, error=error.message), error.status
