#!/usr/bin/env python3
"""Update the existing default systemd installation from an extracted release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import tempfile
import subprocess
import sys
import time
import threading
import urllib.parse
import urllib.request
from contextlib import closing, contextmanager
from datetime import datetime
if __package__:
    from .install_mail import install_mail
    from .health_url import validate_health_url
else:
    from install_mail import install_mail
    from health_url import validate_health_url

APP = Path('/opt/rm-progress')
SERVICE = 'rm-progress'
WEB_UNIT = Path('/etc/systemd/system/rm-progress.service')
RELEASE = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def output(args):
    return subprocess.check_output([str(a) for a in args], text=True).strip()


def setting(key):
    return output(['systemctl', 'show', SERVICE, '-p', key, '--value'])


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def protect_recovery():
    """A second Ctrl+C must not abandon a half-switched installation."""
    handlers = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def install_dependencies(python, requirements, log_path):
    """Install while the old service is online, with live output and a total deadline."""
    # Match the initial installer without changing the server's global pip config.
    index_url = os.environ.get('PIP_INDEX_URL', '').strip() or 'https://pypi.tuna.tsinghua.edu.cn/simple'
    args = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
            '--progress-bar', 'off', '--timeout', '30', '--retries', '2',
            '--index-url', index_url,
            '-r', str(requirements)]
    print('Preparing dependencies while the current service remains online.', flush=True)
    print('Dependency log: ' + str(log_path), flush=True)
    with log_path.open('w') as log:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors='replace', bufsize=1, start_new_session=True)
        def relay():
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end='', flush=True)
        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        deadline = time.monotonic() + 900
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Dependency installation exceeded 15 minutes; old service remains online')
                try:
                    code = process.wait(timeout=min(15, remaining))
                    break
                except subprocess.TimeoutExpired:
                    print('Dependency installation still running; old service remains online...', flush=True)
            if code:
                raise subprocess.CalledProcessError(code, args)
        except BaseException:
            with protect_recovery():
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            raise
        finally:
            reader.join(timeout=5)
            process.stdout.close()


def check_release():
    manifest = json.loads((RELEASE / 'release-manifest.json').read_text())
    if manifest.get('format') != 1 or not re.fullmatch('[0-9a-f]{40,64}', manifest.get('commit', '')):
        raise RuntimeError('Invalid release manifest')
    for name, expected in manifest['files'].items():
        path = RELEASE / name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(RELEASE) or sha(path) != expected:
            raise RuntimeError('Release checksum/path mismatch: ' + name)
    for name in ('app.py', 'common.py', 'requirements.txt', 'deploy/update_release.py', 'deploy/consistent_snapshot.py', 'deploy/install_mail.py'):
        if name not in manifest['files']:
            raise RuntimeError('Missing release file: ' + name)
    return manifest



def render_web_unit(template, service_user):
    """Render only the two supported systemd substitutions from the release."""
    source = template.read_text(encoding='utf-8')
    rendered = (source.replace('{{APP_USER}}', service_user)
                      .replace('{{APP_DIR}}', str(APP)))
    if '{{' in rendered or '}}' in rendered:
        raise RuntimeError('Unresolved placeholder in web service template')
    return rendered.encode('utf-8')


def plan_web_unit_update(service_user, *, installed_unit=WEB_UNIT):
    """Allow a template revision only if the live unit matches the old template.

    An administrator-customized unit must not be overwritten silently.
    """
    original = APP / 'deploy/rm-progress.service'
    replacement = RELEASE / 'deploy/rm-progress.service'
    if original.read_bytes() == replacement.read_bytes():
        return None
    if installed_unit.is_symlink() or not installed_unit.is_file():
        raise RuntimeError('Web systemd unit is missing or symlinked; review manually: ' + str(installed_unit))
    existing = installed_unit.read_bytes()
    if existing != render_web_unit(original, service_user):
        raise RuntimeError(
            'Web service template changed but active systemd unit does not match '
            'the previous generated template; preserve customizations and '
            'review /etc/systemd/system/rm-progress.service before updating')
    return existing, render_web_unit(replacement, service_user)


def replace_web_unit(path, content):
    """Atomically install or restore the checked web unit; never follow symlinks."""
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('Refusing unsafe web systemd unit: ' + str(path))
    fd, temporary = tempfile.mkstemp(prefix='.rm-progress-unit-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.chmod(temporary, 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoHealthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def healthy(url='http://127.0.0.1/login'):
    validate_health_url(url)
    opener = urllib.request.build_opener(NoHealthRedirect())
    for attempt in range(30):
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200 and setting('ActiveState') == 'active':
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('Service failed to start or /login did not return HTTP 200')



def unit_active(name):
    return subprocess.run(['systemctl', 'is-active', '--quiet', name], check=False,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def suspend_mail():
    """Stop new timer launches and let an in-flight SMTP batch finish safely."""
    if unit_active('rm-mail.timer'):
        run(['systemctl', 'stop', 'rm-mail.timer'])
    if unit_active('rm-mail.service'):
        print('Waiting for the current mail batch to finish before backup...', flush=True)
    for _ in range(600):
        if not unit_active('rm-mail.service'):
            return
        time.sleep(2)
    raise RuntimeError('Mail worker still active after 20 minutes; backup cancelled')


def resume_mail(was_active):
    """Do not change the timer enable/disable choice made by the operator."""
    if was_active:
        run(['systemctl', 'start', 'rm-mail.timer'])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--health-url', default='http://127.0.0.1/login', help='更新后及回滚后的 HTTP 200 健康检查地址')
    args = parser.parse_args()
    try:
        validate_health_url(args.health_url)
    except ValueError as exc:
        parser.error(str(exc))
    if sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        raise RuntimeError('Run on the target Linux server: sudo python3 deploy/update_release.py')
    import fcntl
    import pwd
    from consistent_snapshot import create_snapshot, restore_snapshot

    os.umask(0o077)
    lock = open('/run/rm-progress-update.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    manifest = check_release()
    if APP.is_symlink() or RELEASE == APP or RELEASE.is_relative_to(APP) or APP.is_relative_to(RELEASE):
        raise RuntimeError('Extract to a separate release directory, outside the running application')
    if setting('WorkingDirectory') != str(APP) or setting('ActiveState') != 'active':
        raise RuntimeError('Expected an active rm-progress service in /opt/rm-progress')
    account = pwd.getpwnam(setting('User'))
    if account.pw_uid == 0:
        raise RuntimeError('Application must run as a non-root user')
    runtime = dict(s.split('=', 1) for s in Path('/proc/' + setting('MainPID') + '/environ').read_bytes().decode().split('\0') if '=' in s)
    paths = {'PROGRESS_DB': 'data.db', 'FUNDS_DIR': 'data/funds', 'INVOICES_DIR': 'data/invoices',
             'PROGRESS_SECRET_PATH': 'secret.key', 'TASK_ATTACHMENTS_DIR': 'data/task_attachments'}
    for key, relative in paths.items():
        if Path(runtime.get(key, str(APP / relative))).resolve() != APP / relative:
            raise RuntimeError('Custom data path requires manual deployment: ' + key)
    # A template revision may update the standard generated unit, but not
    # administrator-customized systemd units or nonstandard unit locations.
    if ((APP / 'deploy/rm-progress.service').read_bytes() !=
            (RELEASE / 'deploy/rm-progress.service').read_bytes() and
            setting('FragmentPath') != str(WEB_UNIT)):
        raise RuntimeError('Nonstandard web systemd unit location; review manually before upgrading')
    unit_plan = plan_web_unit_update(account.pw_name)
    if (APP / 'DEPLOYED_COMMIT').exists() and (APP / 'DEPLOYED_COMMIT').read_text().strip() == manifest['commit']:
        print('Already deployed: ' + manifest['commit'])
        return
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = Path(account.pw_dir) / 'progress_backups' / ('upgrade-' + stamp)
    stage = APP.with_name('.rm-progress-next-' + stamp)
    previous = APP.with_name('rm-progress.previous-' + stamp)
    failed = APP.with_name('rm-progress.failed-' + stamp)
    backup.mkdir(parents=True)
    stage.mkdir()

    def own(path):
        for entry in [path] + list(path.rglob('*')):
            os.chown(entry, account.pw_uid, account.pw_gid, follow_symlinks=False)

    for name in manifest['files']:
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RELEASE / name, target)
    own(stage)
    # Unchanged dependencies are reused without downloading or mutating the old venv.
    # For an in-tree venv the symlink targets its location AFTER the directory switch.
    if (APP / 'requirements.txt').read_bytes() == (stage / 'requirements.txt').read_bytes():
        current_environment = (APP / 'venv').resolve()
        if current_environment.is_relative_to(APP):
            environment = previous / current_environment.relative_to(APP)
        else:
            environment = current_environment
        migration_python = APP / 'venv/bin/python'
        print('Dependencies unchanged: reusing the current virtual environment; no pip download.', flush=True)
    else:
        environment = APP.with_name('rm-progress-venv-' + stamp)
        print('Dependencies changed: preparing a separate environment before stopping service.', flush=True)
        run(['python3', '-m', 'venv', environment])
        install_dependencies(environment / 'bin/python', stage / 'requirements.txt', backup / 'dependencies.log')
        own(environment)
        migration_python = environment / 'bin/python'
    (stage / 'venv').symlink_to(environment, target_is_directory=True)
    # Compile before the maintenance window; this does not import the app.
    run([migration_python, '-m', 'compileall', '-q', stage / 'app.py', stage / 'common.py', stage / 'modules'], user=account.pw_uid, group=account.pw_gid)
    stopped = moved = installed = unit_updated = False
    mail_paused = False
    timer_was_active = False
    try:
        timer_was_active = unit_active('rm-mail.timer')
        mail_paused = True
        suspend_mail()
        print('Stopping service and creating offline recovery point...', flush=True)
        stopped = True
        run(['systemctl', 'stop', SERVICE])
        if setting('MainPID') != '0':
            raise RuntimeError('Application process is still running')
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                command = (proc / 'cmdline').read_bytes()
                if (proc / 'cwd').resolve() == APP and (b'python' in command or b'gunicorn' in command):
                    raise RuntimeError('Another application writer is running: ' + proc.name)
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        (backup / 'service.txt').write_text(output(['systemctl', 'cat', SERVICE]))
        if unit_plan:
            (backup / 'rm-progress.service').write_bytes(unit_plan[0])
        redacted_runtime = {key: ('[REDACTED]' if any(token in key.upper() for token in
                           ('PASSWORD', 'SECRET', 'TOKEN', 'CREDENTIAL', 'SMTP_')) else value)
                            for key, value in runtime.items()}
        (backup / 'runtime-environment.json').write_text(json.dumps(redacted_runtime, indent=2))
        shutil.copytree(APP, backup / 'old-code', ignore=shutil.ignore_patterns('venv', 'data', 'data.db*', 'secret.key', 'backups', '__pycache__', '.git'), symlinks=True)
        create_snapshot(APP, backup / 'full-data.zip', offline_confirmed=True)
        restore_snapshot(backup / 'full-data.zip', backup / 'restored')
        shutil.copytree(backup / 'restored', stage, dirs_exist_ok=True)
        for item in APP.iterdir():
            if item.is_file() and (item.name.startswith('.env') or item.name in ('index.html', 'initial-admin-password.txt', 'mail.env')):
                if item.is_symlink():
                    raise RuntimeError('Custom configuration symlink requires manual deployment: ' + str(item))
                shutil.copy2(item, stage / item.name)
        # Preserve other runtime data under data/, beyond the snapshot's known folders.
        for item in (APP / 'data').iterdir():
            target = stage / 'data' / item.name
            if not target.exists():
                if item.is_symlink():
                    raise RuntimeError('Custom data symlink requires manual deployment: ' + str(item))
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)
        print('Backup verified: ' + str(backup), flush=True)
        own(stage)
        migration_env = dict(runtime)
        migration_env.update({key: str(stage / relative) for key, relative in paths.items()})
        migration_env['PYTHONPATH'] = str(stage)
        print('Migrating the isolated data copy...', flush=True)
        run([migration_python, '-c', 'import app'], cwd=stage, env=migration_env, user=account.pw_uid, group=account.pw_gid)
        counts = {}
        for relative, tables in [('data.db', ('members', 'tasks', 'logs')), ('data/funds/funds.db', ('records',))]:
            with closing(sqlite3.connect(stage / relative)) as new, closing(sqlite3.connect(backup / 'restored' / relative)) as old:
                if new.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise RuntimeError('Database integrity check failed: ' + relative)
                for table in tables:
                    old_ids = old.execute('SELECT id FROM ' + table + ' ORDER BY id').fetchall()
                    if old_ids != new.execute('SELECT id FROM ' + table + ' ORDER BY id').fetchall():
                        raise RuntimeError('Existing record IDs changed: ' + table)
                    counts[table] = len(old_ids)
        if sha(stage / 'secret.key') != sha(APP / 'secret.key'):
            raise RuntimeError('Signing key unexpectedly changed')
        for original in (backup / 'restored' / 'data').rglob('*'):
            if original.is_file() and (original.suffix.lower() == '.pdf' or 'task_attachments' in original.parts):
                if sha(original) != sha(stage / original.relative_to(backup / 'restored')):
                    raise RuntimeError('Attachment changed during migration: ' + str(original))
        for name, expected in manifest['files'].items():
            if sha(stage / name) != expected:
                raise RuntimeError('Staged code changed: ' + name)
        (stage / 'DEPLOYED_COMMIT').write_text(manifest['commit'] + '\n')
        own(stage)
        if (APP / 'backups').exists():
            (stage / 'backups').symlink_to(previous / 'backups', target_is_directory=True)
        (backup / 'recovery.json').write_text(json.dumps({'previous': str(previous), 'app': str(APP), 'commit': manifest['commit'], 'counts': counts}, indent=2))
        print('Migration verified; switching installation and starting service...', flush=True)
        with protect_recovery():
            APP.rename(previous)
            moved = True
            stage.rename(APP)
            installed = True
        if unit_plan:
            replace_web_unit(WEB_UNIT, unit_plan[1])
            unit_updated = True
            print('Updated reviewed web systemd unit with the new release template.', flush=True)
        install_mail(APP, account.pw_name)
        run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'start', SERVICE])
        healthy(args.health_url)
        (backup / 'result.json').write_text(json.dumps({'status': 'success', 'commit': manifest['commit'], 'counts': counts, 'login_http': 200}, indent=2))
        own(backup)
        resume_mail(timer_was_active)
        mail_paused = False
        print('DEPLOYMENT_OK ' + manifest['commit'])
        print('BACKUP ' + str(backup))
        print('PREVIOUS_INSTALLATION ' + str(previous))
        print('LOGIN_HTTP 200; records: ' + json.dumps(counts))
    except BaseException:
        with protect_recovery():
            try:
                if stopped:
                    print('Restoring old service; please wait. Further Ctrl+C is ignored during recovery.', flush=True)
                    run(['systemctl', 'stop', SERVICE])
                    if installed:
                        APP.rename(failed)
                    if moved:
                        previous.rename(APP)
                    if unit_updated:
                        replace_web_unit(WEB_UNIT, unit_plan[0])
                        run(['systemctl', 'daemon-reload'])
                    run(['systemctl', 'start', SERVICE])
                    healthy(args.health_url)
                    print('Old service restored; failed release and recovery files retained.', flush=True)
            finally:
                if mail_paused:
                    resume_mail(timer_was_active)
        raise


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('Update cancelled. No further deployment steps will run.')
    except Exception as exc:
        raise SystemExit('Update failed: ' + str(exc))
