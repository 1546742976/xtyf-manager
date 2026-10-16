"""Regression checks for the unattended mail systemd installer."""
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch

from deploy.install_mail import install_mail
from deploy import update_release


class MailInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="xtyf-mail-install-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.app = root / "rm-progress"
        deploy = self.app / "deploy"
        deploy.mkdir(parents=True)
        for name in ("mail-163.env.example", "rm-mail.service", "rm-mail.timer"):
            shutil.copyfile(Path(__file__).resolve().parents[1] / "deploy" / name, deploy / name)
        self.config = root / "etc" / "xtyf-manager" / "mail.env"
        self.units = root / "systemd"

    def install(self):
        return install_mail(self.app, "rmuser", config_path=self.config, systemd_dir=self.units)

    def test_fresh_install_does_not_enable_delivery_and_both_services_share_secret(self):
        self.install()
        self.assertIn("MAIL_MODE=off", self.config.read_text())
        self.assertIn("EnvironmentFile=" + str(self.config),
                      (self.units / "rm-mail.service").read_text())
        self.assertIn("EnvironmentFile=" + str(self.config),
                      (self.units / "rm-progress.service.d/zz-xtyf-mail.conf").read_text())
        self.assertIn("User=rmuser", (self.units / "rm-mail.service").read_text())
        self.assertIn("WorkingDirectory=" + str(self.app),
                      (self.units / "rm-mail.service").read_text())
        self.assertIn("OnCalendar=*-*-* *:0/5:00",
                      (self.units / "rm-mail.timer").read_text())
        self.assertFalse((self.units / "timers.target.wants").exists())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)

    def test_repeat_install_does_not_replace_credentials(self):
        self.install()
        self.config.write_text("MAIL_MODE=smtp\nSMTP_PASSWORD=private-authorized-code\n")
        self.install()
        self.assertEqual(self.config.read_text(),
                         "MAIL_MODE=smtp\nSMTP_PASSWORD=private-authorized-code\n")

    def test_legacy_config_migrated_without_deleting_source(self):
        legacy = self.app / "mail.env"
        legacy.write_text("MAIL_MODE=preview\nSMTP_PASSWORD=kept-secret\n")
        self.install()
        self.assertEqual(self.config.read_bytes(), legacy.read_bytes())
        self.assertTrue(legacy.exists())
        self.assertNotIn("kept-secret", (self.units / "rm-mail.service").read_text())

    def test_install_rejects_symlinked_configuration(self):
        self.config.parent.mkdir(parents=True)
        target = self.config.parent / "target"
        target.write_text("MAIL_MODE=off\n")
        try:
            self.config.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("filesystem does not support symlinks")
        with self.assertRaises(RuntimeError):
            self.install()

    def test_installer_is_noop_for_same_templates(self):
        self.install()
        unit = self.units / "rm-mail.timer"
        initial = unit.stat().st_mtime_ns
        self.install()
        self.assertEqual(unit.stat().st_mtime_ns, initial)

    def test_invalid_service_account_is_rejected(self):
        with self.assertRaises(RuntimeError):
            install_mail(self.app, "bad/name", config_path=self.config, systemd_dir=self.units)


class MailDeploymentIntegrationTests(unittest.TestCase):
    def test_deploy_preserves_legacy_environment_and_installs_disabled_units(self):
        content = (Path(__file__).resolve().parents[1] / "deploy/deploy.sh").read_text(encoding="utf-8")
        self.assertIn("--exclude mail.env", content)
        self.assertIn("deploy/install_mail.py", content)
        self.assertNotIn("systemctl enable rm-mail.timer", content)

    def test_updater_suspends_mail_before_backup_and_preserves_legacy_file(self):
        content = (Path(__file__).resolve().parents[1] / "deploy/update_release.py").read_text(encoding="utf-8")
        self.assertLess(content.index("suspend_mail()"), content.index("create_snapshot(APP,"))
        self.assertIn("'mail.env'", content)
        self.assertIn("install_mail(APP, account.pw_name)", content)
        self.assertIn("resume_mail(", content)
        self.assertIn("redacted_runtime", content)



class MailPauseTests(unittest.TestCase):
    def test_suspend_waits_for_worker_without_killing_it(self):
        with patch.object(update_release, "unit_active",
                          side_effect=[True, True, True, False]) as active, \
             patch.object(update_release, "run") as runner, \
             patch.object(update_release.time, "sleep") as sleeping:
            update_release.suspend_mail()
        runner.assert_called_once_with(["systemctl", "stop", "rm-mail.timer"])
        sleeping.assert_called_once_with(2)
        self.assertEqual(active.call_count, 4)

    def test_active_timer_restored_but_disabled_timer_not_enabled(self):
        with patch.object(update_release, "run") as runner:
            update_release.resume_mail(False)
            runner.assert_not_called()
            update_release.resume_mail(True)
            runner.assert_called_once_with(["systemctl", "start", "rm-mail.timer"])

    def test_timeout_cancels_upgrade_instead_of_interrupting_smtp(self):
        with patch.object(update_release, "unit_active", side_effect=lambda name: name == "rm-mail.service"), \
             patch.object(update_release.time, "sleep"), \
             self.assertRaisesRegex(RuntimeError, "Mail worker still active"):
            update_release.suspend_mail()



if __name__ == "__main__":
    unittest.main()
