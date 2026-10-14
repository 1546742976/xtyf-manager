#!/usr/bin/env python3
"""Read-only diagnostics for legacy invoice fields and missing catalog relations.

Never imports the Flask application, migrates a database, or rewrites metadata.
"""
import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.invoice_validation import validate_fields


def audit(root, *, db_path=None, invoice_dir=None):
    root = Path(root).resolve()
    database = Path(db_path or os.environ.get('PROGRESS_DB', root / 'data.db')).resolve()
    directory = Path(invoice_dir or os.environ.get('INVOICES_DIR', root / 'data/invoices')).resolve()
    issues = []
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        members = {row[0] for row in db.execute('SELECT id FROM members')}
        for member_id, in db.execute("SELECT m.id FROM members m WHERE TRIM(COALESCE(m.group_name,''))<>'' "
                                    'AND NOT EXISTS(SELECT 1 FROM member_groups x WHERE x.member_id=m.id)'):
            issues.append({'kind': 'missing_member_groups', 'member_id': member_id})
        for task_id, in db.execute("SELECT id FROM tasks WHERE TRIM(COALESCE(project,''))<>'' AND project_id IS NULL"):
            issues.append({'kind': 'missing_task_project', 'task_id': task_id})
    path = directory / '.invoice_meta.json'
    if path.exists():
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(meta, dict):
                raise ValueError('元数据根节点不是对象')
        except (ValueError, OSError) as exc:
            issues.append({'kind': 'invalid_metadata_file', 'error': str(exc)})
            meta = {}
        for key, entry in meta.items():
            relative = key.replace('\\', '/')
            target = (directory / relative).resolve()
            if not target.is_relative_to(directory) or not target.is_file():
                issues.append({'kind': 'orphan_invoice_metadata', 'path': relative})
            if error := validate_fields(entry):
                issues.append({'kind': 'invalid_invoice_fields', 'path': relative, 'error': error})
            if isinstance(entry, dict):
                cents = entry.get('confirmed_amount_cents')
                if cents is not None and (type(cents) is not int or not 0 <= cents <= 2**63 - 1):
                    issues.append({'kind': 'invalid_confirmed_amount', 'path': relative})
                owner = entry.get('submitter_id')
                if owner not in (None, 0) and (type(owner) is not int or owner not in members):
                    issues.append({'kind': 'invalid_invoice_owner', 'path': relative})
    return {'read_only': True, 'issue_count': len(issues), 'issues': issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, required=True)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--invoice-dir', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.app_dir, db_path=args.database, invoice_dir=args.invoice_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
