"""Additive local collaboration tables; existing task and member data stays in place."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS collaboration_topics (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, author_id INTEGER NOT NULL REFERENCES members(id),
 author_name TEXT NOT NULL, created_at TEXT NOT NULL, created_epoch REAL NOT NULL,
 last_activity_at TEXT NOT NULL, withdrawn_at TEXT, withdrawn_by INTEGER REFERENCES members(id)
);
CREATE TABLE IF NOT EXISTS collaboration_messages (
 id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES collaboration_topics(id),
 author_id INTEGER NOT NULL REFERENCES members(id), author_name TEXT NOT NULL,
 body TEXT NOT NULL, created_at TEXT NOT NULL, created_epoch REAL NOT NULL,
 withdrawn_at TEXT, withdrawn_by INTEGER REFERENCES members(id)
);
CREATE INDEX IF NOT EXISTS collaboration_messages_author ON collaboration_messages(author_id);
CREATE INDEX IF NOT EXISTS collaboration_messages_topic ON collaboration_messages(topic_id,id);
CREATE TABLE IF NOT EXISTS collaboration_attachments (
 id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL REFERENCES collaboration_messages(id),
 original_name TEXT NOT NULL, stored_name TEXT UNIQUE NOT NULL, size_bytes INTEGER NOT NULL,
 sha256 TEXT NOT NULL, thumbnail_name TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS collaboration_attachments_message ON collaboration_attachments(message_id,id);
CREATE TABLE IF NOT EXISTS collaboration_submissions (
 member_id INTEGER NOT NULL REFERENCES members(id), submission_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, topic_id INTEGER NOT NULL REFERENCES collaboration_topics(id),
 message_id INTEGER NOT NULL REFERENCES collaboration_messages(id),
 PRIMARY KEY(member_id,submission_id)
);
CREATE TABLE IF NOT EXISTS collaboration_reads (
 member_id INTEGER NOT NULL REFERENCES members(id), topic_id INTEGER NOT NULL REFERENCES collaboration_topics(id),
 message_id INTEGER NOT NULL, PRIMARY KEY(member_id,topic_id)
);
CREATE TABLE IF NOT EXISTS collaboration_changes (
 id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES collaboration_topics(id),
 kind TEXT NOT NULL, message_id INTEGER REFERENCES collaboration_messages(id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS collaboration_changes_topic ON collaboration_changes(topic_id,id);
CREATE INDEX IF NOT EXISTS collaboration_topics_activity ON collaboration_topics(last_activity_at,id);
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
