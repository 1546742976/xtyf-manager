"""Install metadata, privacy headers, deployment contents and content revisions."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from test_tasks import app_module
from modules.pwa import PUBLIC_FILES, MAX_CACHE_BYTES, build_config
from deploy import package_release

ROOT = Path(__file__).resolve().parents[1]


class TestPWA(unittest.TestCase):
    def test_public_metadata_never_sets_a_session_or_requires_password_change(self):
        for client in (app_module.app.test_client(), app_module.app.test_client()):
            for path, mime in (('/manifest.webmanifest', 'application/manifest+json'), ('/sw.js', 'text/javascript')):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.mimetype, mime)
                    self.assertEqual(response.headers['Cache-Control'], 'no-cache')
                    self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                    self.assertNotIn('Set-Cookie', response.headers)
                    self.assertNotIn('X-CSRF-Token', response.headers)
        client = app_module.app.test_client()
        client.auto_onboard = False
        # Use the existing initial-password gate, not a fabricated session key.
        admin = app_module.app.test_client()
        admin.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'})
        from uuid import uuid4
        username = 'pwa_gate_' + uuid4().hex[:12]
        admin.post('/api/member', json={'name': 'PWA gate', 'username': username, 'password': 'pwa-initial-password'})
        client.post('/login', json={'username': username, 'password': 'pwa-initial-password'})
        self.assertEqual(client.get('/tasks').status_code, 302)
        self.assertEqual(client.get('/sw.js').headers['Service-Worker-Allowed'], '/')

    def test_manifest_and_real_icons_are_same_origin_with_safe_mask(self):
        data = app_module.app.test_client().get('/manifest.webmanifest').get_json()
        # Flask recognizes application/*+json.
        self.assertEqual((data['id'], data['start_url'], data['scope'], data['display']), ('/', '/', '/', 'standalone'))
        self.assertEqual(data['name'], '巡天御风管理系统')
        self.assertEqual([i['sizes'] for i in data['icons']], ['192x192', '512x512', '512x512'])
        for icon in data['icons']:
            with app_module.app.test_client().get(icon['src']) as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'image/png')
                image = Image.open(io.BytesIO(response.data)).convert('RGB')
            self.assertEqual(image.size, tuple(map(int, icon['sizes'].split('x'))))
            if icon['purpose'] == 'maskable':
                background = image.getpixel((0, 0))
                for y in range(512):
                    for x in range(512):
                        if image.getpixel((x, y)) != background:
                            self.assertLessEqual((x - 255.5) ** 2 + (y - 255.5) ** 2, (512 * .4) ** 2)

    def test_every_shell_has_pwa_links_and_private_responses_stay_no_store(self):
        client = app_module.app.test_client()
        self.assertEqual(client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).status_code, 200)
        for path in ('/', '/tasks', '/account', '/assets', '/members', '/manage', '/funds/', '/invoices/',
                     '/discussions', '/updates', '/admin/security', '/missing-pwa-page'):
            response = client.get(path)
            self.assertIn('rel="manifest" href="/manifest.webmanifest"', response.text, path)
            self.assertIn('/static/pwa.js?v=', response.text, path)
            self.assertEqual(response.headers['Cache-Control'], 'no-store', path)
        self.assertIn('rel="manifest"', app_module.app.test_client().get('/login').text)
        for path in ('/api/data', '/api/account/preferences', '/api/csrf', '/invoices/api/invoices'):
            self.assertEqual(client.get(path).headers['Cache-Control'], 'no-store', path)
        self.assertEqual(client.post('/api/account/preferences', json={'theme': 'dark'},
                                     headers={'X-CSRF-Token': 'bad'}).status_code, 403)
        client.post('/logout')
        self.assertIn(client.get('/tasks').status_code, (302, 401))
        self.assertEqual(client.get('/api/data').status_code, 401)

    def test_allowlist_is_bounded_and_changes_in_assets_or_worker_change_version(self):
        config, _ = build_config(ROOT)
        self.assertLessEqual(sum(item['size'] for item in config['resources'].values()), MAX_CACHE_BYTES)
        self.assertEqual(set(config['resources']), {'/static/' + name for name in PUBLIC_FILES})
        self.assertNotIn('/static/asset-camera.js', config['resources'])
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            for name in (*PUBLIC_FILES, 'pwa/sw.js'):
                target = temporary / 'static' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / 'static' / name, target)
            self.assertEqual(build_config(temporary)[0], config)
            with (temporary / 'static/pwa.css').open('ab') as stream:
                stream.write(b'\n/* changed deployment */')
            changed = build_config(temporary)[0]['version']
            self.assertNotEqual(changed, config['version'])
            with (temporary / 'static/pwa/sw.js').open('ab') as stream:
                stream.write(b'\n/* changed policy */')
            self.assertNotEqual(changed, build_config(temporary)[0]['version'])
            (temporary / 'static/pwa.css').write_bytes(b'x' * (512 * 1024 + 1))
            with self.assertRaisesRegex(RuntimeError, 'limit'):
                build_config(temporary)

    def test_head_source_archive_contains_every_pwa_byte_without_building_clients(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for name in package_release.PWA_REQUIRED_FILES:
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            def git(*args):
                return subprocess.run(['git', *args], cwd=source, check=True, capture_output=True)
            git('init')
            git('config', 'core.autocrlf', 'false')
            git('add', '.')
            git('-c', 'user.name=PWA test', '-c', 'user.email=pwa@example.invalid', 'commit', '-m', 'public fixtures')
            with patch.object(package_release, 'ROOT', source):
                raw = package_release.archive_head('HEAD')
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                for name in package_release.PWA_REQUIRED_FILES:
                    self.assertEqual(archive.extractfile(name).read(), (ROOT / name).read_bytes())

    def test_opt_in_secure_cookie_and_safe_worker_retirement(self):
        app = app_module.app
        client = app.test_client()
        for secure in (False, True):
            with patch.dict(app.config, SESSION_COOKIE_SECURE=secure):
                response = app.test_client().get('/api/csrf')
                self.assertEqual('; Secure;' in response.headers['Set-Cookie'], secure)
        with patch.dict(app.config, PWA_ENABLED=False):
            response = client.get('/sw.js')
            self.assertIn('registration.unregister()', response.text)
            self.assertIn("name.startsWith('xtyf-pwa-public-')", response.text)
            self.assertNotIn('fetch', response.text)
            self.assertNotIn('rel="manifest"', client.get('/login').text)
        worker = client.get('/sw.js').text
        self.assertNotIn('__PWA_CONFIG__', worker)
        self.assertIn(app.extensions['pwa']['version'], worker)
        self.assertIsInstance(json.loads(worker.split('const CONFIG = ', 1)[1].split(';\n', 1)[0]), dict)

    def test_native_local_ui_excludes_web_only_installer(self):
        from clients.build_frontend import adapt_template
        for name in ('_pwa_head.html', '_pwa_install.html'):
            self.assertEqual(adapt_template(name, (ROOT / 'templates' / name).read_text(encoding='utf-8')), '')
