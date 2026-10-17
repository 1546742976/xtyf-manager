"""Theme-aware finance PNGs use real rendering against isolated test data."""
import base64
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageColor
from test_tasks import app_module
from modules.funds import CHART_PALETTES, chart_palette, summarize_records, _pie_ink


class TestFundsChartTheme(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.assertTrue(self.client.post('/login', json={
            'username': 'admin', 'password': '123456',
        }).get_json()['ok'])
        self.original_theme = self.client.get('/api/account/preferences').get_json()

    def tearDown(self):
        self.client.post('/api/account/preferences', json={'theme': self.original_theme['theme']})
        self.client.post('/api/account/preferences', json={key: self.original_theme[key] for key in ('mode', 'light_theme', 'dark_theme')})

    def test_all_chart_types_render_transparently_in_all_eight_palettes(self):
        populated = summarize_records([
            {'record_type': '进', 'amount_cents': 50000, 'record_date': '2026-10-14',
             'source_dest': 'Sponsor', 'category': '', 'applicant': '', 'handler': ''},
            {'record_type': '出', 'amount_cents': 12500, 'record_date': '2026-10-15',
             'source_dest': 'Shop', 'category': 'Parts', 'applicant': 'Member', 'handler': 'Admin'},
        ])
        for palette, tokens in CHART_PALETTES.items():
            text_rgb = ImageColor.getrgb(tokens[1])
            for empty, stats in [(True, summarize_records([])), (False, populated)]:
                for chart_type in ('monthly', 'category', 'applicant', 'source'):
                    with self.subTest(palette=palette, empty=empty, chart=chart_type):
                        with patch('modules.funds.summarize_records', return_value=stats):
                            result = self.client.get('/funds/api/chart', query_string={'type': chart_type, 'palette': palette})
                        self.assertEqual(result.status_code, 200)
                        payload = result.get_json()
                        self.assertEqual(payload['code'], 0, payload)
                        self.assertEqual(payload['palette'], palette)
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

    def test_palette_query_overrides_system_fallback_but_never_accepts_arbitrary_colors(self):
        self.client.post('/api/account/preferences', json={'theme': 'dark'})
        self.client.post('/api/account/preferences', json={'mode': 'system', 'light_theme': 'paw', 'dark_theme': 'graphite'})
        for requested, expected in [(None, 'graphite'), ('#ff0000', 'graphite'),
                                    ('__import__("os")', 'graphite'), ('paw', 'paw')]:
            with patch('modules.funds.summarize_records', return_value=summarize_records([])):
                data = self.client.get('/funds/api/chart', query_string={'type': 'source', **({'palette': requested} if requested is not None else {})}).get_json()
            self.assertEqual(data['code'], 0, data)
            self.assertEqual(data['palette'], expected)
        self.assertEqual(chart_palette(None, {'ui_theme': 'light'})[0], 'silver')
        self.assertEqual(chart_palette(None, {'ui_theme': 'dark'})[0], 'space')
        self.assertEqual(chart_palette(None, {'ui_theme': 'dark', 'theme_mode': 'light', 'light_theme': 'mist'})[0], 'mist')

    def test_plot_labels_and_pie_percentages_meet_text_contrast(self):
        def luminance(hex_color):
            rgb = [x / 255 for x in ImageColor.getrgb(hex_color)]
            linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in rgb]
            return sum(x * weight for x, weight in zip(linear, (.2126, .7152, .0722)))
        def contrast(first, second):
            bright, dark = sorted((luminance(first), luminance(second)), reverse=True)
            return (bright + .05) / (dark + .05)
        self.assertEqual(len(CHART_PALETTES), 8)
        for palette in CHART_PALETTES:
            _, colors = chart_palette(palette, {})
            for role in ('text', 'muted'):
                self.assertGreaterEqual(contrast(colors[role], colors['surface']), 4.5, (palette, role))
            for role in ('primary', 'income', 'expense'):
                rgba = [value / 255 for value in ImageColor.getrgb(colors[role])] + [1]
                self.assertGreaterEqual(contrast(_pie_ink(rgba), colors[role]), 4.5, (palette, role))

    def test_unknown_chart_still_returns_error(self):
        result = self.client.get('/funds/api/chart?type=invalid').get_json()
        self.assertEqual(result['code'], 1)


if __name__ == '__main__':
    unittest.main()
