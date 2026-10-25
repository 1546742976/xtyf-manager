"""Forwarded addresses only affect source buckets through explicit proxy trust."""
import ipaddress
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from test_tasks import app_module, _TEMP
from modules.client_address import client_address, parse_trusted_proxies
from modules import security


class TrustedProxyIntegrationTests(unittest.TestCase):
    def setUp(self):
        # test_tasks creates this TemporaryDirectory before importing the app.
        # Refuse any DB path not exactly the synthetic fixture's data.db.
        self.assertEqual(Path(app_module.DB_PATH).resolve(), (Path(_TEMP.name) / 'data.db').resolve())
        with closing(sqlite3.connect(app_module.DB_PATH)) as db, db:
            db.execute('DELETE FROM login_attempts')

    tearDown = setUp

    def test_trusted_clients_get_separate_ip_buckets(self):
        client = app_module.app.test_client()
        config = {'TRUSTED_PROXY_NETWORKS': (ipaddress.ip_network('10.0.0.1/32'),)}
        with patch.dict(app_module.app.config, config), patch.object(app_module, '_LOGIN_IP_LIMIT', 3):
            for index in range(3):
                reply = client.post('/login', json={'username': f'proxy_unknown_{index}', 'password': 'bad-password'},
                                    headers={'X-Forwarded-For': '192.0.2.1'}, environ_overrides={'REMOTE_ADDR': '10.0.0.1'})
            self.assertEqual(reply.status_code, 429)
            reply = client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'},
                                headers={'X-Forwarded-For': '192.0.2.2'}, environ_overrides={'REMOTE_ADDR': '10.0.0.1'})
            self.assertEqual(reply.status_code, 200)
            self.assertTrue(reply.get_json()['ok'])

    def test_default_ignores_forged_headers(self):
        keys = []
        with patch.dict(app_module.app.config, {'TRUSTED_PROXY_NETWORKS': ()}):
            for header in ('192.0.2.1', '192.0.2.2'):
                with app_module.app.test_request_context('/', headers={'X-Forwarded-For': header},
                                                         environ_overrides={'REMOTE_ADDR': '192.0.2.10'}):
                    keys.append(app_module._login_attempt_key('name'))
        self.assertEqual(keys[0], keys[1])

    def resolve(self, peer, header, config='10.0.0.0/8,2001:db8:1::/48'):
        with patch.dict(app_module.app.config, {'TRUSTED_PROXY_NETWORKS': parse_trusted_proxies(config)}):
            with app_module.app.test_request_context('/', headers={'X-Forwarded-For': header},
                                                     environ_overrides={'REMOTE_ADDR': peer}):
                return client_address()

    def test_rightmost_untrusted_hop_blocks_spoofed_left_address(self):
        self.assertEqual(self.resolve('10.0.0.1', '198.51.100.9,192.0.2.2,10.0.0.2'), '192.0.2.2')
        self.assertEqual(self.resolve('192.0.2.1', '198.51.100.9'), '192.0.2.1')
        self.assertEqual(self.resolve('10.0.0.1', '10.0.0.2'), '10.0.0.1')

    def test_ipv6_and_mapped_ipv4(self):
        self.assertEqual(self.resolve('2001:db8:1::1', '2001:db8:2::9,2001:db8:1::2'), '2001:db8:2::9')
        self.assertEqual(self.resolve('::ffff:10.0.0.1', '::ffff:192.0.2.9'), '192.0.2.9')

    def test_invalid_or_oversized_chain_uses_peer(self):
        for header in ('', 'unknown', '192.0.2.9,', 'bad,192.0.2.9', '[2001:db8::1]',
                       'fe80::1%eth0', '192.0.2.9:123', '192.0.2.9,' * 17,
                       ' ' * 2049 + '192.0.2.9'):
            with self.subTest(header=header[:40]):
                self.assertEqual(self.resolve('10.0.0.1', header), '10.0.0.1')

    def test_invalid_config_and_combined_global_trust_rejected(self):
        self.assertEqual(parse_trusted_proxies(''), ())
        for config in ('invalid', ',', '10.0.0.1/8', '0.0.0.0/0', '::/0', '0.0.0.0/1,128.0.0.0/1',
                       '10.0.0.1,', '::ffff:10.0.0.1/128'):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, 'TRUSTED_PROXY_CIDRS'):
                parse_trusted_proxies(config)

    def test_invalid_config_blocks_application_startup(self):
        result = subprocess.run([sys.executable, '-c', 'import app'], capture_output=True, text=True,
                                env={**os.environ, 'TRUSTED_PROXY_CIDRS': '0.0.0.0/0'}, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('TRUSTED_PROXY_CIDRS', result.stderr)

    def test_audit_throttle_shares_effective_source_without_trusting_host(self):
        from flask import request
        with patch.dict(app_module.app.config, {'TRUSTED_PROXY_NETWORKS': parse_trusted_proxies('10.0.0.1')}), \
                patch.object(security, '_audit_rate', {}):
            for forwarded in ('192.0.2.1', '192.0.2.2'):
                with app_module.app.test_request_context('/', environ_overrides={'REMOTE_ADDR': '10.0.0.1'},
                        headers={'X-Forwarded-For': forwarded, 'X-Forwarded-Host': 'attacker.invalid',
                                 'X-Forwarded-Proto': 'https'}):
                    self.assertTrue(security._limited_audit_event('test-proxy'))
                    self.assertFalse(security._limited_audit_event('test-proxy'))
                    self.assertEqual(request.host, 'localhost')
                    self.assertEqual(request.scheme, 'http')


if __name__ == '__main__':
    unittest.main()
