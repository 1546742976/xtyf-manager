"""Gunicorn-only synthetic fixture; never point this entry point at a real site."""
import os
from pathlib import Path

root = Path(os.environ["XTYF_RELIABILITY_ROOT"]).resolve()
if not (root / ".synthetic-only").is_file():
    raise RuntimeError("A newly created synthetic fixture directory is required")
for key, relative in {
    "PROGRESS_DB": "data.db", "FUNDS_DIR": "data/funds",
    "INVOICES_DIR": "data/invoices", "TASK_ATTACHMENTS_DIR": "data/task_attachments",
    "PROGRESS_SECRET_PATH": "secret.key", "MPLCONFIGDIR": "mpl",
}.items():
    os.environ[key] = str(root / relative)
os.environ["PROGRESS_INITIAL_ADMIN_PASSWORD"] = "synthetic-probe-password"

from app import app  # noqa: E402,F401
from modules import invoices  # noqa: E402

# Keep the genuine job stream open without executing invoice work or sending mail.
invoices.start_invoice_worker = lambda: None
