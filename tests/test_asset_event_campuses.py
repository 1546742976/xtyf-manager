"""Historical event queries must include every campus touched, without rewriting events."""
import unittest
import test_asset_lifecycle as fixture


class EventCampusesTest(unittest.TestCase):
    setUp = fixture.LifecycleTest.setUp
    client = fixture.LifecycleTest.client
    post = fixture.LifecycleTest.post
    save = fixture.LifecycleTest.save
    receive = fixture.LifecycleTest.receive
    get_units = fixture.LifecycleTest.get_units
    operate = fixture.LifecycleTest.operate
    object = fixture.LifecycleTest.object

    def assert_both(self, item, action):
        all_events = self.admin.get(f'/api/assets/events?item_id={item}').get_json()['events']
        expected = {e['id'] for e in all_events if e['action'] == action}
        self.assertTrue(expected)
        for campus in (self.campus, self.other):
            result = self.admin.get(f'/api/assets/events?item_id={item}&campus_id={campus}').get_json()
            ids = [e['id'] for e in result['events']]
            self.assertTrue(expected.issubset(ids), (campus, expected, ids))
            self.assertEqual(len(ids), len(set(ids)))

    def test_move_and_remote_return(self):
        item, _ = self.receive(1)
        self.operate('move', self.get_units(item), campus_id=self.other)
        self.assert_both(item, 'unit_move')
        self.operate('borrow', self.get_units(item), self.member)
        self.operate('return', self.get_units(item), self.member, campus_id=self.campus)
        self.assert_both(item, 'unit_return')

    def test_whole_object_and_pagination(self):
        item, _ = self.receive(2)
        obj = self.object()
        self.operate('borrow', self.get_units(item), self.member)
        self.operate('install', self.get_units(item), object_id=obj)
        self.post(self.admin, '/api/assets/units/object-move', dict(
            object_id=obj, version=1, campus_id=self.other, reason='合成整车移动',
            units=[dict(id=u['id'], version=u['version']) for u in self.get_units(item)]))
        self.assert_both(item, 'unit_move')
        self.operate('detach', self.get_units(item), location='test warehouse')
        for i in range(22):
            self.operate('move', self.get_units(item)[:1], campus_id=self.campus if i % 2 == 0 else self.other)
        for campus in (self.campus, self.other):
            url = f'/api/assets/events?item_id={item}&campus_id={campus}&per_page=20'
            first = self.admin.get(url).get_json()
            pages = [first] + [self.admin.get(url + f'&page={p}').get_json()
                               for p in range(2, (first['total'] + first['per_page'] - 1) // first['per_page'] + 1)]
            self.assertGreater(len(pages), 1)
            ids = [e['id'] for p in pages for e in p['events']]
            self.assertEqual(len(ids), pages[0]['total'])
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(len([e for p in pages for e in p['events'] if e['action'] == 'unit_move']), 24)
