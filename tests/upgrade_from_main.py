"""Exercise an actual PR upgrade from the exact main commit the PR targets.

The CI workflow checks out the base commit into .ci-main-baseline. This test
starts *that version* of app.py on a private populated SQLite database, then
starts the PR version against the same on-disk database. No production data or
SMTP service is touched. This file is run explicitly by the PR-only workflow.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]
BASE = REPO / ".ci-main-baseline"

SEED_OLD_DATABASE = r"""
import hashlib
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from werkzeug.security import generate_password_hash

import app  # The *main* version creates and migrates the real database.
from modules import mail_service

db = sqlite3.connect(os.environ["PROGRESS_DB"])
try:
    original_columns = {c[1] for c in db.execute("PRAGMA table_info(member_email)")}
    legacy_link = hasattr(mail_service, "verification_token")
    assert ("verify_attempts" not in original_columns) if legacy_link else ("verify_attempts" in original_columns)
    Path(os.environ["XTYF_UPGRADE_CHALLENGE_KIND"]).write_text(
        "legacy-link" if legacy_link else "numeric-code", encoding="utf-8")
    now = time.time()
    db.execute("UPDATE members SET must_change_password=0 WHERE id=1")
    for member_id in (2, 3):
        db.execute(
            "INSERT INTO members(id,name,username,password_hash,created_at,must_change_password) "
            "VALUES(?,?,?,?,?,0)",
            (member_id, "Existing Member " + str(member_id),
             "upgrade_member_" + str(member_id),
             generate_password_hash("upgrade-fixture-password"),
             "2026-10-16 09:00:00"),
        )
    # An existing verified but paused address must survive unchanged.
    db.execute(
        "INSERT INTO member_email(member_id,address,verified,paused,generation,requested_at) "
        "VALUES(1,'verified@example.test',1,1,'verified-existing',?)", (now-300,))
    old_generation = "legacy-link-pending"
    with app.app.app_context():
        old_hash = (hashlib.sha256(mail_service.verification_token(old_generation).encode()).hexdigest()
                    if legacy_link else mail_service.verification_digest(
                        old_generation, mail_service.verification_code(old_generation)))
    db.execute(
        "INSERT INTO member_email(member_id,address,verified,paused,generation,token_hash,expires_at,requested_at) "
        "VALUES(2,'old-unverified@example.test',0,0,?,?,?,?)",
        (old_generation, old_hash, now+1800, now-120),
    )
    db.execute(
        "INSERT INTO member_email(member_id,address,verified,paused,generation,requested_at) "
        "VALUES(3,'unverified-without-token@example.test',0,1,'no-token',?)", (now-120,))
    db.execute(
        "INSERT INTO mail_jobs(member_id,kind,day,dedupe_key,generation,state,attempts,next_at,created_at) "
        "VALUES(2,'verify','2026-10-16','verify:legacy-link-pending',?,'pending',0,?,?)",
        (old_generation, now-120, now-120),
    )
    db.execute(
        "INSERT INTO mail_jobs(member_id,kind,day,dedupe_key,generation,state,attempts,next_at,created_at,finished_at) "
        "VALUES(1,'daily','2026-10-16','daily:2026-10-16:1','verified-existing','sent',1,?,?,?)",
        (now-600, now-600, now-590),
    )
    db.execute(
        "INSERT INTO daily_projects(id,title,detail,created_at,updated_at) "
        "VALUES(101,'Pre-upgrade shared project','Retain during mail schema migration',?,?)",
        ("2026-10-16 09:00:00", "2026-10-16 09:00:00"),
    )
    db.execute(
        "INSERT INTO daily_groups(project_id,stage,enabled_at) VALUES(101,'vision',?)",
        ("2026-10-16 09:00:00",),
    )
    db.execute(
        "INSERT INTO daily_owners(project_id,stage,member_id) VALUES(101,'vision',2)")
    db.commit()
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
finally:
    db.close()
"""

CHECK_UPGRADED_DATABASE = r"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import app  # The *PR* code performs additive migrations on the baseline DB.
from modules.mail_service import verification_code, process_job
from modules.daily_projects import TZ

connection = sqlite3.connect(os.environ["PROGRESS_DB"])
connection.row_factory = sqlite3.Row
try:
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert "verify_attempts" in {c[1] for c in connection.execute("PRAGMA table_info(member_email)")}
    assert connection.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 3
    verified = connection.execute("SELECT * FROM member_email WHERE member_id=1").fetchone()
    assert (verified["address"], verified["verified"], verified["paused"], verified["generation"]) == (
        "verified@example.test", 1, 1, "verified-existing")
    unverified = connection.execute("SELECT * FROM member_email WHERE member_id=2").fetchone()
    assert (unverified["address"], unverified["verified"], unverified["paused"], unverified["generation"]) == (
        "old-unverified@example.test", 0, 0, "legacy-link-pending")
    old_is_link = Path(os.environ["XTYF_UPGRADE_CHALLENGE_KIND"]).read_text(encoding="utf-8") == "legacy-link"
    if old_is_link:
        # Legacy link hashes must be invalidated, not reinterpreted as OTP verifiers.
        assert unverified["token_hash"] is None and unverified["expires_at"] == 0
    else:
        # On subsequent PRs, a valid numeric-code challenge must survive unchanged.
        from modules.mail_service import verification_digest
        with app.app.app_context():
            expected_hash = verification_digest(
                unverified["generation"], verification_code(unverified["generation"]))
        assert unverified["token_hash"] == expected_hash
        assert unverified["expires_at"] > 0
    assert unverified["verify_attempts"] == 0
    no_token = connection.execute("SELECT * FROM member_email WHERE member_id=3").fetchone()
    assert no_token["address"] == "unverified-without-token@example.test"
    assert no_token["paused"] == 1 and no_token["token_hash"] is None
    assert connection.execute("SELECT title FROM daily_projects WHERE id=101").fetchone()[0] == (
        "Pre-upgrade shared project")
    assert connection.execute("SELECT member_id FROM daily_owners WHERE project_id=101").fetchone()[0] == 2
    sent = connection.execute("SELECT state,attempts FROM mail_jobs WHERE dedupe_key='daily:2026-10-16:1'").fetchone()
    assert (sent["state"], sent["attempts"]) == ("sent", 1)
    old = dict(connection.execute("SELECT * FROM mail_jobs WHERE dedupe_key='verify:legacy-link-pending'").fetchone())
    assert old["state"] == "pending"
    if old_is_link:
        attempted = []
        with app.app.app_context():
            assert process_job(connection, old | {"attempts": 1}, datetime.now(TZ), attempted.append) == "skipped"
        assert not attempted, "Migrated legacy link must never be emailed or accepted"

    # Re-issue through the real API in the upgraded application, then verify.
    client = app.app.test_client()
    csrf = client.get("/api/csrf").get_json()["csrf_token"]
    response = client.post("/login", json={
        "username": "upgrade_member_2", "password": "upgrade-fixture-password",
    }, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200 and response.get_json()["ok"], response.get_data(as_text=True)
    # Login clears the anonymous session, including its CSRF token.
    csrf = client.get("/api/csrf").get_json()["csrf_token"]
    requested = client.post("/api/account/email/request",
        json={"email": "new-address@example.test"},
        headers={"X-CSRF-Token": csrf})
    assert requested.status_code == 202, requested.get_data(as_text=True)
    new = connection.execute("SELECT * FROM member_email WHERE member_id=2").fetchone()
    assert new["address"] == "new-address@example.test"
    assert new["generation"] != "legacy-link-pending" and new["verify_attempts"] == 0
    with app.app.app_context():
        code = verification_code(new["generation"])
    wrong_link = client.post("/api/account/email/verify",
        json={"token": "a" * 64}, headers={"X-CSRF-Token": csrf})
    assert wrong_link.status_code == 400
    verified_response = client.post("/api/account/email/verify",
        json={"code": code}, headers={"X-CSRF-Token": csrf})
    assert verified_response.status_code == 200, verified_response.get_data(as_text=True)
    current = connection.execute("SELECT verified,token_hash FROM member_email WHERE member_id=2").fetchone()
    assert current["verified"] == 1 and current["token_hash"] is None
    assert connection.execute("SELECT COUNT(*) FROM mail_jobs WHERE dedupe_key='daily:2026-10-16:1' AND state='sent'").fetchone()[0] == 1
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
finally:
    connection.close()
"""

CHECK_RESTART = r"""
import os
import sqlite3
import app  # Import re-runs every schema migration; it must be idempotent.
db = sqlite3.connect(os.environ["PROGRESS_DB"])
try:
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM member_email WHERE verified=1").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM mail_jobs WHERE dedupe_key='daily:2026-10-16:1' AND state='sent'").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM daily_projects WHERE id=101").fetchone()[0] == 1
finally:
    db.close()
"""


class UpgradeFromMainTests(unittest.TestCase):
    def test_migrate_real_base_snapshot_with_existing_data_and_pending_link(self):
        expected = os.environ.get("XTYF_UPGRADE_BASE_SHA", "")
        self.assertRegex(expected, r"^[0-9a-f]{40}$", "CI must supply the PR base SHA")
        self.assertTrue((BASE / "app.py").is_file(), "CI did not checkout its main baseline")
        actual = subprocess.check_output(
            ["git", "-C", str(BASE), "rev-parse", "HEAD"], text=True).strip()
        self.assertEqual(actual, expected, "Upgrade baseline must be the exact PR base commit")
        with tempfile.TemporaryDirectory(prefix="xtyf-upgrade-main-") as temp:
            root = Path(temp)
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            env.update({
                "PROGRESS_DB": str(root / "data.db"),
                "PROGRESS_SECRET_PATH": str(root / "secret.key"),
                "PROGRESS_INITIAL_ADMIN_PASSWORD": "ci-upgrade-fixture-only",
                "FUNDS_DIR": str(root / "funds"),
                "INVOICES_DIR": str(root / "invoices"),
                "TASK_ATTACHMENTS_DIR": str(root / "attachments"),
                "MAIL_MODE": "preview",
                "PUBLIC_BASE_URL": "https://team.example.test",
                "XTYF_UPGRADE_CHALLENGE_KIND": str(root / "challenge-kind.txt"),
            })
            for label, checkout, source in (
                ("seed original main database", BASE, SEED_OLD_DATABASE),
                ("upgrade and exercise PR verification", REPO, CHECK_UPGRADED_DATABASE),
                ("restart upgraded version", REPO, CHECK_RESTART),
            ):
                done = subprocess.run(
                    [sys.executable, "-c", textwrap.dedent(source)],
                    cwd=checkout, env=env, capture_output=True, text=True,
                    timeout=120,
                )
                self.assertEqual(done.returncode, 0,
                    f"{label} failed (exit {done.returncode})\nstdout:\n{done.stdout}\nstderr:\n{done.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
