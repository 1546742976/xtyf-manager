"""Exercise configured health probes without contacting a real service."""
import unittest
from unittest.mock import MagicMock, patch

from deploy import update_release


class DeploymentHealthTests(unittest.TestCase):
    def test_custom_bind_port_is_used_for_health_probe(self):
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.object(update_release.urllib.request, 'urlopen', return_value=response) as opening, \
                patch.object(update_release, 'setting', return_value='active'):
            update_release.healthy('http://127.0.0.1:8520/login')
        opening.assert_called_once_with('http://127.0.0.1:8520/login', timeout=2)

    def test_non_200_does_not_accept_failed_start(self):
        response = MagicMock()
        response.__enter__.return_value.status = 503
        with patch.object(update_release.urllib.request, 'urlopen', return_value=response), \
                patch.object(update_release.time, 'sleep'), self.assertRaises(RuntimeError):
            update_release.healthy('http://127.0.0.1:8520/login')
