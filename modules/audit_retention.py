"""Stable action classification; maintenance is explicit and backed up, never startup deletion."""
import os
from datetime import datetime, timedelta

ROUTINE_ACTIONS = ('member_profiles.account_preferences',)


def migrate_classification(db):
    if 'level' not in {r[1] for r in db.execute('PRAGMA table_info(security_audit)')}:
        db.execute("ALTER TABLE security_audit ADD COLUMN level TEXT NOT NULL DEFAULT 'critical'")
        db.execute("UPDATE security_audit SET level='routine' WHERE action IN (?) AND outcome='success'", ROUTINE_ACTIONS)


def cleanup_plan(db):
    """Return exact IDs under the caller's transaction; unknown actions stay critical."""
    days = {'critical': max(0, int(os.environ.get('AUDIT_RETENTION_DAYS', '90'))),
            'routine': max(0, int(os.environ.get('AUDIT_ROUTINE_RETENTION_DAYS', '7')))}
    protected = {r[0] for r in db.execute("""WITH RECURSIVE protected(id) AS (
        SELECT id FROM security_audit WHERE outcome='pending'
        UNION SELECT audit_id FROM invoice_jobs
        UNION SELECT CAST(json_extract(a.details,'$.request_audit_id') AS INTEGER)
        FROM security_audit a JOIN protected p ON p.id=a.id
        WHERE json_valid(a.details) AND json_type(a.details,'$.request_audit_id')='integer'
    ) SELECT id FROM protected""")}
    rows = db.execute('SELECT id,level,created_at FROM security_audit ORDER BY id').fetchall()
    cutoffs = {level: (datetime.now() - timedelta(days=n)).strftime('%Y-%m-%d %H:%M:%S')
               for level, n in days.items() if n}
    ids = {r[0] for r in rows if r[0] not in protected and r[1] in cutoffs and r[2] < cutoffs[r[1]]}
    limit = max(0, int(os.environ.get('AUDIT_MAX_ROWS', '100000')))
    if limit:
        excess = max(0, len(rows) - len(ids) - limit)
        # Capacity never evicts unexpired critical events.
        ids.update(r[0] for r in [r for r in rows if r[0] not in protected | ids and r[1] == 'routine'][:excess])
    return {'counts': {level: sum(r[1] == level for r in rows) for level in days},
            'delete_counts': {level: sum(r[1] == level and r[0] in ids for r in rows) for level in days},
            'ids': sorted(ids), 'protected': len(protected), 'retention_days': days}


def cleanup_audit_rows(db, *, confirmed=False):
    plan = cleanup_plan(db)
    if confirmed:
        db.executemany('DELETE FROM security_audit WHERE id=?', ((i,) for i in plan['ids']))
    return plan
