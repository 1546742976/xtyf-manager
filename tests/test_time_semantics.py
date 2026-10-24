"""Characterize current time semantics; do not migrate or normalize stored times."""
import os
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from test_tasks import app_module
import common
from modules import assets, security, task_mutations, task_queries
from modules.task_dates import dependency_start_date


def clock(moment, host_offset=0):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            local = moment.astimezone(tz or timezone(timedelta(hours=host_offset)))
            return local if tz else local.replace(tzinfo=None)
    return FixedDatetime


class TimeSemanticsTests(unittest.TestCase):
    def test_host_local_strings_and_explicit_beijing_across_month_and_year(self):
        for instant in ('2026-10-31T16:00:00+00:00', '2026-12-31T16:00:00+00:00'):
            moment = datetime.fromisoformat(instant)
            beijing = moment.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
            for offset in (0, 8):
                fixed = clock(moment, offset)
                with patch.object(common, 'datetime', fixed), patch.object(assets, 'datetime', fixed), \
                        patch.object(task_mutations, 'datetime', fixed):
                    self.assertEqual(common.now_str(), fixed.now().strftime('%Y-%m-%d %H:%M:%S'))
                    self.assertEqual(assets.stamp(), beijing)
                    self.assertEqual(task_mutations.completion_stamp(), beijing)

    def test_deadline_uses_beijing_day_on_utc_host(self):
        client = app_module.app.test_client()
        self.assertTrue(client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok'])
        created = client.post('/api/task', json={'title': '合成跨月日期验证', 'due_date': '2026-11-01'}).get_json()['id']
        for instant, expected in [('2026-10-31T15:59:59+00:00', 1), ('2026-10-31T16:00:00+00:00', 0)]:
            with patch.object(task_queries, 'datetime', clock(datetime.fromisoformat(instant))):
                tasks = client.get('/api/data').get_json()['tasks']
                self.assertEqual(next(t for t in tasks if t['id'] == created)['days_left'], expected)

    def test_calendar_dependencies_cross_month_without_timestamp_math(self):
        self.assertEqual(dependency_start_date('2026-11-01'), '2026-10-30')
        self.assertEqual(dependency_start_date('2028-03-01'), '2028-02-28')

    def test_audit_cutoff_is_strict_local_string_and_protects_pending(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript('CREATE TABLE security_audit(id INTEGER PRIMARY KEY,outcome TEXT,details TEXT,created_at TEXT);'
                             'CREATE TABLE invoice_jobs(audit_id INTEGER);')
            db.executemany('INSERT INTO security_audit VALUES(?,?,?,?)', [
                (1, 'ok', '{}', '2026-10-30 23:59:59'), (2, 'ok', '{}', '2026-10-31 00:00:00'),
                (3, 'ok', '{}', '2026-10-31 00:00:01'), (4, 'pending', '{}', '2020-01-01 00:00:00')])
            fixed = clock(datetime(2026, 11, 1, tzinfo=timezone.utc))
            with patch.dict(os.environ, {'AUDIT_RETENTION_DAYS': '1', 'AUDIT_MAX_ROWS': '0'}), \
                    patch.object(security, 'datetime', fixed):
                security.cleanup_audit_rows(db)
            self.assertEqual([r[0] for r in db.execute('SELECT id FROM security_audit ORDER BY id')], [2, 3, 4])
