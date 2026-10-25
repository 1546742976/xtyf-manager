"""Opt-in real-systemd sandbox probe; never creates/changes installed services.

Run in a transient unit with the shipped hardening, PrivateNetwork=yes and
CAP_NET_BIND_SERVICE. Business fixtures use its approved temporary write root;
Gunicorn heartbeat files use Linux private /tmp (not a Windows-mounted volume).
"""
import http.cookiejar
import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request


def main():
    if not os.environ.get('INVOCATION_ID') or sys.platform != 'linux':
        raise RuntimeError('Only run this probe inside an isolated transient systemd unit')
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root), str(root / 'tests')]
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['MAIL_MODE'] = 'preview'
    with tempfile.TemporaryDirectory(prefix='systemd-invoice-fonts-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import test_tasks
        for name in ('PROGRESS_DB', 'FUNDS_DIR', 'INVOICES_DIR', 'TASK_ATTACHMENTS_DIR', 'PROGRESS_SECRET_PATH'):
            assert Path(os.environ[name]).is_relative_to(test_tasks._TEMP.name)
        try:
            with (root / 'app.py').open('r+b'):
                raise AssertionError('Source tree unexpectedly writable inside hardened unit')
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EROFS): raise
        log = tempfile.TemporaryFile()
        server = subprocess.Popen([sys.executable, '-m', 'gunicorn', '--config', 'deploy/gunicorn.conf.py',
            '--workers', '1', '--threads', '8', '--timeout', '240', '--bind', '127.0.0.1:80', 'app:app'],
            cwd=root, stdout=log, stderr=log,
            env={**os.environ, 'TMPDIR': '/tmp', 'HOME': cache})
        reached_login = False
        try:
            session = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            for _ in range(30):
                try:
                    with session.open('http://127.0.0.1/login', timeout=2) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if server.poll() is not None: raise RuntimeError('Gunicorn exited before health check')
                    time.sleep(0.5)
            else:
                raise RuntimeError('Gunicorn health timeout')
            token = json.load(session.open('http://127.0.0.1/api/csrf'))['csrf_token']
            request = urllib.request.Request('http://127.0.0.1/login',
                data=json.dumps({'username': 'admin', 'password': 'ci-test-password-only'}).encode(),
                headers={'Content-Type': 'application/json', 'X-CSRF-Token': token})
            assert json.load(session.open(request))['ok']
            reached_login = True
            print('REAL_GUNICORN_PORT80_LOGIN_OK', flush=True)
        finally:
            server.terminate()
            try: server.wait(timeout=10)
            except subprocess.TimeoutExpired: server.kill(); server.wait()
            log.seek(0)
            if not reached_login or server.returncode not in (0, -15): print(log.read().decode(errors='replace')[-6000:])
            log.close()
        names = ['test_task_attachments', 'test_daily_projects', 'test_invoice_commit',
                 'test_invoice_job_recovery', 'test_assignment_mail']
        result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
        if not result.wasSuccessful(): raise RuntimeError('Hardened API/worker regressions failed')
        for path in (os.environ['PROGRESS_DB'], str(Path(os.environ['FUNDS_DIR']) / 'funds.db')):
            with sqlite3.connect(path) as db:
                assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        print('SYSTEMD_HARDENING_OK synthetic_sqlite_uploads_invoices_mail tests=' + str(result.testsRun), flush=True)


if __name__ == '__main__':
    main()
