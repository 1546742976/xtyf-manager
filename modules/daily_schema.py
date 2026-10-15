"""Repeat-safe, additive daily-project and mail schema."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_projects (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT '未开始', priority TEXT NOT NULL DEFAULT '中',
 start_date TEXT, due_date TEXT, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
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
CREATE TABLE IF NOT EXISTS daily_attachments (
 id INTEGER PRIMARY KEY, update_id INTEGER NOT NULL REFERENCES daily_updates(id),
 original_name TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL, mime_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS member_email (
 member_id INTEGER PRIMARY KEY REFERENCES members(id), address TEXT NOT NULL,
 verified INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0,
 generation TEXT NOT NULL, token_hash TEXT, expires_at REAL, requested_at REAL NOT NULL
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
        db.commit()
    except Exception:
        db.rollback()
        raise
