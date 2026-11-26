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
        if not conn.execute('SELECT 1 FROM funds_revision_migrations WHERE version=2').fetchone():
            conn.execute('ALTER TABLE records ADD COLUMN self_funded INTEGER NOT NULL DEFAULT 0 CHECK(self_funded IN (0,1))')
            conn.execute('ALTER TABLE records ADD COLUMN applicant_id INTEGER')
            conn.execute('ALTER TABLE records ADD COLUMN personal_invoice_key TEXT')
            conn.execute('ALTER TABLE records ADD COLUMN personal_paid_at TEXT')
            conn.execute('CREATE UNIQUE INDEX funds_personal_invoice_key ON records(personal_invoice_key) '
                         'WHERE personal_invoice_key IS NOT NULL')
            conn.execute('INSERT INTO funds_revision_migrations VALUES(2)')
        if not conn.execute('SELECT 1 FROM funds_revision_migrations WHERE version=3').fetchone():
            conn.execute("""CREATE TABLE funds_personal_invoices (
                invoice_key TEXT PRIMARY KEY NOT NULL,
                record_id INTEGER NOT NULL REFERENCES records(id),
                amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                label TEXT NOT NULL DEFAULT ''
            )""")
            conn.execute('CREATE INDEX funds_personal_invoices_record ON funds_personal_invoices(record_id)')
            conn.execute("""INSERT INTO funds_personal_invoices (invoice_key,record_id,amount_cents)
                SELECT personal_invoice_key,id,amount_cents FROM records
                WHERE personal_invoice_key IS NOT NULL AND personal_paid_at IS NOT NULL""")
            conn.execute("""CREATE TABLE funds_personal_events (
                id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL,
                operation TEXT NOT NULL, actor_id INTEGER NOT NULL, actor_name TEXT NOT NULL,
                occurred_at TEXT NOT NULL, reason TEXT NOT NULL,
                before_json TEXT NOT NULL, after_json TEXT NOT NULL
            )""")
            conn.execute('CREATE INDEX funds_personal_events_record ON funds_personal_events(record_id,id DESC)')
            conn.execute('INSERT INTO funds_revision_migrations VALUES(3)')
        if not conn.execute('SELECT 1 FROM funds_revision_migrations WHERE version=4').fetchone():
            conn.execute('ALTER TABLE records ADD COLUMN invoice_selection INTEGER NOT NULL DEFAULT 0')
            conn.execute('INSERT INTO funds_revision_migrations VALUES(4)')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
