"""Add review versions independently of the historical money-table conversion."""


def migrate(conn):
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS funds_revision_migrations (version INTEGER PRIMARY KEY)')
        if not conn.execute('SELECT 1 FROM funds_revision_migrations WHERE version=1').fetchone():
            columns = {row[1] for row in conn.execute('PRAGMA table_info(records)')}
            if 'version' not in columns:
                conn.execute('ALTER TABLE records ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
            conn.execute('INSERT INTO funds_revision_migrations VALUES(1)')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
