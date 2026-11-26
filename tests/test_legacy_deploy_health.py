"""Run the real legacy shell entry with all privileged commands intercepted."""
import os
import unittest

from reliability_experiments import legacy_deploy


@unittest.skipUnless(os.name == 'posix', 'Linux shell mock harness')
class LegacyDeploymentHealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = legacy_deploy()['cases']

    def test_failure_never_announces_completion(self):
        for case in ('http500', 'connection', 'timeout', 'inactive', 'rsync', 'restart', 'invalid_url'):
            with self.subTest(case=case):
                self.assertNotEqual(self.cases[case]['exit_code'], 0)
                self.assertFalse(self.cases[case]['completion_printed'])

    def test_bounded_probe_supports_delayed_start_and_custom_url(self):
        for case in ('none', 'slow', 'custom_url'):
            with self.subTest(case=case):
                self.assertEqual(self.cases[case]['exit_code'], 0)
                self.assertTrue(self.cases[case]['completion_printed'])
        self.assertEqual(self.cases['slow']['health_attempts'], 3)
        for case in ('http500', 'connection', 'timeout', 'inactive'):
            self.assertEqual(self.cases[case]['health_attempts'], 30)
        command = self.cases['custom_url']['health_commands'][0]
        self.assertIn('http://127.0.0.1:9854/login', command)
        self.assertIn('--connect-timeout 2', command)
        self.assertIn('--max-time 3', command)
        self.assertEqual(self.cases['invalid_url']['health_attempts'], 0)
