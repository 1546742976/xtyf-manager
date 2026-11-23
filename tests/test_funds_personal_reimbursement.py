"""Exercise ledger and invoice synchronization through their normal APIs."""
from io import BytesIO
import os
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

from test_tasks import app_module
from modules import funds, funds_revisions, invoices


class TestPersonalReimbursement(unittest.TestCase):
    def setUp(self):
        self.admin = self.login('admin', 'ci-test-password-only')
        self.reviewer, self.reviewer_id = self.member('审核测试', role='manager')
        grant = self.admin.post(f'/api/admin/permissions/{self.reviewer_id}', json={
            'permissions': ['funds.review', 'invoices.manage'],
        })
        self.assertEqual(grant.status_code, 200)
        designation = patch.dict(os.environ, {'FUNDS_PERSONAL_REVIEWER_ID': str(self.reviewer_id)})
        designation.start()
        self.addCleanup(designation.stop)
        self.applicant, self.applicant_id = self.member('同名申请人')
        self.other, self.other_id = self.member('同名申请人')

    def login(self, username, password):
        client = app_module.app.test_client()
        self.assertTrue(client.post('/login', json={'username': username, 'password': password}).get_json()['ok'])
        return client

    def member(self, display_name, role='member'):
        username, password = 'finance_' + uuid4().hex[:12], 'finance-test-password'
        created = self.admin.post('/api/member', json={
            'name': display_name, 'username': username, 'password': password, 'role': role,
        }).get_json()
        self.assertTrue(created['ok'], created)
        return self.login(username, password), created['id']

    def expense(self, amount):
        response = self.applicant.post('/funds/api/records', json={
            'request_id': uuid4().hex, 'record_type': '出', 'self_funded': True,
            'amount': amount, 'source_dest': '购买测试材料', 'record_date': '2026-11-23',
            'applicant_id': self.other_id, 'applicant': '其他人', 'invoice_count': 0,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def invoice(self, owner, amount, status='未报销'):
        token = uuid4().hex
        upload = self.admin.post('/invoices/api/upload', data={
            'file': (BytesIO(b'%PDF-1.4\n% ' + token.encode() + b'\n%%EOF'), 'test.pdf'),
        }).get_json()
        response = self.admin.post('/invoices/api/upload/confirm', json={
            'tmp_path': upload['tmp_path'], 'month': '2026.11', 'date': '2026-11-23',
            'amount': amount, 'submitter_id': owner, 'status': status, 'invoice_number': token[:16],
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()['rel_path']

    def test_member_application_review_payment_and_invoice_move(self):
        amount = '61799.27'
        baseline = self.other.get('/funds/api/stats').get_json()['data']
        created = self.expense(amount)
        record_url = f"/funds/api/records/{created['id']}"
        pending = self.other.get(record_url).get_json()['data']
        self.assertEqual(pending['applicant_id'], self.applicant_id)
        self.assertEqual(pending['applicant'], '同名申请人')
        self.assertEqual(pending['personal_status'], '未个人报销')
        self.assertEqual(pending['review_status'], '待审核')
        self.assertEqual(self.applicant.put(record_url, json={'version': 1, 'amount': '1.00'}).status_code, 403)

        initial_stats = self.other.get('/funds/api/stats').get_json()['data']
        self.assertEqual(initial_stats['balance'], baseline['balance'])
        self.assertEqual(int(initial_stats['unapproved_out_cents']) - int(baseline['unapproved_out_cents']), 6179927)
        self.assertEqual(initial_stats['record_count'], baseline['record_count'] + 1)

        selected = self.invoice(self.applicant_id, amount)
        second = self.invoice(self.applicant_id, amount, '已报销')
        unrelated = self.invoice(self.other_id, amount)
        self.invoice(self.reviewer_id, '71.02')
        before = self.applicant.get('/invoices/api/stats?scope=mine').get_json()
        self.assertEqual(before['status_stats']['未个人报销']['count'], 2)
        self.assertEqual(before['status_stats']['已个人报销']['count'], 0)
        self.assertEqual(self.reviewer.get('/invoices/api/stats?scope=mine').get_json()['total_count'], 1)

        self.assertIn('id="refreshPersonalInvoices"', self.reviewer.get('/funds/').get_data(as_text=True))
        for client in (self.admin, self.other):
            self.assertNotIn('id="refreshPersonalInvoices"', client.get('/funds/').get_data(as_text=True))
            self.assertEqual(client.get('/funds/api/personal-invoices').status_code, 403)
            self.assertEqual(client.post(record_url+'/review', json={'version': 1}).status_code, 403)
            self.assertEqual(client.post(record_url+'/personal-reimbursement', json={'version': 1}).status_code, 403)

        with patch.object(invoices, 'scan_invoices', side_effect=AssertionError('lookup should use the catalog')):
            lookup = self.reviewer.get('/funds/api/personal-invoices').get_json()
        self.assertEqual({item['id'] for item in lookup['matches']['6179927']}, {selected, second, unrelated})
        payment = {'invoice_id': selected, 'generation': lookup['generation'], 'version': 1}
        self.assertEqual(self.reviewer.post(record_url+'/personal-reimbursement', json=payment).status_code, 409)
        approved = self.reviewer.post(record_url+'/review', json={'version': 1})
        self.assertEqual(approved.status_code, 200, approved.get_json())
        payment['version'] = approved.get_json()['version']
        approved_stats = self.other.get('/funds/api/stats').get_json()['data']
        self.assertEqual(int(baseline['balance_cents']) - int(approved_stats['balance_cents']), 6179927)
        self.assertEqual(approved_stats['unapproved_out'], baseline['unapproved_out'])
        self.assertEqual(self.reviewer.post(record_url+'/personal-reimbursement', json={
            **payment, 'invoice_id': unrelated,
        }).status_code, 409)
        self.assertEqual(self.reviewer.post(record_url+'/personal-reimbursement', json={
            **payment, 'generation': 'stale',
        }).status_code, 409)

        paid = self.reviewer.post(record_url+'/personal-reimbursement', json=payment)
        self.assertEqual(paid.status_code, 200, paid.get_json())
        self.assertEqual(self.other.get(record_url).get_json()['data']['personal_status'], '已个人报销')
        self.assertEqual(self.admin.delete(record_url, query_string={'version': paid.get_json()['version']}).status_code, 409)
        self.assertEqual(self.reviewer.post(record_url+'/personal-reimbursement', json=payment).status_code, 409)
        another = self.expense(amount)['id']
        self.assertEqual(self.reviewer.post(f'/funds/api/records/{another}/review', json={'version': 1}).status_code, 200)
        self.assertEqual(self.reviewer.post(f'/funds/api/records/{another}/personal-reimbursement',
                                            json=payment).status_code, 409)

        move = self.admin.post('/invoices/api/invoice/update', json={'id': selected, 'status': '正在报销'}).get_json()
        self.assertTrue(move['moved'], move)
        self.assertEqual(self.admin.post('/invoices/api/invoice/update', json={
            'id': move['id'], 'submitter_id': self.other_id,
        }).status_code, 409)
        filter_args = {'scope': 'mine', 'status': '已个人报销'}
        mine = self.applicant.get('/invoices/api/invoices', query_string=filter_args).get_json()['data']
        self.assertEqual([(invoice['id'], invoice['status']) for invoice in mine], [(move['id'], '已个人报销')])
        self.assertNotIn('personal_invoice_key', mine[0])
        after = self.applicant.get('/invoices/api/stats?scope=mine').get_json()
        self.assertEqual(after['status_stats']['已个人报销']['count'], 1)
        all_invoices = self.admin.get('/invoices/api/invoices', query_string={'id': move['id']}).get_json()['data']
        self.assertEqual(all_invoices[0]['status'], '正在报销')
        self.assertEqual(self.other.get('/invoices/api/invoices', query_string=filter_args).get_json()['total'], 0)

    def test_multiple_invoices_are_validated_and_corrections_release_old_links(self):
        row = self.expense('0.30')
        path = f"/funds/api/records/{row['id']}"
        payment = path+'/personal-reimbursement'
        first = self.invoice(self.applicant_id, '0.10')
        second = self.invoice(self.applicant_id, '0.20')
        replacement = self.invoice(self.applicant_id, '0.20')
        unrelated = self.invoice(self.other_id, '0.20')
        options = self.reviewer.get(path+'/personal-invoices').get_json()
        self.assertEqual({item['id'] for item in options['data']}, {first, second, replacement})
        decision = {'version': 1, 'generation': options['generation'], 'invoice_ids': [first, second]}
        self.assertEqual(self.reviewer.post(payment, json=decision).status_code, 409)
        self.assertEqual(self.reviewer.post(path+'/review', json={'version': 1}).status_code, 200)
        decision['version'] = 2
        for selection, expected in [([first, first], 400), ([first], 409), ([first, unrelated], 409)]:
            self.assertEqual(self.reviewer.post(payment, json={**decision, 'invoice_ids': selection}).status_code, expected)

        with closing(funds.get_funds_db()) as conn:
            conn.execute("""CREATE TRIGGER fail_reimbursement_event BEFORE INSERT ON funds_personal_events
                BEGIN SELECT RAISE(ABORT,'synthetic event write failure'); END""")
            conn.commit()
        try:
            self.assertEqual(self.reviewer.post(payment, json=decision).status_code, 503)
            unpaid = self.applicant.get(path).get_json()['data']
            self.assertEqual((unpaid['personal_paid_at'], unpaid['version']), (None, 2))
            self.assertTrue(set(funds.personal_invoice_payments()).isdisjoint({
                invoices.load_meta()[invoice]['personal_invoice_key'] for invoice in (first, second)
            }))
        finally:
            with closing(funds.get_funds_db()) as conn:
                conn.execute('DROP TRIGGER fail_reimbursement_event')
                conn.commit()

        result = self.reviewer.post(payment, json=decision)
        self.assertEqual(result.status_code, 200, result.get_json())
        selected = self.reviewer.get(path+'/personal-invoices').get_json()['data']
        self.assertEqual({item['id'] for item in selected if item['selected']}, {first, second})
        other_application = self.expense('0.30')
        other_path = f"/funds/api/records/{other_application['id']}"
        self.assertEqual(self.reviewer.post(other_path+'/review', json={'version': 1}).status_code, 200)
        self.assertNotIn(first, {item['id'] for item in self.reviewer.get(other_path+'/personal-invoices').get_json()['data']})
        self.assertEqual(self.reviewer.post(other_path+'/personal-reimbursement', json=decision).status_code, 409)

        correction = {**decision, 'operation': 'correct', 'version': 3, 'paid_date': '2026-11-22',
                      'invoice_ids': [first, replacement], 'reason': '补交正确的票据'}
        result = self.reviewer.post(payment, json=correction)
        self.assertEqual(result.status_code, 200, result.get_json())
        states = {item['id']: item['status'] for item in self.applicant.get(
            '/invoices/api/invoices?scope=mine').get_json()['data']}
        self.assertEqual(states, {first: '已个人报销', second: '未个人报销', replacement: '已个人报销'})
        history = self.reviewer.get(payment+'/history').get_json()['data']
        self.assertEqual([event['operation'] for event in history], ['correct', 'confirm'])
        self.assertEqual(history[0]['actor_id'], self.reviewer_id)
        self.assertEqual(history[0]['reason'], correction['reason'])
        self.assertEqual(history[0]['after']['paid_at'], correction['paid_date'])
        self.assertNotEqual(history[0]['before']['invoices'], history[0]['after']['invoices'])
        options = self.reviewer.get(other_path+'/personal-invoices').get_json()['data']
        self.assertEqual([item['id'] for item in options], [second])

    def test_historical_backfill_requires_an_explicit_owner_date_and_reason(self):
        amount = '56.78'
        legacy = self.admin.post('/funds/api/records', json={
            'request_id': uuid4().hex, 'record_type': '出', 'amount': amount,
            'applicant': '旧账中的姓名', 'handler': '经手测试', 'source_dest': '旧账材料',
            'record_date': '2026-11-20', 'invoice_count': 1,
        }).get_json()
        path = f"/funds/api/records/{legacy['id']}"
        payment = path+'/personal-reimbursement'
        self.assertEqual(self.admin.post(path+'/review', json={'version': 1}).status_code, 200)
        balance = self.applicant.get('/funds/api/stats').get_json()['data']['balance']
        invoice = self.invoice(self.applicant_id, amount)
        self.invoice(self.other_id, amount)
        self.assertEqual(self.reviewer.get(path+'/personal-invoices').status_code, 400)
        result = self.reviewer.get(path+'/personal-invoices', query_string={'applicant_id': self.applicant_id}).get_json()
        self.assertEqual([item['id'] for item in result['data']], [invoice])
        entry = {'operation': 'backfill', 'version': 2, 'generation': result['generation'],
                 'paid_date': '2026-11-20', 'invoice_ids': [invoice], 'applicant_id': self.applicant_id,
                 'reason': '按旧付款凭证补录'}
        for invalid in ({'reason': ''}, {'paid_date': '2099-01-01'}, {'applicant_id': None}):
            self.assertEqual(self.reviewer.post(payment, json={**entry, **invalid}).status_code, 400)
        self.assertEqual(self.admin.post(payment, json=entry).status_code, 403)
        self.assertEqual(self.reviewer.post(payment, json=entry).status_code, 200)
        reimbursed = self.applicant.get(path).get_json()['data']
        self.assertEqual(reimbursed['applicant_id'], self.applicant_id)
        self.assertEqual(reimbursed['personal_paid_at'], '2026-11-20')
        self.assertEqual(self.applicant.get(payment+'/history').status_code, 403)
        self.assertEqual(self.applicant.get('/funds/api/stats').get_json()['data']['balance'], balance)

        revoke = {'operation': 'revoke', 'version': 3, 'reason': '旧付款凭证需复核'}
        self.assertEqual(self.reviewer.post(payment, json={**revoke, 'reason': ' '}).status_code, 400)
        self.assertEqual(self.reviewer.post(payment, json=revoke).status_code, 200)
        restored = self.applicant.get(path).get_json()['data']
        self.assertEqual((restored['applicant_id'], restored['applicant'], restored['self_funded']),
                         (None, '旧账中的姓名', 0))
        events = self.reviewer.get(payment+'/history').get_json()['data']
        self.assertEqual([event['operation'] for event in events], ['revoke', 'backfill'])
        self.assertEqual(events[0]['before']['invoices'][0]['amount'], amount)
        self.assertEqual(events[0]['after']['invoices'], [])
        self.assertEqual(self.applicant.get('/invoices/api/invoices?scope=mine').get_json()['data'][0]['status'],
                         '未个人报销')

    def test_migration_keeps_existing_single_invoice_allocations(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.executescript("""
                CREATE TABLE funds_revision_migrations(version INTEGER PRIMARY KEY);
                INSERT INTO funds_revision_migrations VALUES(1),(2);
                CREATE TABLE records(id INTEGER PRIMARY KEY, amount_cents INTEGER, applicant_id INTEGER,
                                     personal_invoice_key TEXT, personal_paid_at TEXT);
                INSERT INTO records VALUES(12,3070,7,'legacy-invoice','2025-01-01 12:00:00');
            """)
            funds_revisions.migrate(conn)
            funds_revisions.migrate(conn)
            self.assertEqual(conn.execute('SELECT invoice_key,record_id,amount_cents FROM funds_personal_invoices').fetchall(),
                             [('legacy-invoice', 12, 3070)])
            self.assertEqual(conn.execute('SELECT personal_paid_at FROM records').fetchone()[0], '2025-01-01 12:00:00')


if __name__ == '__main__':
    unittest.main()
