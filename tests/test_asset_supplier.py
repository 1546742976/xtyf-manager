"""Supplier metadata lookup regressions; these tests never access the network."""
import io
import json
import socket
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from modules import asset_supplier as supplier


class FakeResponse(io.BytesIO):
    def __init__(self, html, length=None):
        data = html.encode("utf-8") if isinstance(html, str) else html
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


class SupplierLookupTest(unittest.TestCase):
    def setUp(self):
        self.clear_cache()
        self.addCleanup(self.clear_cache)

    @staticmethod
    def clear_cache():
        with supplier._cache_lock:
            supplier._cache.clear()

    @staticmethod
    def product_page(sku="C12345", model="RES-10"):
        product = {
            "@type": "Product", "sku": sku, "name": "测试电阻",
            "mpn": model, "brand": {"name": "测试品牌"},
            "additionalProperty": [{"name": "Package", "value": "0603"}],
        }
        return '<script type="application/ld+json">' + json.dumps(
            {"@graph": [product]}, ensure_ascii=False
        ) + '</script>'

    def test_validation_prevents_arbitrary_network_destinations(self):
        with patch.object(supplier, "build_opener") as opener:
            invalid = (None, "", "c123", "C0", "C0123", "C1/path",
                       "C1?url=bad", "C" + "1" * 19)
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    supplier.lookup_lcsc(value)
            opener.assert_not_called()
        self.assertIsNone(supplier._NoRedirect().redirect_request(
            None, None, 302, "Found", {}, "https://example.invalid"
        ))

    def test_matching_jsonld_and_cache_are_isolated(self):
        opener = Mock()
        opener.open.return_value = FakeResponse(self.product_page())
        with patch.object(supplier, "build_opener", return_value=opener):
            first = supplier.lookup_lcsc("C12345")
            self.assertEqual(first["status"], "found")
            self.assertEqual(first["product"]["model"], "RES-10")
            self.assertEqual(first["product"]["package"], "0603")
            self.assertEqual(first["product"]["manufacturer"], "测试品牌")
            self.assertFalse(first["cached"])
            first["product"]["name"] = "污染缓存"
            second = supplier.lookup_lcsc("C12345")
            self.assertTrue(second["cached"])
            self.assertEqual(second["product"]["name"], "测试电阻")
            opener.open.assert_called_once()

    def test_mismatched_or_incomplete_product_is_never_accepted(self):
        url = "https://www.lcsc.com/product-detail/C12345.html"
        for page in (self.product_page("C99999"), self.product_page(model=""),
                     '<script type="application/ld+json">invalid</script>'):
            with self.subTest(page=page):
                self.assertIsNone(supplier._product_metadata(page, "C12345", url))

    def test_errors_and_timeouts_return_unavailable_without_stock_write(self):
        for problem, expected in (
            (HTTPError("https://www.lcsc.com", 404, "missing", {}, None), "not_found"),
            (HTTPError("https://www.lcsc.com", 302, "redirect", {}, None), "unavailable"),
            (HTTPError("https://www.lcsc.com", 429, "rate limit", {}, None), "unavailable"),
            (socket.timeout("timeout"), "unavailable"),
            (URLError("offline"), "unavailable"),
        ):
            with self.subTest(problem=str(problem)):
                self.clear_cache()
                opener = Mock()
                opener.open.side_effect = problem
                with patch.object(supplier, "build_opener", return_value=opener):
                    result = supplier.lookup_lcsc("C12345")
                self.assertEqual(result["status"], expected)
                self.assertIsNone(result["product"])

    def test_page_size_is_bounded_even_without_valid_content_length(self):
        limit = supplier.MAX_PAGE_BYTES + 1
        for response in (FakeResponse(b"", length=limit), FakeResponse(b"x" * limit, length=0)):
            with self.subTest(header=response.headers["Content-Length"]):
                self.clear_cache()
                opener = Mock()
                opener.open.return_value = response
                with patch.object(supplier, "build_opener", return_value=opener):
                    result = supplier.lookup_lcsc("C12345")
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["product"])


if __name__ == "__main__":
    unittest.main()
