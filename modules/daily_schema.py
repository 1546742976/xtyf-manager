"""Repeat-safe, additive daily-project and mail schema."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_projects (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT '未开始', priority TEXT NOT NULL DEFAULT '中',
 start_date TEXT, due_date TEXT, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS daily_groups (
 project_id INTEGER NOT NULL REFERENCES daily_projects(id), stage TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1, progress INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
 version INTEGER NOT NULL DEFAULT 1, enabled_at TEXT NOT NULL,
 PRIMARY KEY(project_id,stage)
);
CREATE TABLE IF NOT EXISTS daily_owners (
 project_id INTEGER NOT NULL, stage TEXT NOT NULL, member_id INTEGER NOT NULL REFERENCES members(id),
 PRIMARY KEY(project_id,stage,member_id),
 FOREIGN KEY(project_id,stage) REFERENCES daily_groups(project_id,stage)
);
CREATE TABLE IF NOT EXISTS daily_updates (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, stage TEXT NOT NULL,
 actor_id INTEGER NOT NULL REFERENCES members(id), actor_name TEXT NOT NULL,
 progress INTEGER NOT NULL CHECK(progress BETWEEN 0 AND 100), note TEXT NOT NULL,
 created_at TEXT NOT NULL, FOREIGN KEY(project_id,stage) REFERENCES daily_groups(project_id,stage)
);
CREATE INDEX IF NOT EXISTS daily_updates_group ON daily_updates(project_id,stage,id);
CREATE TABLE IF NOT EXISTS daily_changes (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES daily_projects(id),
 actor_id INTEGER NOT NULL REFERENCES members(id), actor_name TEXT NOT NULL,
 action TEXT NOT NULL, note TEXT NOT NULL, before_json TEXT NOT NULL,
 after_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_attachments (
 id INTEGER PRIMARY KEY, update_id INTEGER NOT NULL REFERENCES daily_updates(id),
 original_name TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL, mime_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_blocker_events (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES daily_projects(id),
 actor_id INTEGER NOT NULL REFERENCES members(id), actor_name TEXT NOT NULL,
 action TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', next_action TEXT NOT NULL DEFAULT '',
 helper_id INTEGER REFERENCES members(id), follow_up TEXT,
 resolution TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS daily_blocker_events_project ON daily_blocker_events(project_id,id);
CREATE TABLE IF NOT EXISTS member_email (
 member_id INTEGER PRIMARY KEY REFERENCES members(id), address TEXT NOT NULL,
 verified INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0,
 generation TEXT NOT NULL, token_hash TEXT, expires_at REAL, requested_at REAL NOT NULL,
  verify_attempts INTEGER NOT NULL DEFAULT 0,
 morning_interval_hours INTEGER NOT NULL DEFAULT 72 CHECK(morning_interval_hours BETWEEN 12 AND 168 AND morning_interval_hours % 12 = 0),
 daily_interval_days INTEGER NOT NULL DEFAULT 7 CHECK(daily_interval_days BETWEEN 1 AND 14),
 countdown_interval_days INTEGER NOT NULL DEFAULT 7 CHECK(countdown_interval_days BETWEEN 1 AND 14),
 morning_enabled INTEGER NOT NULL DEFAULT 1 CHECK(morning_enabled IN (0,1)),
 daily_enabled INTEGER NOT NULL DEFAULT 1 CHECK(daily_enabled IN (0,1)),
 countdown_enabled INTEGER NOT NULL DEFAULT 0 CHECK(countdown_enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS mail_jobs (
 id INTEGER PRIMARY KEY, member_id INTEGER NOT NULL REFERENCES members(id), kind TEXT NOT NULL,
 day TEXT NOT NULL, dedupe_key TEXT UNIQUE NOT NULL, generation TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_at REAL NOT NULL, created_at REAL NOT NULL, claimed_at REAL,
 finished_at REAL, error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS mail_jobs_due ON mail_jobs(state,next_at);
"""


def migrate(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        for statement in SCHEMA.split(';'):
            if statement.strip():
                db.execute(statement)
        columns = {row[1] for row in db.execute('PRAGMA table_info(daily_projects)')}
        if 'created_by' not in columns:
            db.execute('ALTER TABLE daily_projects ADD COLUMN created_by INTEGER REFERENCES members(id)')
        if 'completed_at' not in columns:
            # Historical updated_at is not evidence of completion.
            db.execute('ALTER TABLE daily_projects ADD COLUMN completed_at TEXT')
        for name, definition in (
            ('blocker_reason', "TEXT NOT NULL DEFAULT ''"),
            ('blocker_next_action', "TEXT NOT NULL DEFAULT ''"),
            ('blocker_helper_id', 'INTEGER REFERENCES members(id)'),
            ('blocker_follow_up', 'TEXT'),
        ):
            if name not in columns:
                db.execute(f'ALTER TABLE daily_projects ADD COLUMN {name} {definition}')
        group_columns = {row[1] for row in db.execute('PRAGMA table_info(daily_groups)')}
        if 'group_id' not in group_columns:
            db.execute('ALTER TABLE daily_groups ADD COLUMN group_id INTEGER REFERENCES groups(id)')
        if 'label_snapshot' not in group_columns:
            db.execute("ALTER TABLE daily_groups ADD COLUMN label_snapshot TEXT NOT NULL DEFAULT ''")
            # Historical stages have no trustworthy link to a possibly renamed catalog.
            for stage, label in (('mechanical', '机械'), ('electrical', '电控'), ('vision', '视觉')):
                db.execute('UPDATE daily_groups SET label_snapshot=? WHERE stage=?', (label, stage))
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS daily_groups_catalog ON daily_groups(project_id,group_id) WHERE group_id IS NOT NULL')
        email_columns = {row[1] for row in db.execute('PRAGMA table_info(member_email)')}
        if 'assignment_enabled' not in email_columns:
            db.execute('ALTER TABLE member_email ADD COLUMN assignment_enabled INTEGER NOT NULL DEFAULT 1 CHECK(assignment_enabled IN (0,1))')
        job_columns = {row[1] for row in db.execute('PRAGMA table_info(mail_jobs)')}
        if 'payload_json' not in job_columns:
            db.execute('ALTER TABLE mail_jobs ADD COLUMN payload_json TEXT')
        if 'verify_attempts' not in email_columns:
            db.execute('ALTER TABLE member_email ADD COLUMN verify_attempts INTEGER NOT NULL DEFAULT 0')
            # Invalidate existing link-based challenges when switching to numeric codes.
            db.execute('UPDATE member_email SET token_hash=NULL,expires_at=0 WHERE verified=0 AND token_hash IS NOT NULL')
        for name, definition in (
            ('morning_interval_hours', 'INTEGER NOT NULL DEFAULT 72 CHECK(morning_interval_hours BETWEEN 12 AND 168 AND morning_interval_hours % 12 = 0)'),
            ('daily_interval_days', 'INTEGER NOT NULL DEFAULT 7 CHECK(daily_interval_days BETWEEN 1 AND 14)'),
            ('countdown_interval_days', 'INTEGER NOT NULL DEFAULT 7 CHECK(countdown_interval_days BETWEEN 1 AND 14)'),
            ('morning_enabled', 'INTEGER NOT NULL DEFAULT 1 CHECK(morning_enabled IN (0,1))'),
            ('daily_enabled', 'INTEGER NOT NULL DEFAULT 1 CHECK(daily_enabled IN (0,1))'),
            ('countdown_enabled', 'INTEGER NOT NULL DEFAULT 0 CHECK(countdown_enabled IN (0,1))'),
        ):
            if name not in email_columns:
                db.execute(f'ALTER TABLE member_email ADD COLUMN {name} {definition}')
        db.commit()
    except Exception:
        db.rollback()
        raise
