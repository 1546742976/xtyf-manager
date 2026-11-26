import unittest
import test_assets as fixture


class PdfResourcesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from clients.frontend.vendor_dependencies import ensure_vendor_assets
        ensure_vendor_assets()

    setUp = fixture.AssetsTest.setUp
    client = fixture.AssetsTest.client
    post = fixture.AssetsTest.post
    save = fixture.AssetsTest.save

    def test_only_locked_public_pdf_resources_are_exposed(self):
        for resource in ('pdf.mjs', 'pdf.worker.mjs', 'cmaps/UniGB-UCS2-H.bcmap', 'standard_fonts/FoxitFixed.pfb'):
            response = self.admin.get('/vendor/pdfjs/'+resource)
            self.assertEqual(response.status_code, 200, resource)
            response.close()
        for resource in ('../shell.js', '%2e%2e%2fshell.js', 'shell.js', 'missing.mjs', '../../app.py'):
            response = self.admin.get('/vendor/pdfjs/'+resource)
            self.assertEqual(response.status_code, 404, resource)
            response.close()
