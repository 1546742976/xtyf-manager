"""Active invoice allocations and append-only changes, committed with the ledger."""

import json

from common import now_str
from modules.money import format_cents


def allocated_invoices(conn):
    return conn.execute("""SELECT i.invoice_key, i.record_id, r.applicant_id, r.personal_paid_at
                           FROM funds_personal_invoices i JOIN records r ON r.id=i.record_id""")


def paid_invoices(conn):
    return (row for row in allocated_invoices(conn) if row['personal_paid_at'])


def replace_allocations(conn, rid, allocations):
    conn.execute('DELETE FROM funds_personal_invoices WHERE record_id=?', (rid,))
    conn.executemany('INSERT INTO funds_personal_invoices VALUES(?,?,?,?)',
                     [(key, rid, cents, label) for key, cents, label in allocations])


def payment_state(conn, record):
    return {
        'applicant': record['applicant'], 'applicant_id': record['applicant_id'],
        'self_funded': record['self_funded'], 'paid_at': record['personal_paid_at'],
        'amount': format_cents(record['amount_cents']),
        'invoices': [
            {'key': row['invoice_key'], 'amount': format_cents(row['amount_cents']), 'label': row['label']}
            for row in conn.execute('SELECT * FROM funds_personal_invoices WHERE record_id=? ORDER BY invoice_key',
                                    (record['id'],))
        ],
    }


def apply_payment(conn, record, user, operation, reason, paid_at, owner, allocations):
    """The caller holds the ledger transaction and has validated the allocations."""
    rid = record['id']
    before = payment_state(conn, record)
    replace_allocations(conn, rid, allocations)
    if record['invoice_selection']:
        conn.execute('UPDATE records SET invoice_count=? WHERE id=?', (len(allocations), rid))
        if operation == 'revoke':
            conn.execute("""UPDATE records SET review_status='待审核',reviewed_by='',reviewed_at=''
                            WHERE id=?""", (rid,))
    conn.execute("""UPDATE records SET applicant_id=?, applicant=?, self_funded=?,
                      personal_invoice_key=NULL, personal_paid_at=?, version=version+1 WHERE id=?""",
                 (owner['applicant_id'], owner['applicant'], owner['self_funded'], paid_at, rid))
    updated = conn.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
    after = payment_state(conn, updated)
    conn.execute("""INSERT INTO funds_personal_events
        (record_id,operation,actor_id,actor_name,occurred_at,reason,before_json,after_json)
        VALUES (?,?,?,?,?,?,?,?)""", (rid, operation, user['id'], user['name'], now_str(), reason,
                                     json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))


def owner_after_revocation(conn, record):
    event = conn.execute("""SELECT operation,before_json FROM funds_personal_events WHERE record_id=?
                            AND operation IN ('confirm','backfill') ORDER BY id DESC LIMIT 1""",
                         (record['id'],)).fetchone()
    if event and event['operation'] == 'backfill':
        return json.loads(event['before_json'])
    return record


def payment_history(conn, record_id):
    history = []
    for row in conn.execute('SELECT * FROM funds_personal_events WHERE record_id=? ORDER BY id DESC', (record_id,)):
        event = dict(row)
        for side in ('before', 'after'):
            state = json.loads(event.pop(side + '_json'))
            for invoice in state['invoices']:
                invoice.pop('key')
            event[side] = state
        history.append(event)
    return history
