"""Durable creation receipts in the same SQLite transaction as ledger records."""
import hashlib
import json
import re

from modules.money import format_cents


def migrate(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS funds_requests (
            actor_id INTEGER NOT NULL, request_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL, result_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(actor_id, request_id))''')


def valid_request_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{16,100}', value) is not None


def fingerprint(data, amount_cents):
    payload = {key: value for key, value in data.items() if key != 'request_id'}
    payload['amount'] = format_cents(amount_cents)
    # Never add current user names, permission-derived defaults or current dates.
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()
