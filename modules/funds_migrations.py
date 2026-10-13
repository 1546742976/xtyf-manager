"""One transactional conversion of legacy REAL amounts; preserve IDs and sources."""
from modules.money import MAX_CENTS, to_cents

RECORD_SCHEMA = f"""CREATE TABLE records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_type TEXT NOT NULL CHECK(record_type IN ('进','出')),
    amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents BETWEEN 1 AND {MAX_CENTS}),
    amount TEXT GENERATED ALWAYS AS (printf('%d.%02d',amount_cents / 100,amount_cents % 100)) VIRTUAL,
    category TEXT DEFAULT '', source_dest TEXT DEFAULT '', applicant TEXT DEFAULT '',
    invoice_count INTEGER DEFAULT 0, handler TEXT DEFAULT '', record_date TEXT NOT NULL,
    notes TEXT DEFAULT '', review_status TEXT DEFAULT '', reviewed_by TEXT DEFAULT '',
    reviewed_at TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime'))
)"""
COPY_FIELDS = ('id', 'record_type', 'category', 'source_dest', 'applicant', 'invoice_count',
               'handler', 'record_date', 'notes', 'review_status', 'reviewed_by', 'reviewed_at', 'created_at')


def migrate(conn):
    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS funds_money_migrations (version INTEGER PRIMARY KEY)')
        if conn.execute('SELECT 1 FROM funds_money_migrations WHERE version=1').fetchone():
            conn.commit()
            return
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records'").fetchone()
        if not exists:
            conn.execute(RECORD_SCHEMA)
        else:
            cols = {r[1] for r in conn.execute('PRAGMA table_info(records)')}
            for col in ('review_status', 'reviewed_by', 'reviewed_at'):
                if col not in cols:
                    conn.execute(f"ALTER TABLE records ADD COLUMN {col} TEXT DEFAULT ''")
            rows = conn.execute('SELECT * FROM records ORDER BY id').fetchall()
            names = [col[1] for col in conn.execute('PRAGMA table_info(records)')]
            converted = []
            for values in rows:
                row = dict(zip(names, values))
                try:
                    cents = to_cents(row['amount'], positive=True)
                except ValueError as exc:
                    raise RuntimeError(f"资金金额迁移失败，记录 ID={row['id']}：{exc}") from exc
                converted.append((*[row[key] for key in COPY_FIELDS], cents))
            # Keep original SQLite values, including their storage type, for reconciliation.
            conn.execute('CREATE TABLE funds_legacy_amounts (record_id INTEGER PRIMARY KEY, amount)')
            conn.execute('INSERT INTO funds_legacy_amounts SELECT id,amount FROM records')
            has_sequence = conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
            sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='records'").fetchone() if has_sequence else None
            indexes = conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='records' AND sql IS NOT NULL").fetchall()
            triggers = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='records'").fetchall()
            conn.execute('ALTER TABLE records RENAME TO records_before_cents')
            conn.execute(RECORD_SCHEMA)
            fields = ','.join((*COPY_FIELDS, 'amount_cents'))
            conn.executemany(f"INSERT INTO records({fields}) VALUES({','.join('?' for _ in range(len(COPY_FIELDS)+1))})", converted)
            conn.execute('DROP TABLE records_before_cents')
            for sql, in indexes:
                conn.execute(sql)
            for sql, in triggers:
                conn.execute(sql)
            if sequence:
                current = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='records'").fetchone()
                highest = max(sequence[0], current[0] if current else 0)
                conn.execute("DELETE FROM sqlite_sequence WHERE name='records'")
                conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('records',?)", (highest,))
        conn.execute('INSERT INTO funds_money_migrations VALUES(1)')
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
