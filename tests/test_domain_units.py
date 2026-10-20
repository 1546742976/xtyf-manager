"""Unit-level domain rules, using in-memory graph fixtures and test-only files."""
import os
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

from werkzeug.exceptions import BadRequest
from test_tasks import app_module  # noqa: F401  # initializes isolated app/test paths
from modules.csv_security import safe_csv_cell
from modules.funds import (_validated_count, summarize_records, stats_payload,
                           valid_record_date, validate_expense_fields)
from modules.invoices import (_is_safe_rel, _normalize_month, get_stats,
                              load_cache, parse_invoice_filename, save_cache)
from modules.member_profiles import _clean_text
from modules.money import (MAX_CENTS, decimal_amount, format_cents, money_fields,
                           money_json, to_cents)
from modules.security import _audit_details
from modules.task_validation import (MAX_SQLITE_INTEGER, InvalidIdError, active_ids,
                                     creates_cycle, normalize_ids, parse_id,
                                     valid_tasks, validate_dates)


class TestMoneyRules(unittest.TestCase):
    def test_half_up_and_integer_cents(self):
        cases = {"0": 0, "0.004": 0, "0.005": 1, "1.005": 101,
                 "1.995": 200, Decimal("0.295"): 30,
                 "100000000000.09": 10_000_000_000_009}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(to_cents(value), expected)

    def test_reject_bad_amounts_and_nonpositive_rounding(self):
        for value in (True, None, [], {}, "-1", "NaN", "Infinity", "1,000",
                      "1_000", "1.2.3", "1e500", " "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                to_cents(value)
        for value in ("0", "0.004", "0.0001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                to_cents(value, positive=True)

    def test_bounds_and_lossless_serialization(self):
        self.assertEqual(to_cents("92233720368547758.07"), MAX_CENTS)
        with self.assertRaises(ValueError):
            to_cents("92233720368547758.08")
        self.assertEqual(format_cents(-123456), "-1234.56")
        self.assertEqual(format_cents(123456, grouping=True), "1,234.56")
        self.assertEqual(money_fields(MAX_CENTS), {
            "amount": "92233720368547758.07", "amount_cents": str(MAX_CENTS)})
        self.assertEqual(decimal_amount("0.105"), Decimal("0.11"))
        for cents in (True, Decimal("10"), 1.2):
            with self.assertRaises(ValueError):
                format_cents(cents)

    def test_json_preserves_decimal_lexeme(self):
        with app_module.app.test_request_context(
                "/fake", method="POST", data='{"amount":0.105,"count":3}',
                content_type="application/json"):
            data = money_json()
            self.assertEqual(data["amount"], Decimal("0.105"))
            self.assertEqual(data["count"], 3)
        for payload in ('[1]', '{"amount":', 'null'):
            with self.subTest(payload=payload):
                with app_module.app.test_request_context(
                        "/fake", method="POST", data=payload,
                        content_type="application/json"):
                    with self.assertRaises(BadRequest):
                        money_json()


class TestTaskValidationUnits(unittest.TestCase):
    def test_normalize_and_parse_ids(self):
        self.assertEqual(normalize_ids(["3", 1, "2"], "成员"), ([3, 1, 2], None))
        self.assertEqual(normalize_ids(None, "成员"), ([], None))
        for values in (5, "1", [True], [1.0], [0], [-1], [1, "1"],
                       [MAX_SQLITE_INTEGER + 1], ["1.0"]):
            with self.subTest(values=values):
                ids, error = normalize_ids(values, "成员")
                self.assertIsNone(ids)
                self.assertIsNotNone(error)
        self.assertIsNone(parse_id(None, "owner"))
        self.assertIsNone(parse_id("0", "owner"))
        self.assertEqual(parse_id(str(MAX_SQLITE_INTEGER), "owner"), MAX_SQLITE_INTEGER)
        for value in (True, False, -1, " 1", "1.0", 1.0,
                      MAX_SQLITE_INTEGER + 1):
            with self.subTest(value=value), self.assertRaises(InvalidIdError):
                parse_id(value, "owner", nullable=False)

    def test_dates_and_leap_year(self):
        self.assertIsNone(validate_dates("2024-02-29", "2024-03-01"))
        self.assertIsNone(validate_dates("", None))
        for start, due in (("2025-02-29", ""), ("2026-13-02", ""),
                           ("2026-10-02", "2026-10-01"), (123, "")):
            with self.subTest(start=start, due=due):
                self.assertIsNotNone(validate_dates(start, due))

    def test_deleted_references_and_graph_cycles(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE members(id INTEGER PRIMARY KEY,is_active INT,deleted_at TEXT);
            CREATE TABLE groups(id INTEGER PRIMARY KEY,deleted_at TEXT);
            CREATE TABLE projects(id INTEGER PRIMARY KEY,deleted_at TEXT);
            CREATE TABLE tasks(id INTEGER PRIMARY KEY,parent_id INT,deleted_at TEXT);
            CREATE TABLE task_dependencies(task_id INT,predecessor_id INT);
            INSERT INTO members VALUES(1,1,NULL),(2,0,NULL),(3,1,'deleted');
            INSERT INTO groups VALUES(1,NULL),(2,'deleted');
            INSERT INTO projects VALUES(1,NULL),(2,'deleted');
            INSERT INTO tasks VALUES(1,NULL,NULL),(2,1,NULL),(3,2,NULL),
                                    (4,NULL,'deleted');
            INSERT INTO task_dependencies VALUES(1,2),(2,3);
        """)
        self.assertTrue(active_ids(db, "members", [1]))
        self.assertTrue(active_ids(db, "projects", []))
        for table in ("groups", "projects"):
            self.assertFalse(active_ids(db, table, [2]))
        self.assertFalse(active_ids(db, "members", [2]))
        self.assertFalse(active_ids(db, "members", [3]))
        self.assertTrue(valid_tasks(db, [1, 2]))
        self.assertFalse(valid_tasks(db, [4]))
        self.assertFalse(valid_tasks(db, [1], current_id=1))
        self.assertTrue(creates_cycle(db, "tasks", "parent_id", 1, [3]))
        self.assertFalse(creates_cycle(db, "tasks", "parent_id", 3, [1]))
        self.assertTrue(creates_cycle(db, "task_dependencies", "predecessor_id", 3, [1]))
        self.assertFalse(creates_cycle(db, "task_dependencies", "predecessor_id", 1, [3]))


class TestLedgerUnits(unittest.TestCase):
    def test_expense_fields_invoice_count_and_dates(self):
        valid = {"source_dest": "采购", "applicant": "甲",
                 "handler": "乙", "invoice_count": 1}
        self.assertEqual(validate_expense_fields(valid), (True, ""))
        ok, reason = validate_expense_fields({})
        self.assertFalse(ok)
        self.assertIn("发票数目", reason)
        for value in (-1, True, "1.1", Decimal("1.5"), "1e2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validated_count(value)
        for value in (0, "03", 2, Decimal("2.0"), MAX_SQLITE_INTEGER):
            with self.subTest(value=value):
                self.assertEqual(_validated_count(value), int(value))
        self.assertTrue(valid_record_date("2024-02-29"))
        for value in ("2025-02-29", "2026-1-1", None, 123):
            self.assertFalse(valid_record_date(value))

    def test_grouped_income_and_expense_remains_exact(self):
        records = [
            {"record_type": "进", "amount_cents": 105, "record_date": "2026-10-01",
             "source_dest": "赞助", "category": "", "applicant": "", "handler": ""},
            {"record_type": "出", "amount_cents": 37, "record_date": "2026-10-02",
             "source_dest": "商家", "category": "电控", "applicant": "甲", "handler": "乙"},
            {"record_type": "出", "amount_cents": 28, "record_date": "2026-11-02",
             "source_dest": "商家", "category": "电控", "applicant": "甲", "handler": "乙"},
        ]
        stats = summarize_records(records)
        self.assertEqual((stats["total_in"], stats["total_out"], stats["balance"]),
                         (105, 65, 40))
        self.assertEqual(stats["category_stats"][0]["total"], 65)
        self.assertEqual(stats["monthly_stats"][0]["income"], 105)
        self.assertEqual(stats["monthly_stats"][1]["expense"], 28)
        payload = stats_payload(stats)
        self.assertEqual(payload["balance"], "0.40")
        self.assertEqual(payload["category_stats"][0]["total"], "0.65")
        self.assertEqual(payload["record_count"], 3)


class TestInvoiceUnits(unittest.TestCase):
    def test_filename_parsing_and_month_normalization(self):
        parsed = parse_invoice_filename(
            "2-机械-1,234.565元-某店-1234567890-2026.10.03-发票.pdf")
        self.assertEqual(parsed["seq"], "2")
        self.assertEqual(parsed["amount"], Decimal("1234.57"))
        self.assertEqual(parsed["category"], "机械")
        self.assertEqual(parsed["date"], "2026-10-03")
        self.assertTrue(parsed["from_filename"])
        self.assertIsNone(parse_invoice_filename("scan.txt"))
        fallback = parse_invoice_filename("普通扫描件.pdf")
        self.assertFalse(fallback["from_filename"])
        self.assertEqual(fallback["amount"], Decimal("0.00"))
        self.assertEqual(_normalize_month("2026.01"), "2026.1")

    def test_reject_traversal_and_drive_paths(self):
        for value in ("../secret.pdf", "/etc/passwd", "C:\\secret.pdf",
                      "invoices/../../data.db", "", "name\x00.pdf"):
            with self.subTest(value=value):
                self.assertFalse(_is_safe_rel(value))
        self.assertTrue(_is_safe_rel("2026.10/未报销/正常.pdf"))

    def test_cache_roundtrip_uses_integer_cents(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "cache.json")
            with patch("modules.invoices.CACHE_FILE", path):
                save_cache({"item.pdf": {"amount": Decimal("0.105"), "vendor": "商家"}})
                items = load_cache()
                self.assertEqual(items["item.pdf"]["amount"], Decimal("0.11"))
                self.assertEqual(items["item.pdf"]["amount_cents"], 11)
                with open(path, encoding="utf-8") as stream:
                    self.assertIn('"amount_cents": 11', stream.read())

    def test_stat_aggregation_is_exact(self):
        shared = {"month": "2026.10", "status": "未报销",
                  "submitter": "甲", "submitter_id": 123, "category": "电控"}
        records = [dict(shared, amount=value) for value in ("0.10", "0.20")]
        result = get_stats(records)
        self.assertEqual(result["total_amount"], "0.30")
        self.assertEqual(result["status_stats"]["未报销"]["amount"], "0.30")
        self.assertEqual(result["submitter_stats"]["甲 (#123)"]["count"], 2)


class TestDefensiveHelpers(unittest.TestCase):
    def test_csv_injection(self):
        for value in ("=1+1", "\t+cmd", "\r\n@HYPERLINK()", "-1", " @x"):
            with self.subTest(value=value):
                self.assertEqual(safe_csv_cell(value), "'" + value)
        self.assertEqual(safe_csv_cell("safe"), "safe")
        self.assertEqual(safe_csv_cell(None), "")
        self.assertEqual(safe_csv_cell(-1), -1)
        self.assertEqual(safe_csv_cell(True), "True")

    def test_profile_restrictions(self):
        self.assertEqual(_clean_text("  张三  ", "姓名", 80), ("张三", None))
        for value in ("", "\n", "x" * 81, 5, "a\x7fb"):
            with self.subTest(value=value):
                self.assertIsNotNone(_clean_text(value, "姓名", 80)[1])

    def test_audit_redacts_secrets(self):
        details = _audit_details({
            "id": 4, "amount": "1.00", "password": "secret",
            "notes": "private", "updates": {"status": "已完成", "token": "secret"},
            "items": [{"id": 2, "new_password": "secret"}],
        })
        self.assertEqual(details["id"], 4)
        self.assertNotIn("password", details)
        self.assertNotIn("notes", details)
        self.assertEqual(details["updates"], {"status": "已完成"})
        self.assertEqual(details["items"], [{"id": 2}])


if __name__ == "__main__":
    unittest.main()
