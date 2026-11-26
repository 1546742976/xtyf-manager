"""Stable audit classification and explicit, backup-first retention maintenance."""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta

ROUTINE_ACTIONS = ('member_profiles.account_preferences',)
# 0 disables time-based expiry for the corresponding level. It also exempts
# routine records from optional capacity trimming.
RETENTION_DEFAULTS = {'critical': 365, 'important': 180, 'normal': 90, 'routine': 7}
RETENTION_ENV = {
    'critical': 'AUDIT_RETENTION_DAYS',
    'important': 'AUDIT_IMPORTANT_RETENTION_DAYS',
    'normal': 'AUDIT_NORMAL_RETENTION_DAYS',
    'routine': 'AUDIT_ROUTINE_RETENTION_DAYS',
}
MAX_RETENTION_DAYS = 36500
LEVELS = ('permanent', 'critical', 'important', 'normal', 'routine')
CATEGORIES = ('finance', 'security', 'assets', 'members', 'tasks', 'collaboration', 'settings', 'other')


def classify_action(action, outcome='pending'):
    """Return (business category, retention priority). Unknown actions fail closed."""
    action = str(action or '')
    outcome = str(outcome or 'pending')
    # Financial records (including rejected operations, exports and async jobs)
    # must never become subject to age- or capacity-based deletion.
    if action.startswith(('funds.', 'invoices.', 'invoice_jobs.', 'funds_')):
        return 'finance', 'permanent'
    if (action in {'login', 'logout', 'api_password', 'export_members_csv',
                   'api_member_reset', 'api_member_role', 'api_member_toggle'}
            or action.startswith(('security.', 'client_api.login', 'client_api.logout'))):
        return 'security', 'critical'
    if action in ROUTINE_ACTIONS or action == 'email.save_own_preferences':
        category, level = 'settings', 'routine'
    elif action.startswith('asset_labels.'):
        category, level = 'assets', 'routine'
    elif action.startswith(('assets.', 'asset_', 'asset_bom.', 'asset_scan.', 'asset_units.')):
        category, level = 'assets', 'important'
    elif (action.startswith(('member_profiles.', 'member_avatars.', 'member_introductions.',
                              'members.', 'member.', 'api_member',
                              'task_catalogs.member_', 'task_catalogs.members_'))):
        category, level = 'members', 'important'
    elif (action.startswith(('collaboration_tasks.', 'task_progress.', 'task_attachments.',
                              'task_alerts.', 'task_', 'tasks.', 'task.',
                              'task_catalogs.', 'projects.', 'daily_projects.',
                              'task_schedule.'))):
        category, level = 'tasks', 'normal'
    elif action.startswith(('discussions.', 'item_topics.', 'email.', 'collaboration.',
                             'github_', 'mail_', 'notifications.')):
        category, level = 'collaboration', 'normal'
    elif action.startswith(('workspace_settings.', 'site_updates.', 'client_updates.', 'pwa.')):
        category, level = 'settings', 'routine'
    else:
        category, level = 'other', 'critical'
    if outcome in ('failure', 'partial'):
        level = 'critical'
    elif any(part in action.lower() for part in ('delete', 'restore', 'rollback', 'revoke', 'export')):
        if level in ('normal', 'routine'):
            level = 'important'
    return category, level


def validate_retention_days(values, *, exact=False):
    """Strictly validate non-permanent levels; reject floats, bools, unknowns."""
    if not isinstance(values, dict):
        raise ValueError('审计保留期配置必须是对象')
    if not set(values).issubset(RETENTION_DEFAULTS) or (exact and set(values) != set(RETENTION_DEFAULTS)):
        raise ValueError('审计保留期只允许 critical、important、normal、routine 四个级别')
    validated = {}
    for level, value in values.items():
        if type(value) is not int or not 0 <= value <= MAX_RETENTION_DAYS:
            raise ValueError(f'{level} 保留天数必须为 0～{MAX_RETENTION_DAYS} 的整数')
        validated[level] = value
    return validated


def migrate_retention_settings(db):
    """Admin overrides are stored with the audited business database."""
    db.execute("""CREATE TABLE IF NOT EXISTS audit_retention_settings (
        level TEXT PRIMARY KEY CHECK(level IN ('critical','important','normal','routine')),
        days INTEGER NOT NULL CHECK(days BETWEEN 0 AND 36500)
    )""")


def retention_policy(db):
    """Resolve admin > config file > legacy env > defaults, without side effects."""
    days = dict(RETENTION_DEFAULTS)
    sources = dict.fromkeys(RETENTION_DEFAULTS, 'default')
    for level, name in RETENTION_ENV.items():
        value = os.environ.get(name)
        if value is not None:
            days[level] = validate_retention_days({level: int(value)})[level]
            sources[level] = 'environment'
    path = os.environ.get('AUDIT_RETENTION_CONFIG')
    if path:
        config_path = Path(path).expanduser()
        if config_path.stat().st_size > 16384:
            raise ValueError('审计保留期配置文件超过 16 KiB')
        content = json.loads(config_path.read_text(encoding='utf-8'))
        overrides = validate_retention_days(content)
        for level, value in overrides.items():
            days[level], sources[level] = value, 'file'
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit_retention_settings'").fetchone():
        for level, value in db.execute('SELECT level,days FROM audit_retention_settings'):
            days[level] = validate_retention_days({level: value})[level]
            sources[level] = 'admin'
    return {'days': {'permanent': None, **days}, 'sources': {'permanent': 'fixed', **sources}}


def save_retention_days(db, values=None, *, reset=False):
    """Write all admin overrides, or remove them to expose file/env values."""
    if reset:
        if values is not None:
            raise ValueError('恢复继承配置时不可同时传入保留期')
    else:
        values = validate_retention_days(values, exact=True)
    migrate_retention_settings(db)
    db.execute('DELETE FROM audit_retention_settings')
    if not reset:
        db.executemany('INSERT INTO audit_retention_settings(level,days) VALUES(?,?)', values.items())


def migrate_classification(db):
    """Reclassify existing rows once when category is introduced; never delete here."""
    columns = {r[1] for r in db.execute('PRAGMA table_info(security_audit)')}
    needs_backfill = 'category' not in columns or 'level' not in columns
    if 'level' not in columns:
        db.execute("ALTER TABLE security_audit ADD COLUMN level TEXT NOT NULL DEFAULT 'critical'")
    if 'category' not in columns:
        db.execute("ALTER TABLE security_audit ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")
    if needs_backfill:
        rows = db.execute('SELECT id,action,outcome FROM security_audit').fetchall()
        db.executemany(
            'UPDATE security_audit SET category=?,level=? WHERE id=?',
            ((*classify_action(action, outcome), identifier) for identifier, action, outcome in rows),
        )


def cleanup_plan(db):
    """Preview expired rows. Finance is protected even if a legacy level is wrong."""
    policy = retention_policy(db)
    days = {level: policy['days'][level] for level in RETENTION_DEFAULTS}
    protected = {r[0] for r in db.execute("""WITH RECURSIVE protected(id) AS (
        SELECT id FROM security_audit WHERE outcome='pending'
        UNION SELECT audit_id FROM invoice_jobs
        UNION SELECT CAST(json_extract(a.details,'$.request_audit_id') AS INTEGER)
        FROM security_audit a JOIN protected p ON p.id=a.id
        WHERE json_valid(a.details) AND json_type(a.details,'$.request_audit_id')='integer'
    ) SELECT id FROM protected""")}
    columns = {r[1] for r in db.execute('PRAGMA table_info(security_audit)')}
    has_action, has_category = 'action' in columns, 'category' in columns
    rows = db.execute(
        'SELECT id,level,created_at' + (',action' if has_action else '') +
        (',category' if has_category else '') + ' FROM security_audit ORDER BY id'
    ).fetchall()
    # Never delete financial events, even if a legacy writer/reset assigned
    # the wrong level. Both the stable endpoint and category act as safeguards.
    finance_ids = {r[0] for r in rows if r[1] == 'permanent' or
                   (has_action and classify_action(r[3])[0] == 'finance') or
                   (has_category and r[4 if has_action else 3] == 'finance')}
    protected |= finance_ids
    cutoffs = {level: (datetime.now() - timedelta(days=n)).strftime('%Y-%m-%d %H:%M:%S')
               for level, n in days.items() if n}
    ids = {r[0] for r in rows if r[0] not in protected and
           r[1] in cutoffs and r[2] < cutoffs[r[1]]}
    limit = max(0, int(os.environ.get('AUDIT_MAX_ROWS', '100000')))
    if limit and days['routine']:
        excess = max(0, len(rows) - len(ids) - limit)
        # Capacity pressure only discards routine events; never important evidence.
        for r in rows:
            if excess <= 0:
                break
            if r[0] not in protected | ids and r[1] == 'routine':
                ids.add(r[0])
                excess -= 1
    return {
        'counts': {level: sum(r[1] == level for r in rows) for level in LEVELS},
        'delete_counts': {level: sum(r[1] == level and r[0] in ids for r in rows)
                          for level in LEVELS},
        'ids': sorted(ids), 'protected': len(protected),
        'retention_days': policy['days'], 'retention_sources': policy['sources'],
    }


def cleanup_audit_rows(db, *, confirmed=False):
    plan = cleanup_plan(db)
    if confirmed:
        db.executemany('DELETE FROM security_audit WHERE id=?', ((i,) for i in plan['ids']))
    return plan
