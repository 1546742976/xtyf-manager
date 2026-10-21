"""Read-only vector PDF labels, downloaded through the existing authenticated channel."""

from io import BytesIO
from flask import Blueprint, request, send_file, jsonify
from common import get_db, login_required
from modules.assets import Invalid, row

bp = Blueprint("asset_labels", __name__)


@bp.get("/api/assets/labels.pdf")
@login_required
def labels():
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.graphics.barcode import code128, qr
        from reportlab.graphics.shapes import Drawing
        from reportlab.graphics import renderPDF

        ids = request.args.get("ids", "").split(",")
        if not 1 <= len(ids) <= 100 or any(not x.isascii() or not x.isdigit() or int(x) < 1 for x in ids):
            raise Invalid("请选择 1 至 100 张标签")
        kind = request.args.get("kind", "unit")
        if kind not in ("unit", "item"):
            raise Invalid("标签类型无效")
        try:
            w, h = (float(request.args.get(k, d)) for k, d in (("width", "60"), ("height", "35")))
        except ValueError:
            raise Invalid("标签尺寸无效") from None
        if not 40 <= w <= 190 or not 25 <= h <= 270:
            raise Invalid("标签宽度 40–190 mm，高度 25–270 mm")
        a4 = request.args.get("paper", "a4") == "a4"
        db = get_db()
        records = []
        for identity in dict.fromkeys(map(int, ids)):
            unit = row(db, "asset_units", identity) if kind == "unit" else None
            item = row(db, "asset_items", unit["item_id"] if unit else identity)
            records.append(
                (
                    ("U:" if unit else "M:") + str(identity),
                    item["name"],
                    unit["serial"] if unit else item["specification"],
                )
            )
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        output = BytesIO()
        page_w, page_h = (210, 297) if a4 else (w, h)
        doc = canvas.Canvas(output, pagesize=(page_w * mm, page_h * mm))
        cols = int(190 // w) if a4 else 1
        rows = int(277 // h) if a4 else 1
        for i, (code, name, serial) in enumerate(records):
            pos = i % (cols * rows)
            if i and pos == 0:
                doc.showPage()
            x = (10 if a4 else 0) + (pos % cols) * w
            y = page_h - (10 if a4 else 0) - (pos // cols + 1) * h
            doc.saveState()
            doc.translate(x * mm, y * mm)
            # Preserve QR/barcode proportions on arbitrary thermal dimensions.
            scale = min((w - 2) / 60, (h - 2) / 35)
            doc.translate((w - 60 * scale) * mm / 2, (h - 35 * scale) * mm / 2)
            doc.scale(scale, scale)
            doc.setFont("STSong-Light", 8)
            doc.drawString(2 * mm, 31 * mm, name[:26])
            doc.setFont("Helvetica", 8)
            doc.drawString(2 * mm, 26 * mm, code)
            doc.setFont("STSong-Light", 6)
            doc.drawString(2 * mm, 22 * mm, serial[:30])
            widget = qr.QrCodeWidget(code)
            bounds = widget.getBounds()
            size = 19 * mm
            drawing = Drawing(
                size, size, transform=[size / (bounds[2] - bounds[0]), 0, 0, size / (bounds[3] - bounds[1]), 0, 0]
            )
            drawing.add(widget)
            renderPDF.draw(drawing, doc, 39 * mm, 8 * mm)
            barcode = code128.Code128(code, barHeight=9 * mm, barWidth=0.22 * mm, quiet=True)
            doc.saveState()
            doc.translate(1 * mm, 4 * mm)
            doc.scale(min(1, 37 * mm / barcode.width), 1)
            barcode.drawOn(doc, 0, 0)
            doc.restoreState()
            doc.restoreState()
        doc.save()
        output.seek(0)
        response = send_file(output, mimetype="application/pdf", as_attachment=True, download_name="asset-labels.pdf")
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except Invalid as e:
        return jsonify(ok=False, error=e.message), e.status
