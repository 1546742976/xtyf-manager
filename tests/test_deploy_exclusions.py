"""Guard against rsyncing developer secrets into a production deployment."""
from pathlib import Path
import shlex
import unittest

ROOT = Path(__file__).resolve().parents[1]


def excludes():
    script = (ROOT / "deploy/deploy.sh").read_text(encoding="utf-8")
    begin = script.index("sudo rsync -a --delete")
    end = script.index('"$SRC_DIR"/ "$APP_DIR"/', begin)
    text = script[begin + len("sudo rsync -a --delete"):end]
    tokens = shlex.split(text.replace(chr(92) + chr(10), " "))
    values = [token.partition("=")[2] for token in tokens if token.startswith("--exclude=")]
    for index, token in enumerate(tokens[:-1]):
        if token == "--exclude":
            values.append(tokens[index + 1])
    return values


class DeployExclusionsTest(unittest.TestCase):
    def test_signing_and_credentials_are_excluded(self):
        values = excludes()
        for name in ("/clients/signing/", ".env", ".env.*", "initial-admin-password.txt",
                     "mail.env", "secret.key", "data", "data.db"):
            self.assertIn(name, values, f"Unprotected runtime secret: {name}")

    def test_build_products_are_not_deployed(self):
        values = excludes()
        for name in (".venv", ".build-tools", "node_modules", "dist"):
            self.assertIn(name, values, f"Unexpected build product: {name}")

    def test_public_signed_update_bundle_is_kept(self):
        self.assertNotIn("/client-ui-release/", excludes())


if __name__ == "__main__":
    unittest.main()
