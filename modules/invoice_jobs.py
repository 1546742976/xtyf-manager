"""Durable invoice queue in data.db; claim/recovery require the invoice file lock."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from common import DB_PATH
from modules.security import audit_background_finish, audit_background_start, permission_is_current

JOB_TYPES = {'scan_dups', 'organize', 'refresh', 'delete_dups'}
TERMINAL = {'done', 'error', 'interrupted'}


@contextmanager
def _db():
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def migrate(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS invoice_jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL CHECK(type IN ('scan_dups','organize','refresh','delete_dups')),
            actor_id INTEGER NOT NULL,
            actor_name TEXT NOT NULL,
            session_version INTEGER NOT NULL,
            audit_id INTEGER NOT NULL REFERENCES security_audit(id),
            status TEXT NOT NULL CHECK(status IN ('queued','running','done','error','interrupted')),
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL,
            result TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_invoice_jobs_queue ON invoice_jobs(status,created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_jobs_active_type
            ON invoice_jobs(type) WHERE status IN ('queued','running');
    """)


def _serialize(row, include_result=True):
    if row is None:
        return None
    job = dict(row)
    job.pop('audit_id', None)
    job.pop('session_version', None)
    if include_result:
        job['result'] = json.loads(job['result']) if job['result'] else None
    else:
        job.pop('result', None)
    return job


def enqueue(job_type, user):
    if job_type not in JOB_TYPES:
        raise ValueError('未知任务类型')
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute("SELECT * FROM invoice_jobs WHERE type=? AND status IN ('queued','running')",
                              (job_type,)).fetchone()
        if existing:
            return _serialize(existing), False
        jid = uuid.uuid4().hex
        audit_id = audit_background_start(job_type, jid, db=db)
        db.execute(
            "INSERT INTO invoice_jobs(id,type,actor_id,actor_name,session_version,audit_id,status,message,created_at) "
            "VALUES(?,?,?,?,?,?,'queued','等待执行',?)",
            (jid, job_type, user['id'], user['name'], user['session_version'], audit_id, time.time()),
        )
        return _serialize(db.execute('SELECT * FROM invoice_jobs WHERE id=?', (jid,)).fetchone()), True


def get_job(jid):
    with _db() as db:
        return _serialize(db.execute('SELECT * FROM invoice_jobs WHERE id=?', (jid,)).fetchone())


def actor_is_current(user_id, session_version):
    """Validate a stream viewer without retaining a Flask or SQLite context."""
    with _db() as db:
        return permission_is_current(db, user_id, 'invoices.manage', session_version)


def list_jobs():
    with _db() as db:
        rows = db.execute(
            "SELECT id,type,actor_id,actor_name,status,progress,message,attempts,created_at,started_at,finished_at "
            "FROM invoice_jobs ORDER BY (status IN ('queued','running')) DESC,created_at DESC LIMIT 30"
        ).fetchall()
        return [dict(row) for row in rows]


def update_job(jid, progress, message):
    with _db() as db:
        db.execute("UPDATE invoice_jobs SET progress=?,message=? WHERE id=? AND status='running'",
                   (max(0, min(int(progress), 99)), message, jid))


def _finish(db, row, status, message, result, outcome):
    db.execute(
        "UPDATE invoice_jobs SET status=?,progress=?,message=?,result=?,finished_at=? WHERE id=?",
        (status, 100 if status == 'done' else row['progress'], message,
         json.dumps(result, ensure_ascii=False), time.time(), row['id']),
    )
    # The task outcome and its audit outcome commit in one database transaction.
    audit_background_finish(row['audit_id'], outcome, result, db=db)


def finish_job(jid, result=None, error=None):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM invoice_jobs WHERE id=? AND status='running'", (jid,)).fetchone()
        if row is None:
            return
        if error is not None:
            _finish(db, row, 'error', str(error), {'error': str(error)}, 'failure')
        else:
            partial = isinstance(result, dict) and (result.get('failed') or result.get('errors'))
            _finish(db, row, 'done', '完成（部分项目失败，请查看结果）' if partial else '完成',
                    result, 'partial' if partial else 'success')


def recover_and_claim():
    """Only call while holding the file lock continuously until finish_job.

    An active executor holds that same lock. Therefore any persisted 'running'
    row seen here belongs to an executor which has exited, without lease timers
    that might accidentally reclaim a slow but healthy PDF operation.
    """
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT * FROM invoice_jobs WHERE status='running'").fetchall():
            if row['type'] in ('scan_dups', 'refresh') and row['attempts'] < 3:
                db.execute("UPDATE invoice_jobs SET status='queued',progress=0,message='服务中断，等待重新执行' WHERE id=?",
                           (row['id'],))
            else:
                message = '执行中断；部分操作可能已完成，请核对发票后重新发起'
                _finish(db, row, 'interrupted', message, {'error': message}, 'failure')
        while True:
            row = db.execute("SELECT * FROM invoice_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row is None:
                return None
            allowed = permission_is_current(db, row['actor_id'], 'invoices.manage', row['session_version'])
            if not allowed:
                _finish(db, row, 'error', '发起人的账号或财务授权已变更，请重新登录后发起',
                        {'error': '发起人的账号或财务授权已变更'}, 'failure')
                continue
            db.execute("UPDATE invoice_jobs SET status='running',progress=0,message='开始执行',"
                       "attempts=attempts+1,started_at=? WHERE id=?", (time.time(), row['id']))
            return dict(row)
