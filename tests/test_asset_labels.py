"""Dimensions and layout regression for vector asset label PDFs."""
from io import BytesIO
from itertools import product
import unittest
from uuid import uuid4

from modules.asset_labels import label_geometry, build_labels_pdf
from modules.assets import Invalid
import test_assets as fixture


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
        for width, height, layout in ((5, 10, "auto"), (50, 5, "auto"), (50, 30, "none")):
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
        for options in ({"font": 19}, {"font_width": 60}, {"paper": "unknown"}, {"name_font": 37},
                        {"spec_font": float("nan")}, {"serial_overflow": "bad"}, {"auto_fit": "false"},
                        {"show_text": False, "show_qr": False, "show_barcode": False}):
            with self.subTest(options=options):
                with self.assertRaises(Invalid):
                    build_labels_pdf(data, 50, 30, **options)

    def test_all_visible_combinations_reclaim_space_without_overlap(self):
        for width, height in ((6, 6), (12, 8), (20, 12), (50, 30), (40, 270)):
            for flags in product((False, True), repeat=3):
                if not any(flags):
                    continue
                options = dict(zip(("show_text", "show_qr", "show_barcode"), flags))
                for mode in ("auto", "wide", "compact"):
                    with self.subTest(width=width, height=height, flags=flags, mode=mode):
                        geometry = label_geometry(width, height, mode, **options)
                        boxes = [geometry[key] for key in ("text", "qr", "barcode") if geometry[key]]
                        self.assertEqual(len(boxes), sum(flags))
                        for x, y, w, h in boxes:
                            self.assertGreater(min(w, h), 0)
                            self.assertGreaterEqual(min(x, y), 0)
                            self.assertLessEqual(x + w, width + 1e-6)
                            self.assertLessEqual(y + h, height + 1e-6)
                        for a, b in product(boxes, repeat=2):
                            if a is b:
                                continue
                            self.assertTrue(a[0] + a[2] <= b[0] + 1e-6 or b[0] + b[2] <= a[0] + 1e-6
                                            or a[1] + a[3] <= b[1] + 1e-6 or b[1] + b[3] <= a[1] + 1e-6)
                        if sum(flags) == 1:
                            self.assertAlmostEqual(min(boxes[0][2:]), min(width, height) - 2 * max(
                                .75, min(1.6, min(width, height) * .045)))

    def test_code_only_pdf_has_no_text_and_manual_fields_have_independent_sizes(self):
        import pdfplumber
        record = ("M:12/U:42", "电机", "SN-1", "M3508-P19")
        for qr_only in (True, False):
            output = build_labels_pdf([record], 12, 8, show_text=False, show_qr=qr_only,
                                      show_barcode=not qr_only)
            with pdfplumber.open(output) as pdf:
                self.assertFalse(pdf.pages[0].chars, "pure code labels must have no captions")
                self.assertTrue(pdf.pages[0].rects or pdf.pages[0].curves)
        output = build_labels_pdf([record], 100, 60, show_qr=False, show_barcode=False, auto_fit=False,
                                  name_font=16, spec_font=12, serial_font=10, code_font=8)
        with pdfplumber.open(output) as pdf:
            page = pdf.pages[0]
            self.assertEqual({round(char["size"]) for char in page.chars}, {8, 10, 12, 16})
            text = page.extract_text()
            for field in record:
                self.assertIn(field, text)

    def test_wrap_truncate_and_auto_fit_keep_text_in_its_area(self):
        import pdfplumber
        for overflow, auto_fit in product(("wrap", "truncate"), (False, True)):
            output = build_labels_pdf([("M:1/U:42", "超长名称ABCDEFGHIJK" * 6, "LONG-SN" * 10, "型号" * 30)],
                                      20, 12, show_qr=False, show_barcode=False, auto_fit=auto_fit,
                                      name_font=12, spec_font=10, name_overflow=overflow, spec_overflow=overflow)
            with pdfplumber.open(output) as pdf:
                page = pdf.pages[0]
                for char in page.chars:
                    self.assertGreaterEqual(char["x0"], 0)
                    self.assertGreaterEqual(char["top"], 0)
                    self.assertLessEqual(char["x1"], page.width + .01)
                    self.assertLessEqual(char["bottom"], page.height + .01)
                self.assertIn("…", page.extract_text())

    def test_long_name_wraps_on_multiple_lines_or_truncates_on_one_line(self):
        import pdfplumber
        for overflow in ("wrap", "truncate"):
            output = build_labels_pdf([("", "Long motor model " * 12, "")], 30, 30,
                                      show_qr=False, show_barcode=False, auto_fit=False,
                                      name_font=12, name_overflow=overflow)
            with pdfplumber.open(output) as pdf:
                baselines = {round(char["top"], 2) for char in pdf.pages[0].chars}
                if overflow == "wrap":
                    self.assertGreater(len(baselines), 1)
                else:
                    self.assertEqual(len(baselines), 1)
                self.assertIn("…", pdf.pages[0].extract_text())


class LabelApiTests(unittest.TestCase):
    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save

    def test_unit_suffix_is_unique_with_or_without_sn_and_old_codes_still_resolve(self):
        import pdfplumber
        item = self.save("items", dict(name="标签单件" + uuid4().hex, specification="DM4310", kind="returnable", unit="台"))
        ids = self.post(self.admin, "/api/assets/units/receive", dict(
            item_id=item, campus_id=self.campus, quantity=3, reason="合成标签",
            serials=["", "SAME-SN", "SAME-SN"],
        ))["unit_ids"]
        for identity in ids:
            code = f"M:{item}/U:{identity}"
            response = self.member.get(f"/api/assets/labels.pdf?ids={identity}&width=100&height=60")
            self.assertEqual(response.status_code, 200)
            with pdfplumber.open(BytesIO(response.data)) as pdf:
                self.assertIn(code, pdf.pages[0].extract_text())
                self.assertIn("DM4310", pdf.pages[0].extract_text())
            for raw in (code, f"U:{identity}"):
                scanned = self.member.post("/api/assets/scan/resolve", json={"raw": raw}).get_json()
                self.assertEqual(scanned["unit"]["id"], identity)
            self.assertEqual(self.member.get(f"/api/assets/units/{identity}").get_json()["unit"]["serial"],
                             "" if identity == ids[0] else "SAME-SN")
        wrong = self.member.post("/api/assets/scan/resolve", json={"raw": f"M:{self.item}/U:{ids[0]}"})
        self.assertEqual(wrong.status_code, 409)
        self.assertIsNone(self.member.post("/api/assets/scan/resolve", json={"raw": f"M:{item}"}).get_json()["unit"])

    def test_pdf_api_validates_new_options_and_supports_small_code_only_labels(self):
        from urllib.parse import urlencode
        url = f"/api/assets/labels.pdf?kind=item&ids={self.item}&"
        for options in ({"show_text": "maybe"}, {"auto_fit": "maybe"}, {"name_font": "NaN"},
                        {"code_overflow": "bad"}, {"spec_font": "abc"},
                        {"show_text": "false", "show_qr": "false", "show_barcode": "false"}):
            self.assertEqual(self.member.get(url + urlencode(options)).status_code, 400, options)
        response = self.member.get(url + "width=6&height=6&show_text=false&show_barcode=false")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")


if __name__ == "__main__":
    unittest.main()
