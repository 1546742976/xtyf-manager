import unittest
import test_asset_units as fixture


class MineSummaryTest(unittest.TestCase):
    setUp = fixture.UnitsTest.setUp
    client = fixture.UnitsTest.client
    post = fixture.UnitsTest.post
    save = fixture.UnitsTest.save
    receive = fixture.UnitsTest.receive
    get_units = fixture.UnitsTest.get_units
    operate = fixture.UnitsTest.operate

    def summary(self, client=None, suffix=''):
        return (client or self.member).get('/api/assets/units?mine=1'+suffix).get_json()['summary']

    def test_all_campuses_all_pages_and_custody_handoff(self):
        self.assertEqual(self.summary(), dict(quantity_loans=0, unit_loans=0, pending_receipts=0))
        item, _ = self.receive(22)
        units = self.get_units(item)
        units += self.admin.get(f'/api/assets/units?item_id={item}&page=2').get_json()['units']
        self.operate('borrow', units, self.member)
        self.assertEqual(self.summary()['unit_loans'], 22)
        self.assertEqual(self.summary(suffix='&page=2&campus_id='+str(self.other))['unit_loans'], 22)
        self.operate('handoff', self.get_units(item)[:1], self.member, member_id=self.admin_id)
        self.assertEqual(self.summary(self.admin)['pending_receipts'], 1)
        self.assertEqual(self.summary()['unit_loans'], 22)
        self.operate('accept', self.get_units(item)[:1])
        self.assertEqual(self.summary(self.admin), dict(quantity_loans=0, unit_loans=1, pending_receipts=0))
        self.operate('return', self.get_units(item)[:1], campus_id=self.other)
        self.assertEqual(self.summary(self.admin)['unit_loans'], 0)

    def test_quantity_and_mixed_loans_are_counted_separately(self):
        for action in ('receive', 'borrow'):
            self.post(self.member, '/api/assets/operations', dict(action=action, item_id=self.item,
                campus_id=self.campus, quantity=3, reason='合成数量借用'))
        self.assertEqual(self.summary(), dict(quantity_loans=1, unit_loans=0, pending_receipts=0))
        item, _ = self.receive(1)
        self.operate('borrow', self.get_units(item), self.member)
        self.assertEqual(self.summary(), dict(quantity_loans=1, unit_loans=1, pending_receipts=0))
        loan = self.member.get('/api/assets/loans?mine=1').get_json()['loans'][0]
        self.post(self.member, '/api/assets/operations', dict(action='return', loan_id=loan['id'], quantity=3, reason='合成归还'))
        self.operate('return', self.get_units(item), self.member, campus_id=self.campus)
        self.assertEqual(self.summary(), dict(quantity_loans=0, unit_loans=0, pending_receipts=0))
