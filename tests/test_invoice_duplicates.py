"""Number-only duplicate queries preserve finance and upload ownership rules."""
import io
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from test_tasks import app_module


class TestInvoiceDuplicateQueries(unittest.TestCase):
    def setUp(self):
        self.admin = app_module.app.test_client()
        self.admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})

    def test_finance_can_find_pdf_invoice_number_without_a_path(self):
        number = str(int(uuid4().hex[:15], 16)).zfill(20)
        pdf = io.BytesIO()
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        canvas = Canvas(pdf)
        canvas.setFont('STSong-Light', 12)
        canvas.drawString(60, 700, '发票号码：' + number)
        canvas.save()
        pdf.seek(0)
        upload = self.admin.post('/invoices/api/upload', data={'file': (pdf, 'number.pdf')}).get_json()
        confirmed = self.admin.post('/invoices/api/upload/confirm', json={
            'tmp_path': upload['tmp_path'], 'month': '2026.10', 'amount': '3.00',
            'invoice_number': number, 'submitter_id': 1}).get_json()
        self.assertTrue(confirmed['success'], confirmed)
        result = self.admin.get('/invoices/api/check-duplicate', query_string={'invoice_number': number}).get_json()
        self.assertTrue(any(item['path'].replace('\\', '/') == confirmed['rel_path']
                            for item in result['duplicates']), result)
        import builtins
        real_open = builtins.open

        def forbid_pdf(path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and os.fspath(path).lower().endswith('.pdf'):
                raise AssertionError('unchanged duplicate index reread PDF')
            return real_open(path, *args, **kwargs)

        with patch('builtins.open', side_effect=forbid_pdf):
            cached = self.admin.get('/invoices/api/check-duplicate', query_string={'invoice_number': number})
            self.assertEqual(cached.get_json(), result)
        moved = self.admin.post('/invoices/api/invoice/update', json={
            'id': confirmed['rel_path'], 'month': '2026.11', 'invoice_number': '99999999999999999999'}).get_json()
        self.assertTrue(moved['success'])
        refreshed = self.admin.get('/invoices/api/check-duplicate', query_string={'invoice_number': number}).get_json()
        self.assertEqual([item['path'] for item in refreshed['duplicates']], [moved['rel_path']])
        # Manual display corrections do not silently replace the PDF-number rule.
        manual = self.admin.get('/invoices/api/check-duplicate', query_string={'invoice_number': '99999999999999999999'})
        self.assertEqual(manual.get_json()['duplicates'], [])

    def test_member_cannot_query_number_without_own_upload(self):
        name = 'invoice-query-' + uuid4().hex[:10]
        self.admin.post('/api/member', json={'name': name, 'username': name,
                                           'password': 'invoice-query-test', 'role': 'member'})
        member = app_module.app.test_client()
        member.post('/login', json={'username': name, 'password': 'invoice-query-test'})
        response = member.get('/invoices/api/check-duplicate', query_string={'invoice_number': '12345678901234567890'})
        self.assertEqual(response.status_code, 403)
