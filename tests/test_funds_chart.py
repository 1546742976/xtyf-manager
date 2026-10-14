"""Theme-aware finance PNGs use real rendering against isolated test data."""
import base64
import io
import unittest
from unittest.mock import patch

from PIL import Image
from test_tasks import app_module
from modules.funds import summarize_records


class TestFundsChartTheme(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={
            'username': 'admin', 'password': 'ci-test-password-only',
        }).get_json()['ok'])
        self.original_theme = self.client.get('/api/account/preferences').get_json()['theme']

    def tearDown(self):
        self.client.post('/api/account/preferences', json={'theme': self.original_theme})

    def test_all_chart_types_render_transparently_in_both_themes(self):
        populated = summarize_records([
            {'record_type': '进', 'amount_cents': 50000, 'record_date': '2026-10-14',
             'source_dest': 'Sponsor', 'category': '', 'applicant': '', 'handler': ''},
            {'record_type': '出', 'amount_cents': 12500, 'record_date': '2026-10-15',
             'source_dest': 'Shop', 'category': 'Parts', 'applicant': 'Member', 'handler': 'Admin'},
        ])
        for theme, text_rgb in [('light', (29, 29, 31)), ('dark', (245, 245, 247))]:
            saved = self.client.post('/api/account/preferences', json={'theme': theme})
            self.assertEqual(saved.status_code, 200)
            for empty, stats in [(True, summarize_records([])), (False, populated)]:
                for chart_type in ('monthly', 'category', 'applicant', 'source'):
                    with self.subTest(theme=theme, empty=empty, chart=chart_type):
                        with patch('modules.funds.summarize_records', return_value=stats):
                            result = self.client.get('/funds/api/chart', query_string={'type': chart_type})
                        self.assertEqual(result.status_code, 200)
                        payload = result.get_json()
                        self.assertEqual(payload['code'], 0, payload)
                        png = base64.b64decode(payload['data'].split(',', 1)[1])
                        with Image.open(io.BytesIO(png)) as image:
                            self.assertEqual(image.format, 'PNG')
                            rgba = image.convert('RGBA')
                            self.assertEqual(rgba.getpixel((0, 0))[3], 0)
                            histogram = dict((color, count) for count, color in rgba.getcolors(rgba.width * rgba.height))
                            self.assertGreater(histogram.get((*text_rgb, 255), 0), 10)
                            if empty:
                                transparent = sum(count for color, count in histogram.items() if color[3] == 0)
                                self.assertGreater(transparent, rgba.width * rgba.height * .9)

    def test_unknown_chart_still_returns_error(self):
        result = self.client.get('/funds/api/chart?type=invalid').get_json()
        self.assertEqual(result['code'], 1)


if __name__ == '__main__':
    unittest.main()
