"""发票字段排序：日期/金额采用数值，跨页排序，缺失置底且权限入口不变。"""
import unittest
from decimal import Decimal
from unittest.mock import patch

from test_tasks import app_module
from modules import invoices


class TestInvoiceSorting(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        login = self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only'
        })
        self.assertTrue(login.get_json()['ok'])

    @staticmethod
    def rows():
        records = [
            ('a', '2026.2', '2026-2-9', '2.50', '供应商9', '未报销'),
            ('b', '2026.10', '2026-10-03', '10.00', '供应商10', '已报销'),
            ('c', '2026.11', '2026年11月1日', '9.00', '供应商2', '正在报销'),
            ('d', '未归类', '', '0.00', '', '未报销'),
            ('e', '2026.10', '2026.10.03', '9.00', '供应商2', '已报销'),
        ]
        return [
            dict(id=ident, month=month, date=date, amount=Decimal(amount),
                 vendor=vendor, status=status, category='耗材',
                 submitter='测试提交人', submitter_id=1, ownership_unresolved=False,
                 filename=ident + '.pdf', invoice_number='100' + ident)
            for ident, month, date, amount, vendor, status in records
        ]

    def test_date_and_month_sort_real_calendar_values(self):
        data = self.rows()
        self.assertEqual([x['id'] for x in invoices.sort_invoices(data)],
                         ['c', 'b', 'e', 'a', 'd'])
        self.assertEqual([x['id'] for x in invoices.sort_invoices(data, 'date', 'asc')],
                         ['a', 'b', 'e', 'c', 'd'])
        self.assertEqual([x['id'] for x in invoices.sort_invoices(data, 'month', 'asc')],
                         ['a', 'b', 'e', 'c', 'd'])
        self.assertIsNone(invoices._invoice_date_value('2026-02-30'))
        self.assertIsNone(invoices._invoice_month_value('2026.13'))

    def test_amount_natural_name_status_and_blank_placement(self):
        data = self.rows()
        self.assertEqual([x['id'] for x in invoices.sort_invoices(data, 'amount', 'asc')],
                         ['d', 'a', 'c', 'e', 'b'])
        self.assertEqual([x['id'] for x in invoices.sort_invoices(data, 'amount', 'desc')],
                         ['b', 'c', 'e', 'a', 'd'])
        self.assertEqual([x['id'] for x in invoices.sort_invoices(data, 'vendor', 'asc')],
                         ['c', 'e', 'a', 'b', 'd'])
        self.assertEqual([x['id'] for x in invoices.sort_invoices(data, 'status', 'asc')],
                         ['a', 'd', 'c', 'b', 'e'])

    def test_sort_before_pagination_and_combine_with_filters(self):
        with patch.object(invoices, 'scan_invoices', return_value=self.rows()), \
             patch.object(invoices, '_is_finance', return_value=True):
            response = self.client.get('/invoices/api/invoices', query_string={
                'sort_by': 'date', 'sort_order': 'desc', 'page': '2', 'per_page': '2'
            })
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload['total'], 5)
            self.assertEqual([r['id'] for r in payload['data']], ['e', 'a'])
            response = self.client.get('/invoices/api/invoices', query_string={
                'date_from': '2026-10-01', 'date_to': '2026-10-31',
                'sort_by': 'amount', 'sort_order': 'asc'
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual([r['id'] for r in response.get_json()['data']], ['e', 'b'])

    def test_sort_whitelist_and_bad_calendar_range(self):
        for query in (
            {'sort_by': '__class__'},
            {'sort_order': 'sideways'},
            {'date_from': '2026-02-30'},
            {'date_to': 'invalid'},
        ):
            with self.subTest(query=query), patch.object(invoices, 'scan_invoices',
                                                        return_value=self.rows()):
                self.assertEqual(self.client.get('/invoices/api/invoices',
                                                 query_string=query).status_code, 400)


if __name__ == '__main__':
    unittest.main()
