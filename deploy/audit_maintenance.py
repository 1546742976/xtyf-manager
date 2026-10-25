"""Preview by default. Explicit apply requires a new SQLite backup; no startup cleanup."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.audit_retention import cleanup_audit_rows, migrate_classification  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup')
    parser.add_argument('--reset-classification', action='store_true', help='保留记录，将所有分类恢复为保守关键类')
    args = parser.parse_args()
    path = Path(args.database).resolve(strict=True)
    if args.apply and (not args.backup or Path(args.backup).exists()):
        parser.error('--apply 需要一个尚不存在的 --backup 文件路径')
    with closing(sqlite3.connect(path)) as db:
        if args.apply:
            with closing(sqlite3.connect(args.backup)) as backup:
                db.backup(backup)
        db.execute('BEGIN IMMEDIATE')
        try:
            migrate_classification(db)
            if args.reset_classification:
                db.execute("UPDATE security_audit SET level='critical'")
            report = cleanup_audit_rows(db, confirmed=args.apply and not args.reset_classification)
            print(json.dumps({k: v for k, v in report.items() if k != 'ids'}, ensure_ascii=False))
            if args.apply:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise


if __name__ == '__main__':
    main()
