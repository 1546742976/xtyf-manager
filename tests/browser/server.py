"""Ephemeral loopback-only application for the review-fix browser smoke."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

with tempfile.TemporaryDirectory(prefix='xtyf-browser-review-') as root:
    for key, relative in {
        'PROGRESS_DB': 'data.db', 'FUNDS_DIR': 'funds', 'INVOICES_DIR': 'invoices',
        'TASK_ATTACHMENTS_DIR': 'attachments', 'PROGRESS_SECRET_PATH': 'secret.key',
        'MPLCONFIGDIR': 'matplotlib',
    }.items():
        os.environ[key] = str(Path(root) / relative)
    os.environ['PROGRESS_INITIAL_ADMIN_PASSWORD'] = '123456'
    from app import app
    from modules import invoices
    from werkzeug.serving import make_server
    # The smoke never runs background jobs; no unrelated daemon touches teardown.
    invoices.start_invoice_worker = lambda: None
    server = make_server('127.0.0.1', 0, app, threaded=True)
    print(f'BROWSER_URL=http://127.0.0.1:{server.server_port}', flush=True)
    print(f'BROWSER_ROOT={root}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
