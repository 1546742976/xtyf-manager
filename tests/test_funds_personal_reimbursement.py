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

    def legacy_expense(self, amount):
        response = self.applicant.post('/funds/api/records', json={
            'request_id': uuid4().hex, 'record_type': '出', 'self_funded': True,
            'amount': amount, 'source_dest': '购买测试材料', 'record_date': '2026-11-23',
            'applicant_id': self.other_id, 'applicant': '其他人', 'invoice_count': 0,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        # Records migrated from before applicant-side selection have the default flag.
        with closing(funds.get_funds_db()) as conn, conn:
            conn.execute('UPDATE records SET invoice_selection=0 WHERE id=?', (result['id'],))
        return result

    def historical_expense(self, applicant, amount):
        response = self.admin.post('/funds/api/records', json={
            'request_id': uuid4().hex, 'record_type': '出', 'amount': amount,
            'applicant': applicant, 'handler': '经手测试', 'source_dest': '旧账材料',
            'record_date': '2026-11-20', 'invoice_count': 1,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        path = f"/funds/api/records/{response.get_json()['id']}"
        self.assertEqual(self.admin.post(path+'/review', json={'version': 1}).status_code, 200)
        return path

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

    def selected_expense(self, amount, selected, client=None):
        return (client or self.applicant).post('/funds/api/records', json={
            'request_id': uuid4().hex, 'record_type': '出', 'self_funded': True,
            'amount': amount, 'source_dest': '选票回归', 'record_date': '2026-11-23',
            'invoice_ids': selected, 'invoice_count': 99,
        })

    def test_selected_application_locks_invoices_until_delete_or_payment(self):
        first = self.invoice(self.applicant_id, '500.00')
        second = self.invoice(self.applicant_id, '180.00')
        oversize = self.invoice(self.applicant_id, '680.00')
        other = self.invoice(self.other_id, '180.00')
        choices = self.applicant.get('/funds/api/application-invoices').get_json()['data']
        self.assertEqual({item['id'] for item in choices}, {first, second})
        for invalid in (oversize, other):
            self.assertEqual(self.selected_expense('680.00', [invalid]).status_code, 400)
        self.assertEqual(self.selected_expense('100.00', [second]).status_code, 400)
        created = self.selected_expense('680.00', [first])
        self.assertEqual(created.status_code, 200, created.get_json())
        path = f"/funds/api/records/{created.get_json()['id']}"
        pending = self.applicant.get(path).get_json()['data']
        self.assertEqual((pending['invoice_count'], pending['personal_status'], pending['invoice_difference_cents']),
                         (1, '待补票', '18000'))
        self.assertEqual(self.reviewer.post(path+'/review', json={'version': 1}).status_code, 409)
        self.assertEqual(self.selected_expense('500.00', [first]).status_code, 400)
        self.assertEqual(self.other.get(path+'/application-invoices').status_code, 403)

        supplement = {'version': 1, 'invoice_ids': [first, second]}
        self.assertEqual(self.other.put(path+'/application-invoices', json=supplement).status_code, 403)
        self.assertEqual(self.applicant.put(path+'/application-invoices', json=supplement).status_code, 200)
        self.assertEqual(self.applicant.put(path+'/application-invoices', json=supplement).status_code, 409)
        self.assertEqual(self.applicant.get(path).get_json()['data']['personal_status'], '待审核')
        approved = self.reviewer.post(path+'/review', json={'version': 2})
        self.assertEqual(approved.status_code, 200, approved.get_json())
        self.assertEqual(self.applicant.get(path).get_json()['data']['personal_status'], '待付款')

        alternate = self.invoice(self.applicant_id, '180.00')
        choices = self.reviewer.get(path+'/personal-invoices').get_json()
        self.assertEqual({item['id'] for item in choices['data'] if item['selected']}, {first, second})
        payment = {'version': 3, 'generation': choices['generation'], 'invoice_ids': [first, second]}
        self.assertEqual(self.reviewer.post(path+'/personal-reimbursement', json={
            **payment, 'invoice_ids': [first, alternate]}).status_code, 409)
        self.assertEqual(self.reviewer.post(path+'/personal-reimbursement', json=payment).status_code, 200)
        mine = self.applicant.get('/invoices/api/invoices?scope=mine').get_json()['data']
        self.assertEqual({item['id'] for item in mine if item['personal_progress'] == '已个人报销'}, {first, second})
        self.assertEqual(self.applicant.get(path).get_json()['data']['personal_status'], '已个人报销')
        corrected = self.reviewer.post(path+'/personal-reimbursement', json={
            **payment, 'version': 4, 'operation': 'correct', 'reason': '换票', 'invoice_ids': [first, alternate],
        })
        self.assertEqual(corrected.status_code, 200, corrected.get_json())

        reused = self.selected_expense('180.00', [second])
        self.assertEqual(reused.status_code, 200, reused.get_json())
        reused_path = f"/funds/api/records/{reused.get_json()['id']}"
        self.assertEqual(self.admin.delete(reused_path+'?version=1').status_code, 200)
        self.assertEqual(self.selected_expense('180.00', [second]).status_code, 200)

    def test_managed_amount_change_and_competing_claims(self):
        from concurrent.futures import ThreadPoolExecutor

        without_selection = self.applicant.post('/funds/api/records', json={
            'request_id': uuid4().hex, 'record_type': '出', 'self_funded': True,
            'amount': '350.00', 'source_dest': '未选票', 'record_date': '2026-11-23',
        }).get_json()
        empty_path = f"/funds/api/records/{without_selection['id']}"
        self.assertEqual(self.applicant.get(empty_path).get_json()['data']['personal_status'], '待补票')
        self.assertEqual(self.reviewer.post(empty_path+'/review', json={'version': 1}).status_code, 409)

        invoice = self.invoice(self.applicant_id, '350.00')
        # Two already-authenticated browser sessions for the same applicant.
        browser = app_module.app.test_client()
        cookie = self.applicant.get_cookie('session')
        browser.set_cookie('session', cookie.value)
        with ThreadPoolExecutor(max_workers=2) as workers:
            requests = [workers.submit(self.selected_expense, '350.00', [invoice], client)
                        for client in (self.applicant, browser)]
            responses = [task.result() for task in requests]
        self.assertEqual(sorted(response.status_code for response in responses), [200, 400])
        created = next(response.get_json() for response in responses if response.status_code == 200)
        path = f"/funds/api/records/{created['id']}"
        self.assertEqual(self.reviewer.post(path+'/review', json={'version': 1}).status_code, 200)
        self.assertEqual(self.admin.put(path, json={'version': 2, 'amount': '400.00'}).status_code, 200)
        row = self.applicant.get(path).get_json()['data']
        self.assertEqual((row['review_status'], row['personal_status']), ('待审核', '待补票'))
        replacement = self.invoice(self.applicant_id, '400.00')
        response = self.applicant.put(path+'/application-invoices', json={'version': 3, 'invoice_ids': [replacement]})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.selected_expense('350.00', [invoice]).status_code, 200)
        self.assertEqual(self.reviewer.post(path+'/review', json={'version': 4}).status_code, 200)

    def test_member_application_review_payment_and_invoice_move(self):
        amount = '61799.27'
        baseline = self.other.get('/funds/api/stats').get_json()['data']
        created = self.legacy_expense(amount)
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
        another = self.legacy_expense(amount)['id']
        self.assertEqual(self.reviewer.post(f'/funds/api/records/{another}/review', json={'version': 1}).status_code, 200)
        self.assertEqual(self.reviewer.post(f'/funds/api/records/{another}/personal-reimbursement',
                                            json=payment).status_code, 409)

        move = self.admin.post('/invoices/api/invoice/update', json={
            'id': selected, 'status': '正在报销', 'reimbursed_date': '2026-11-23',
        }).get_json()
        self.assertTrue(move['moved'], move)
        self.assertEqual(self.admin.post('/invoices/api/invoice/update', json={
            'id': move['id'], 'submitter_id': self.other_id,
        }).status_code, 409)
        filter_args = {'scope': 'mine', 'status': '已个人报销'}
        mine = self.applicant.get('/invoices/api/invoices', query_string=filter_args).get_json()['data']
        self.assertEqual([(invoice['id'], invoice['status']) for invoice in mine], [(move['id'], '已个人报销')])
        self.assertNotIn('personal_invoice_key', mine[0])
        self.assertEqual(mine[0]['reimbursed_date'], '')
        after = self.applicant.get('/invoices/api/stats?scope=mine').get_json()
        self.assertEqual(after['status_stats']['已个人报销']['count'], 1)
        all_invoices = self.admin.get('/invoices/api/invoices', query_string={'id': move['id']}).get_json()['data']
        self.assertEqual(all_invoices[0]['status'], '正在报销')
        self.assertEqual(all_invoices[0]['reimbursed_date'], '2026-11-23')
        self.assertEqual(self.other.get('/invoices/api/invoices', query_string=filter_args).get_json()['total'], 0)

    def test_multiple_invoices_are_validated_and_corrections_release_old_links(self):
        row = self.legacy_expense('0.30')
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
        other_application = self.legacy_expense('0.30')
        other_path = f"/funds/api/records/{other_application['id']}"
        self.assertEqual(self.reviewer.post(other_path+'/review', json={'version': 1}).status_code, 200)
        self.assertNotIn(first, {item['id'] for item in self.reviewer.get(other_path+'/personal-invoices').get_json()['data']})
        self.assertEqual(self.reviewer.post(other_path+'/personal-reimbursement', json=decision).status_code, 409)

        correction = {**decision, 'operation': 'correct', 'version': 3, 'reason': '补交正确的票据'}
        unchanged = self.reviewer.post(payment, json=correction)
        self.assertEqual(unchanged.status_code, 409)
        self.assertEqual(unchanged.get_json()['msg'], '关联发票未变更')
        correction['invoice_ids'] = [first, replacement]
        with patch.object(funds, 'now_str', return_value='2026-11-24 08:00:00'):
            result = self.reviewer.post(payment, json=correction)
        self.assertEqual(result.status_code, 200, result.get_json())
        states = {item['id']: item['status'] for item in self.applicant.get(
            '/invoices/api/invoices?scope=mine').get_json()['data']}
        self.assertEqual(states, {first: '已个人报销', second: '未个人报销', replacement: '已个人报销'})
        history = self.reviewer.get(payment+'/history').get_json()['data']
        self.assertEqual([event['operation'] for event in history], ['correct', 'confirm'])
        self.assertEqual(history[0]['actor_id'], self.reviewer_id)
        self.assertEqual(history[0]['reason'], correction['reason'])
        self.assertEqual(history[0]['after']['paid_at'], history[1]['after']['paid_at'])
        self.assertNotEqual(history[0]['before']['invoices'], history[0]['after']['invoices'])
        options = self.reviewer.get(other_path+'/personal-invoices').get_json()['data']
        self.assertEqual([item['id'] for item in options], [second])

    def test_historical_backfill_defaults_to_the_ledger_applicant_without_a_date(self):
        amount = '56.78'
        name = '旧账申请人'
        applicant, applicant_id = self.member(name)
        path = self.historical_expense(name, amount)
        payment = path+'/personal-reimbursement'
        balance = applicant.get('/funds/api/stats').get_json()['data']['balance']
        invoice = self.invoice(applicant_id, amount)
        self.invoice(self.other_id, amount)
        choices = self.reviewer.get(path+'/personal-invoices')
        self.assertEqual(choices.status_code, 200)
        result = choices.get_json()
        self.assertEqual([item['id'] for item in result['data']], [invoice])
        entry = {'operation': 'backfill', 'version': 2, 'generation': result['generation'],
                 'invoice_ids': [invoice], 'reason': '按旧付款凭证补录'}
        self.assertEqual(self.reviewer.post(payment, json={**entry, 'reason': ''}).status_code, 400)
        self.assertEqual(self.admin.post(payment, json=entry).status_code, 403)
        recorded_at = '2026-11-23 08:00:00'
        with patch.object(funds, 'now_str', return_value=recorded_at):
            self.assertEqual(self.reviewer.post(payment, json=entry).status_code, 200)
        reimbursed = applicant.get(path).get_json()['data']
        self.assertEqual(reimbursed['applicant_id'], applicant_id)
        self.assertEqual(reimbursed['personal_paid_at'], recorded_at)
        self.assertEqual(applicant.get(payment+'/history').status_code, 403)
        self.assertEqual(applicant.get('/funds/api/stats').get_json()['data']['balance'], balance)

        revoke = {'operation': 'revoke', 'version': 3, 'reason': '旧付款凭证需复核'}
        self.assertEqual(self.reviewer.post(payment, json={**revoke, 'reason': ' '}).status_code, 400)
        self.assertEqual(self.reviewer.post(payment, json=revoke).status_code, 200)
        restored = applicant.get(path).get_json()['data']
        self.assertEqual((restored['applicant_id'], restored['applicant'], restored['self_funded']),
                         (None, name, 0))
        events = self.reviewer.get(payment+'/history').get_json()['data']
        self.assertEqual([event['operation'] for event in events], ['revoke', 'backfill'])
        self.assertEqual(events[0]['before']['invoices'][0]['amount'], amount)
        self.assertEqual(events[0]['after']['invoices'], [])
        self.assertEqual(applicant.get('/invoices/api/invoices?scope=mine').get_json()['data'][0]['status'],
                         '未个人报销')

    def test_historical_backfill_requests_a_member_when_name_cannot_be_resolved(self):
        for name, amount in [('同名申请人', '11.01'), ('无对应成员', '11.02')]:
            with self.subTest(name=name):
                path = self.historical_expense(name, amount)
                invoice = self.invoice(self.applicant_id, amount)
                choices_url = path+'/personal-invoices'
                payment = path+'/personal-reimbursement'
                self.assertEqual(self.reviewer.get(choices_url).status_code, 400)
                result = self.reviewer.get(choices_url, query_string={'applicant_id': self.applicant_id}).get_json()
                self.assertEqual([item['id'] for item in result['data']], [invoice])
                entry = {'operation': 'backfill', 'version': 2, 'generation': result['generation'],
                         'invoice_ids': [invoice], 'reason': '按旧付款凭证补录'}
                self.assertEqual(self.reviewer.post(payment, json=entry).status_code, 400)
                entry['applicant_id'] = self.applicant_id
                self.assertEqual(self.reviewer.post(payment, json=entry).status_code, 200)

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
