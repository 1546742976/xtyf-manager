"""Durable projection revisions; business records remain authoritative."""


def migrate(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS funds_ledger_state (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0,
        synced_revision INTEGER NOT NULL DEFAULT -1, state TEXT NOT NULL DEFAULT 'pending',
        last_attempt TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '')''')
    conn.execute('INSERT OR IGNORE INTO funds_ledger_state(id) VALUES(1)')
    for operation in ('INSERT', 'UPDATE', 'DELETE'):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS funds_ledger_{operation.lower()}
            AFTER {operation} ON records BEGIN
            UPDATE funds_ledger_state SET revision=revision+1,state='pending' WHERE id=1;
            END''')


def status(conn):
    return dict(conn.execute('SELECT * FROM funds_ledger_state WHERE id=1').fetchone())


def failed(conn, error):
    conn.execute("UPDATE funds_ledger_state SET state='failed',last_attempt=datetime('now'),last_error=? WHERE id=1 AND revision<>synced_revision", (type(error).__name__,))
    conn.commit()


def synced(conn, revision):
    conn.execute("UPDATE funds_ledger_state SET synced_revision=?,state=CASE WHEN revision=? THEN 'ok' ELSE 'pending' END,last_attempt=datetime('now'),last_error='' WHERE id=1", (revision, revision))
    conn.commit()
