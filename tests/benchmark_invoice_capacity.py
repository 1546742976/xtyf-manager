"""Opt-in synthetic capacity probe. Run with --count 500 or --count 2000.

Uses test_tasks' disposable stores, never the configured business directory.
Timing is evidence, not a machine-dependent pass/fail threshold.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import sys
import time
import tracemalloc
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_tasks import app_module  # noqa: E402
from modules import invoices  # noqa: E402
from reportlab.pdfgen.canvas import Canvas  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, choices=(500, 2000), required=True)
    args = parser.parse_args()
    app_module.app.config['TESTING'] = True
    invoices.start_invoice_worker = lambda: None
    client = app_module.app.test_client()
    assert client.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok']
    stream = io.BytesIO()
    document = Canvas(stream)
    document.drawString(72, 72, 'Synthetic invoice capacity fixture')
    document.showPage()
    document.save()
    pdf = stream.getvalue()
    folder = Path(invoices.BASE_DIR) / '2026.10' / '未报销'
    folder.mkdir(parents=True, exist_ok=True)
    metadata = {}
    for number in range(args.count):
        path = folder / f'capacity-{number:05d}.pdf'
        path.write_bytes(pdf)
        metadata[path.relative_to(invoices.BASE_DIR).as_posix()] = {
            'amount': '1.00', 'submitter_id': 1, 'submitter': '管理员', 'vendor': 'Synthetic'}
    invoices.save_meta(metadata)
    report = {'count': args.count, 'runtime': sys.version, 'durations_seconds': {}}

    def measure(name, call):
        started = time.perf_counter()
        result = call()
        report['durations_seconds'][name] = round(time.perf_counter() - started, 4)
        return result

    tracemalloc.start()
    with app_module.app.app_context():
        assert measure('initial_snapshot', invoices._publish_invoice_snapshot)
    report['python_traced_peak_bytes'] = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    with patch.object(invoices, 'scan_invoices', side_effect=AssertionError('read/rejection scanned PDFs')):
        for name, route in [('list', '/invoices/api/invoices'), ('stats', '/invoices/api/stats'),
                            ('export', '/invoices/api/export/download')]:
            response = measure(name, lambda: client.get(route))
            assert response.status_code == 200, (route, response.status_code)
            response.close()
        response = measure('invalid_write', lambda: client.post('/invoices/api/invoice/update', json={'id': next(iter(metadata)), 'month': 'invalid'}))
        assert response.status_code == 400
    # A genuinely new PDF gets distinct bytes; all stores and users are synthetic.
    upload = client.post('/invoices/api/upload', data={'file': (io.BytesIO(pdf + b'\n% new receipt'), 'new.pdf')}).get_json()
    with patch.object(invoices, 'scan_invoices', wraps=invoices.scan_invoices) as scans:
        response = measure('valid_receipt_publish', lambda: client.post('/invoices/api/upload/confirm', json={
            'tmp_path': upload['tmp_path'], 'month': '2026.10', 'amount': '2.00', 'submitter_id': 1}))
        assert response.status_code == 200, response.get_json()
        assert scans.call_count == 1, scans.call_count
    report['scans'] = {'unchanged_reads_and_invalid_writes': 0, 'valid_receipt': scans.call_count}
    clients = [app_module.app.test_client() for _ in range(4)]
    for reader in clients:
        assert reader.post('/login', json={'username': 'admin', 'password': 'ci-test-password-only'}).get_json()['ok']
    def read(reader):
        start = time.perf_counter()
        response = reader.get('/invoices/api/invoices')
        assert response.status_code == 200
        return round(time.perf_counter() - start, 4)
    with ThreadPoolExecutor(max_workers=4) as executor:
        report['concurrent_read_seconds'] = list(executor.map(read, clients))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
