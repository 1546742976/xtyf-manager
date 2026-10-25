"""Dimensions and layout regression for vector asset label PDFs."""
from io import BytesIO
import unittest

from modules.asset_labels import label_geometry, build_labels_pdf
from modules.assets import Invalid


class LabelLayoutTests(unittest.TestCase):
    def test_auto_layout_stays_inside_printable_label(self):
        for width, height, mode in [
            (50, 30, "wide"), (30, 30, "compact"),
            (25, 15, "compact"), (20, 12, "compact")
        ]:
            with self.subTest(width=width, height=height):
                geometry = label_geometry(width, height)
                self.assertEqual(geometry["mode"], mode)
                for x, y, w, h in (geometry["text"], geometry["qr"], geometry["barcode"]):
                    self.assertGreaterEqual(min(x, y), 0)
                    self.assertGreater(w, 0)
                    self.assertGreater(h, 0)
                    self.assertLessEqual(x + w, width + 1e-6)
                    self.assertLessEqual(y + h, height + 1e-6)
                self.assertEqual(geometry["qr"][2], geometry["qr"][3])

    def test_manual_layout_and_invalid_dimensions(self):
        self.assertEqual(label_geometry(50, 30, "compact")["mode"], "compact")
        self.assertEqual(label_geometry(30, 30, "wide")["mode"], "wide")
        for width, height, layout in ((14, 10, "auto"), (50, 9, "auto"), (50, 30, "none")):
            with self.subTest(width=width, height=height, layout=layout):
                with self.assertRaises(Invalid):
                    label_geometry(width, height, layout)

    def test_vector_pdf_preserves_exact_page_size_and_a4_grid(self):
        import pdfplumber
        entries = [("M:128", "贴片电阻 10kΩ", "0603")] * 5
        for width, height, layout in ((50, 30, "wide"), (30, 30, "compact"), (20, 12, "auto")):
            output = build_labels_pdf(entries[:1], width, height, "thermal", layout, 12, 82)
            with pdfplumber.open(BytesIO(output.getvalue())) as pdf:
                self.assertEqual(len(pdf.pages), 1)
                self.assertLess(abs(pdf.pages[0].width * 25.4 / 72 - width), .02)
                self.assertLess(abs(pdf.pages[0].height * 25.4 / 72 - height), .02)
        output = build_labels_pdf(entries, 50, 30, "a4", "auto", 11, 88)
        with pdfplumber.open(BytesIO(output.getvalue())) as pdf:
            self.assertEqual(len(pdf.pages), 1)
            self.assertLess(abs(pdf.pages[0].width * 25.4 / 72 - 210), .02)
            self.assertLess(abs(pdf.pages[0].height * 25.4 / 72 - 297), .02)

    def test_rejects_out_of_range_font_and_paper(self):
        data = [("U:42", "M3508 电机", "SN-1")]
        for options in ({"font": 19}, {"font_width": 60}, {"paper": "unknown"}):
            with self.subTest(options=options):
                with self.assertRaises(Invalid):
                    build_labels_pdf(data, 50, 30, **options)


if __name__ == "__main__":
    unittest.main()
