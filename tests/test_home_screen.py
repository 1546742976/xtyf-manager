"""Home-screen assets are public; account and business pages remain private."""
import io
import json
import unittest
from urllib.parse import urljoin

from PIL import Image

from test_tasks import app_module


class TestHomeScreen(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def test_public_manifest_and_icon(self):
        response = self.client.get('/static/app.webmanifest')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/manifest+json')
        manifest = json.loads(response.data)
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['start_url'], '/')
        self.assertEqual(manifest['scope'], '/')
        for icon in manifest['icons']:
            path = urljoin('/static/app.webmanifest', icon['src'])
            response = self.client.get(path)
            self.addCleanup(response.close)
            self.assertEqual(response.status_code, 200)
            with Image.open(io.BytesIO(response.data)) as image:
                self.assertEqual(image.format, 'PNG')
                self.assertEqual(f'{image.width}x{image.height}', icon['sizes'])
        # Installation metadata must not open up authenticated pages.
        self.assertEqual(self.client.get('/account').status_code, 302)

    def test_login_and_all_workspace_shells_share_install_metadata(self):
        login = self.client.get('/login')
        self.assertIn(b'app.webmanifest', login.data)
        self.assertIn(b'data-ios-install', login.data)
        result = self.client.post('/login', json={'username': 'admin', 'password': '123456'})
        self.assertEqual(result.status_code, 200)
        for path in ('/', '/account', '/tasks', '/funds/', '/invoices/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'app.webmanifest', response.data)
                self.assertIn(b'apple-touch-icon', response.data)
                self.assertIn(b'apple-mobile-web-app-capable', response.data)
                self.assertIn('no-store', response.headers['Cache-Control'])


if __name__ == '__main__':
    unittest.main()
