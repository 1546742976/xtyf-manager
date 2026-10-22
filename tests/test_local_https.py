"""PR #52 local HTTPS boundary and certificate tests (no listener or network)."""
import argparse
import tempfile
import unittest
from pathlib import Path

from cryptography import x509
from werkzeug.test import Client
from werkzeug.wrappers import Response

from deploy import local_https


class LocalHTTPSTest(unittest.TestCase):
    def test_restricts_address_and_port_to_lan_configuration(self):
        for address in ("10.0.0.5", "172.16.1.20", "172.31.255.254", "192.168.1.42"):
            self.assertEqual(str(local_https.lan_ip(address)), address)
        for address in ("127.0.0.1", "8.8.8.8", "100.64.0.1",
                        "169.254.1.2", "not-ip", "::1"):
            with self.subTest(address=address), self.assertRaises(argparse.ArgumentTypeError):
                local_https.lan_ip(address)
        for value in ("0", "65536", "bad"):
            with self.subTest(port=value), self.assertRaises(argparse.ArgumentTypeError):
                local_https.port_number(value)
        self.assertEqual(local_https.port_number("8521"), 8521)

    def test_proxy_blocks_unexpected_hosts_and_exposes_only_public_ca(self):
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory) / "root-ca.cer"
            public.write_bytes(b"public-certificate-test")
            app = local_https.make_proxy("192.168.2.8", 8521, 8520, public)
            client = Client(app, Response)
            wrong = client.get("/local-https/root-ca.cer", headers={"Host": "external.example"})
            self.assertEqual(wrong.status_code, 400)
            allowed = client.get("/local-https/root-ca.cer", headers={"Host": "192.168.2.8:8521"})
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.data, b"public-certificate-test")
            self.assertIn("no-store", allowed.headers["Cache-Control"])

    def test_prepared_certificate_reuse_has_private_lan_ip_san(self):
        with tempfile.TemporaryDirectory() as directory:
            where = Path(directory)
            address = local_https.lan_ip("192.168.2.8")
            first = local_https.prepare_certificates(where, address)
            second = local_https.prepare_certificates(where, address)
            self.assertEqual(first[3], second[3])
            self.assertEqual(first[0].read_bytes(), second[0].read_bytes())
            self.assertEqual(first[1].read_bytes(), second[1].read_bytes())
            ca = x509.load_der_x509_certificate(first[2].read_bytes())
            self.assertTrue(ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
            server = x509.load_pem_x509_certificate(first[0].read_bytes())
            san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual(san.get_values_for_type(x509.IPAddress), [address])


if __name__ == "__main__":
    unittest.main()
