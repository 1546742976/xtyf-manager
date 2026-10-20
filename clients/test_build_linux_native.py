"""Confirm architecture selection and unsupported-host fail-closed behaviour."""
import unittest
from clients.build_linux_native import architecture


class NativePackagingTests(unittest.TestCase):
    def test_architecture_labels(self):
        self.assertEqual(architecture("x86_64"), "x86_64")
        self.assertEqual(architecture("aarch64"), "arm64")
        with self.assertRaisesRegex(RuntimeError, "native x86_64 or aarch64"):
            architecture("armv7l")


if __name__ == "__main__":
    unittest.main()
