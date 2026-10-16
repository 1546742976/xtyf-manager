#!/usr/bin/env python3
"""Idempotently install systemd mail units without enabling email delivery.

Configuration is stored outside the release tree so upgrades and rollbacks do
not replace SMTP credentials. Run as root on the actual Linux server.
"""
import argparse
import os
from pathlib import Path
import tempfile

CONFIG_PATH = Path("/etc/xtyf-manager/mail.env")
SYSTEMD_DIR = Path("/etc/systemd/system")
DROPIN_NAME = "zz-xtyf-mail.conf"
HEADER = "# Managed by xtyf-manager deploy/install_mail.py; do not edit here.\n"


def _regular(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Refusing unsafe mail configuration/unit path: {path}")


def _atomic_unit(path, content):
    _regular(path)
    data = content.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".xtyf-mail-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.chmod(temporary, 0o644)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_mail(app_dir, app_user, *, config_path=CONFIG_PATH, systemd_dir=SYSTEMD_DIR):
    """Create a disabled config if missing, then install/update managed units.

    Never overwrite an existing SMTP config. An old in-tree mail.env is copied
    (not removed), so existing manual drop-ins keep working across upgrades.
    """
    app_dir = Path(app_dir)
    config_path = Path(config_path)
    systemd_dir = Path(systemd_dir)
    if not app_dir.is_absolute() or not app_dir.is_dir() or not app_user or any(
        char in app_user for char in "\n\r /"
    ):
        raise RuntimeError("Invalid application path or service account")
    templates = app_dir / "deploy"
    if not templates.is_dir():
        raise RuntimeError("Mail systemd templates missing")

    if config_path.parent.is_symlink():
        raise RuntimeError("Mail configuration directory must not be a symlink")
    config_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(config_path.parent, 0o700)
    _regular(config_path)
    if not config_path.exists():
        legacy = app_dir / "mail.env"
        _regular(legacy)
        source = legacy if legacy.exists() else templates / "mail-163.env.example"
        _regular(source)
        with config_path.open("xb") as dest:
            os.chmod(config_path, 0o600)
            dest.write(source.read_bytes())
    os.chmod(config_path, 0o600)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.chown(config_path, 0, 0)

    service = (templates / "rm-mail.service").read_text(encoding="utf-8")
    service = (service.replace("{{APP_USER}}", app_user)
                      .replace("{{APP_DIR}}", str(app_dir))
                      .replace("{{MAIL_ENV}}", str(config_path)))
    if "{{" in service or "}}" in service or str(config_path) not in service:
        raise RuntimeError("Unresolved mail service template")
    timer = (templates / "rm-mail.timer").read_text(encoding="utf-8")
    _atomic_unit(systemd_dir / "rm-mail.service", HEADER + service)
    _atomic_unit(systemd_dir / "rm-mail.timer", HEADER + timer)
    _atomic_unit(systemd_dir / "rm-progress.service.d" / DROPIN_NAME,
                 HEADER + "[Service]\nEnvironmentFile=" + str(config_path) + "\n")
    return config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", required=True, type=Path)
    parser.add_argument("--app-user", required=True)
    args = parser.parse_args()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        parser.error("Install mail units as root")
    import pwd
    account = pwd.getpwnam(args.app_user)
    if account.pw_uid == 0:
        parser.error("The application must use a non-root service account")
    install_mail(args.app_dir, args.app_user)
    print("Mail units installed; SMTP configuration preserved; timer state unchanged.")
    print("Config file: " + str(CONFIG_PATH))
    print("New installations default to MAIL_MODE=off and timer disabled.")


if __name__ == "__main__":
    main()
