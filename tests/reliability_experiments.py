"""Explicit isolated experiments, not a production load test or deployment command.

Run with repository root on PYTHONPATH. --sse requires Linux and existing Gunicorn.
All accounts, purchase information, files, subprocesses and directories are synthetic.
"""
import argparse
import hashlib
import http.cookiejar
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def proxy_limits(app):
    shared = "198.51.100.61"
    client = app.test_client()
    failures = [client.post("/login", json={"username": f"synthetic-{i}",
                "password": "incorrect-password"},
                environ_overrides={"REMOTE_ADDR": shared},
                headers={"X-Forwarded-For": f"203.0.113.{i + 1}"}).status_code
                for i in range(40)]
    credentials = {"username": "admin", "password": "ci-test-password-only"}
    blocked = client.post("/login", json=credentials,
                          environ_overrides={"REMOTE_ADDR": shared}).status_code
    allowed = app.test_client().post("/login", json=credentials,
                  environ_overrides={"REMOTE_ADDR": "198.51.100.62"}).status_code
    assert blocked == 429 and allowed == 200, (blocked, allowed)
    return {"failed_attempts": len(failures), "status_codes": sorted(set(failures)),
            "first_rejection_attempt": next((i + 1 for i, status in enumerate(failures) if status == 429), None),
            "same_proxy_valid_login": blocked, "different_peer_valid_login": allowed,
            "forwarded_header_did_not_bypass": True,
            "risk": "Shared reverse-proxy peers share the IP failure budget."}


def time_semantics():
    import common
    from modules import assets

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = datetime(2026, 10, 24, 17, 0, tzinfo=timezone.utc)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    with patch.object(common, "datetime", FixedDatetime), patch.object(assets, "datetime", FixedDatetime):
        local, beijing = common.now_str(), assets.stamp()
    return {"instant_utc": "2026-10-24T17:00:00Z", "simulated_host_timezone": "UTC",
            "common_now_str": local, "assets_stamp": beijing,
            "host_timezone_unchanged": True, "different_calendar_date": local[:10] != beijing[:10]}


def full_restore(app):
    from deploy.consistent_snapshot import create_snapshot, restore_snapshot, verify_snapshot

    client = app.test_client()
    assert client.post("/login", json={"username": "admin", "password": "ci-test-password-only"}).get_json()["ok"]
    image = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 60, 90)).save(image, "PNG")
    png = image.getvalue()
    avatar = client.post("/api/account/avatar", data={
        "avatar": (io.BytesIO(png), "synthetic.png"),
        "crop": json.dumps({"x": 0, "y": 0, "size": 64})})
    assert avatar.status_code == 200, avatar.get_json()
    task = client.post("/api/task", json={"title": "合成恢复验证任务"}).get_json()["id"]
    attachment = client.post(f"/api/task/{task}/attachments", data={
        "description": "合成恢复图片", "files": (io.BytesIO(png), "synthetic.png")})
    assert attachment.status_code == 200, attachment.get_json()
    pdf = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(pdf, "PDF")
    upload = client.post("/invoices/api/upload", data={
        "file": (io.BytesIO(pdf.getvalue()), "synthetic.pdf")}).get_json()
    invoice = client.post("/invoices/api/upload/confirm", json={
        "tmp_path": upload["tmp_path"], "month": "2026.10", "amount": "12.34", "submitter_id": 1}).get_json()
    assert invoice["success"], invoice
    key = uuid4().hex
    funds = client.post("/funds/api/records", json={
        "request_id": key, "record_type": "进", "amount": "12.34",
        "record_date": "2026-10-24", "source_dest": "合成恢复", "notes": "合成资料"}).get_json()
    assert funds["code"] == 0, funds
    with tempfile.TemporaryDirectory(prefix="xtyf-full-restore-") as directory:
        base = Path(directory)
        source = base / "offline-source"
        source.mkdir()
        with sqlite3.connect(os.environ["PROGRESS_DB"]) as original, sqlite3.connect(source / "data.db") as copied:
            original.backup(copied)
        shutil.copyfile(os.environ["PROGRESS_SECRET_PATH"], source / "secret.key")
        for variable, relative in (("FUNDS_DIR", "data/funds"), ("INVOICES_DIR", "data/invoices"),
                                   ("TASK_ATTACHMENTS_DIR", "data/task_attachments")):
            shutil.copytree(os.environ[variable], source / relative)
        archive = base / "synthetic.zip"
        # This is an offline copy without any writer process; no system service is stopped.
        with patch("deploy.consistent_snapshot.service_is_active", return_value=False):
            manifest = create_snapshot(source, archive, offline_confirmed=True)
        verify_snapshot(archive)
        restored = base / "restored"
        restore_snapshot(archive, restored)
        for relative, metadata in manifest["files"].items():
            assert hashlib.sha256((restored / relative).read_bytes()).hexdigest() == metadata["sha256"], relative
        with sqlite3.connect(restored / "data.db") as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            avatar_key = db.execute("SELECT avatar_key FROM members WHERE id=1").fetchone()[0]
            assert (restored / "data/task_attachments/avatars" / avatar_key).is_file()
            attached = db.execute("SELECT stored_name FROM task_attachments WHERE task_id=?", (task,)).fetchall()
            assert attached and all((restored / "data/task_attachments" / row[0]).is_file() for row in attached)
        with sqlite3.connect(restored / "data/funds/funds.db") as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            receipt = json.loads(db.execute("SELECT result_json FROM funds_requests WHERE request_id=?", (key,)).fetchone()[0])
            assert receipt["id"] == funds["id"]
            assert db.execute("SELECT id FROM records WHERE id=?", (receipt["id"],)).fetchone()
        # Metadata keys and paths must continue pointing at the restored invoice file.
        metadata = json.loads((restored / "data/invoices/.invoice_meta.json").read_text(encoding="utf-8"))
        invoice_paths = [name for name in manifest["files"] if name.endswith(".pdf") and "/.uploads/" not in name]
        assert invoice_paths and all((restored / name).is_file() for name in invoice_paths)
        invoice_meta = metadata[invoice["rel_path"]]
        assert invoice_meta["submitter_id"] == 1
        assert invoice_meta["rel_path"] == invoice["rel_path"]
        assert (restored / "data/invoices" / invoice_meta["rel_path"]).is_file()
        return {"files_verified": len(manifest["files"]), "sqlite_integrity": "both ok",
                "references": ["member avatar", "task attachment", "funds receipt and record", "invoice and metadata"],
                "service_state_check": "mocked for offline synthetic copy", "live_restore_performed": False}


def legacy_deploy():
    if os.name != "posix":
        return {"status": "not run: Linux mock-command harness required"}
    results = {}
    with tempfile.TemporaryDirectory(prefix="xtyf-deploy-mock-") as directory:
        root = Path(directory)
        source, binaries = root / "source", root / "bin"
        (source / "deploy").mkdir(parents=True)
        binaries.mkdir()
        for name in ("deploy.sh", "rm-progress.service"):
            shutil.copyfile(ROOT / "deploy" / name, source / "deploy" / name)
        scripts = {
            "sudo": '''#!/bin/bash
case "$1" in
  mkdir) /bin/mkdir -p "$APP_DIR" ;;
  rsync) [[ "$PROBE_FAILURE" == rsync ]] && exit 23; /bin/cp -R "$PROBE_SOURCE/." "$APP_DIR/" ;;
  tee) cat >/dev/null ;;
  systemctl) [[ "$PROBE_FAILURE" == restart && "$2" == restart ]] && exit 1; echo 'synthetic service status' ;;
  apt-get|python3|chmod|chown|ufw|*/venv/bin/pip) : ;;
  *) echo "Unexpected mock command: $1" >&2; exit 77 ;;
esac
''',
            "git": "#!/bin/bash\nexit 1\n",
            "sleep": "#!/bin/bash\nexit 0\n",
            "curl": '''#!/bin/bash
[[ "$PROBE_FAILURE" == connection ]] && exit 7
if [[ "$PROBE_FAILURE" == http500 ]]; then echo 'HTTP 500'; else echo 'HTTP 200'; fi
''',
        }
        for name, content in scripts.items():
            path = binaries / name
            path.write_text(content, encoding="utf-8")
            path.chmod(0o700)
        for failure in ("none", "http500", "connection", "rsync", "restart"):
            result = subprocess.run(["bash", str(source / "deploy/deploy.sh")], capture_output=True,
                text=True, timeout=15, env={**os.environ, "PATH": str(binaries) + ":/usr/bin:/bin",
                "APP_DIR": str(root / ("app-" + failure)), "APP_USER": "synthetic",
                "PROBE_SOURCE": str(source), "PROBE_FAILURE": failure})
            results[failure] = {"exit_code": result.returncode, "completion_printed": "完成。局域网访问" in result.stdout}
            assert result.returncode != 77, result.stderr
    return {"commands_mocked": True, "cases": results}


def sse_capacity():
    if os.name != "posix":
        raise RuntimeError("SSE experiment requires Linux Gunicorn, not a Flask development server")
    with tempfile.TemporaryDirectory(prefix="xtyf-sse-capacity-") as directory:
        root = Path(directory)
        (root / ".synthetic-only").touch()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        cookies = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies), urllib.request.ProxyHandler({}))

        def request(path, data=None, timeout=2):
            headers = {}
            if data is not None:
                with opener.open(origin + "/api/csrf", timeout=timeout) as response:
                    headers = {"X-CSRF-Token": json.load(response)["csrf_token"], "Content-Type": "application/json"}
            started = time.monotonic()
            body = None if data is None else json.dumps(data).encode()
            with opener.open(urllib.request.Request(origin + path, data=body, headers=headers), timeout=timeout) as response:
                payload = response.read()
            return {"seconds": round(time.monotonic() - started, 4), "body": payload}

        streams = []
        with (root / "gunicorn.log").open("wb") as log:
            process = subprocess.Popen([sys.executable, "-m", "gunicorn", "--config", "deploy/gunicorn.conf.py",
                "--pythonpath", "tests", "--workers", "1", "--threads", "8", "--timeout", "240",
                "--bind", f"127.0.0.1:{port}", "reliability_probe_app:app"], cwd=ROOT,
                env={**os.environ, "XTYF_RELIABILITY_ROOT": str(root)}, stdout=log, stderr=log, start_new_session=True)
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        request("/login")
                        break
                    except (OSError, TimeoutError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError((root / "gunicorn.log").read_text())
                        time.sleep(.1)
                request("/login", {"username": "admin", "password": "synthetic-probe-password"})
                request("/api/password", {"old": "synthetic-probe-password", "new": "synthetic-probe-password-rotated"})
                job = json.loads(request("/invoices/api/job/start", {"type": "refresh"})["body"])["job_id"]
                results = {}
                for count in range(9):
                    if count:
                        stream = opener.open(origin + "/invoices/api/job/stream/" + job, timeout=5)
                        assert stream.readline(), "SSE did not produce an initial message"
                        streams.append(stream)
                    if count in (0, 7, 8):
                        results[str(count)] = {}
                        for path in ("/login", "/api/data"):
                            try:
                                result = request(path)
                                results[str(count)][path] = {"seconds": result["seconds"], "status": "ok"}
                            except (TimeoutError, OSError) as error:
                                results[str(count)][path] = {"status": "timeout/error", "error_type": type(error).__name__}
                for stream in streams:
                    stream.close()
                streams.clear()
                started = time.monotonic()
                while True:
                    try:
                        request("/api/data")
                        results["recovery_seconds"] = round(time.monotonic() - started, 4)
                        break
                    except (TimeoutError, OSError):
                        if time.monotonic() - started > 35:
                            results["recovery_seconds"] = "not recovered within 35 seconds"
                            break
                        time.sleep(.2)
                return {"workers": 1, "threads": 8, "actual_invoice_sse": True, "observations": results}
            finally:
                for stream in streams:
                    stream.close()
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sse", action="store_true")
    args = parser.parse_args()
    # Fixture initializes fresh paths before importing app, never copies an acceptance DB.
    from test_tasks import app_module
    results = {"runtime": sys.version.split()[0], "synthetic_only": True}
    try:
        for name, operation in (("proxy_limits", lambda: proxy_limits(app_module.app)),
                ("time_semantics", time_semantics), ("full_restore", lambda: full_restore(app_module.app)),
                ("legacy_deploy", legacy_deploy)):
            results[name] = operation()
            print(name + " completed", flush=True)
        if args.sse:
            results["sse_capacity"] = sse_capacity()
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
