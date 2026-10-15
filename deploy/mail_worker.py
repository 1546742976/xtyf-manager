"""One bounded queue pass. Start with the same environment as the web service."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true', help='Write private .eml previews, never send SMTP')
    args = parser.parse_args()
    from app import app
    from common import get_db
    from modules.mail_service import run_once, configured
    with app.app_context():
        if args.preview:
            app.config['MAIL_SETTINGS']['mode'] = 'preview'
        if not configured():
            print('Mail disabled or configuration incomplete; no messages processed.')
            return
        print(f'Processed {run_once(get_db())} mail jobs.')


if __name__ == '__main__':
    main()
