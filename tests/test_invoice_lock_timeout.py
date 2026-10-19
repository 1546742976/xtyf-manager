"""发票模块：共享锁被长期占用时，请求必须快速返回可重试的 503，而不是挂死。"""
import os
import threading
import time
import unittest

from test_tasks import app_module
from modules import invoices

ADMIN_PASSWORD = os.environ["PROGRESS_INITIAL_ADMIN_PASSWORD"]


class TestInvoiceLockTimeout(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        login = self.client.post('/login', json={'username': 'admin', 'password': ADMIN_PASSWORD})
        self.assertTrue(login.get_json()['ok'], 'admin login failed')
        self._original_timeout = invoices._INVOICE_LOCK_TIMEOUT
        invoices._INVOICE_LOCK_TIMEOUT = 0.3
        self._holding = threading.Event()
        self._release = threading.Event()
        self._holder = threading.Thread(target=self._hold_lock, daemon=True)
        self._holder.start()
        self.assertTrue(self._holding.wait(5), 'lock holder never started')

    def _hold_lock(self):
        with invoices._invoice_lock:
            self._holding.set()
            self._release.wait(15)

    def tearDown(self):
        self._release.set()
        if self._holder.is_alive():
            self._holder.join(15)
        invoices._INVOICE_LOCK_TIMEOUT = self._original_timeout

    def test_api_request_answers_503_when_the_lock_is_saturated(self):
        started = time.monotonic()
        response = self.client.get('/invoices/api/invoices')
        elapsed = time.monotonic() - started
        self.assertEqual(response.status_code, 503, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertFalse(payload['success'])
        self.assertIn('稍后重试', payload['error'])
        self.assertEqual(response.headers['Retry-After'], '5')
        self.assertLess(elapsed, 5, 'request waited far beyond the configured lock timeout')

    def test_requests_succeed_again_after_the_holder_releases(self):
        self.assertEqual(self.client.get('/invoices/api/invoices').status_code, 503)
        self._release.set()
        self._holder.join(15)
        response = self.client.get('/invoices/api/invoices')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertIn('data', payload)
        self.assertEqual(payload['total'], 0)


if __name__ == '__main__':
    unittest.main()
