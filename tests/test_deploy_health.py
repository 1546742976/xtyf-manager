"""Exercise configured health probes without contacting a real service."""
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import MagicMock, patch

from deploy import update_release


class DeploymentHealthTests(unittest.TestCase):
    def test_redirect_does_not_turn_the_requested_entry_healthy(self):
        seen = []
        redirect = [302]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(redirect[0] if self.path == '/login' else 200)
                if self.path == '/login': self.send_header('Location', '/other')
                self.end_headers()
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for status in (301, 302):
                redirect[0] = status
                with self.subTest(status=status), patch.object(update_release, 'setting', return_value='active'), patch.object(update_release.time, 'sleep'):
                    with self.assertRaises(RuntimeError):
                        update_release.healthy(f'http://127.0.0.1:{server.server_port}/login')
            self.assertNotIn('/other', seen)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_invalid_health_url_fails_before_privileged_changes(self):
        invalid = ['http://localhost:99999/login', 'http://localhost:0/login', 'http://localhost:abc/login',
                   'http://localhost:/login', 'http://u:p@localhost/login', 'http://localhost/login#part',
                   'http://localhost/login#', 'http://local host/login', 'http://localhost/\nlogin',
                   'file:///tmp/login', 'http://-bad/login', 'http://[::1', 'http://999.999.0.1/login']
        for url in invalid:
            with self.subTest(url=url), patch('sys.argv', ['update_release.py', '--health-url', url]), \
                    patch.object(update_release, 'run') as run, patch.object(update_release, 'setting') as setting:
                with self.assertRaises(SystemExit): update_release.main()
                run.assert_not_called(); setting.assert_not_called()

    def test_custom_bind_port_is_used_for_health_probe(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch('urllib.request.OpenerDirector.open', return_value=response) as opening, \
                patch.object(update_release, 'setting', return_value='active'):
            update_release.healthy('http://127.0.0.1:8520/login')
        opening.assert_called_once_with('http://127.0.0.1:8520/login', timeout=2)

    def test_non_200_does_not_accept_failed_start(self):
        response = MagicMock()
        response.__enter__.return_value.status = 503
        with patch('urllib.request.OpenerDirector.open', return_value=response), \
                patch.object(update_release.time, 'sleep'), self.assertRaises(RuntimeError):
            update_release.healthy('http://127.0.0.1:8520/login')

    def test_default_port_80_and_inactive_or_unreachable_service(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch('urllib.request.OpenerDirector.open', return_value=response) as opening, patch.object(update_release, 'setting', return_value='active'):
            update_release.healthy()
        opening.assert_called_once_with('http://127.0.0.1/login', timeout=2)
        with patch('urllib.request.OpenerDirector.open', return_value=response), patch.object(update_release, 'setting', return_value='inactive'), patch.object(update_release.time, 'sleep'):
            with self.assertRaises(RuntimeError): update_release.healthy()
        with patch('urllib.request.OpenerDirector.open', side_effect=OSError('synthetic offline')), patch.object(update_release.time, 'sleep'):
            with self.assertRaises(RuntimeError): update_release.healthy()

    def test_shared_validator_accepts_real_hosts_and_ports(self):
        for url in ['http://127.0.0.1/login', 'http://localhost:80/login', 'https://example.com/login', 'http://[::1]:80/login', 'https://example.com:65535/login']:
            self.assertEqual(update_release.validate_health_url(url), url)
