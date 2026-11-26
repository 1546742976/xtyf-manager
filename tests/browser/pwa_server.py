"""Loopback-only PWA deployment fixture; never imports production data."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

with tempfile.TemporaryDirectory(prefix='xtyf-pwa-browser-') as directory:
    root = Path(directory)
    for key, name in {
        'PROGRESS_DB': 'data.db', 'FUNDS_DIR': 'funds', 'INVOICES_DIR': 'invoices',
        'TASK_ATTACHMENTS_DIR': 'attachments', 'PROGRESS_SECRET_PATH': 'secret.key', 'MPLCONFIGDIR': 'matplotlib',
    }.items():
        os.environ[key] = str(root / name)
    os.environ['PROGRESS_INITIAL_ADMIN_PASSWORD'] = 'pwa-browser-initial'
    os.environ['PROGRESS_PWA_ENABLED'] = '1'
    os.environ['PROGRESS_COOKIE_SECURE'] = '0'
    from app import app
    from flask import jsonify, send_from_directory
    from modules import invoices
    from modules.pwa import PUBLIC_FILES, build_config
    from werkzeug.serving import make_server
    invoices.start_invoice_worker = lambda: None
    client = app.test_client()

    def post(path, data):
        token = client.get('/api/csrf').get_json()['csrf_token']
        response = client.post(path, json=data, headers={'X-CSRF-Token': token})
        if response.status_code != 200:
            raise RuntimeError((path, response.get_json()))
        return response.get_json()

    for name in (*PUBLIC_FILES, 'pwa/sw.js'):
        target = root / 'static' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(app.static_folder) / name, target)
    original_static = app.view_functions['static']

    def static(filename):
        if filename in PUBLIC_FILES:
            return send_from_directory(root / 'static', filename)
        return original_static(filename=filename)
    app.view_functions['static'] = static

    @app.post('/__pwa_fixture__/release')
    def next_release():
        # Simulate a new HEAD deployment without touching the checkout or its DB.
        with (root / 'static/pwa.css').open('ab') as stream:
            stream.write(b'\n/* next isolated deployment */\n')
        config, source = build_config(root)
        app.extensions['pwa'] = config
        app.extensions['pwa_worker'] = source.replace('__PWA_CONFIG__', json.dumps(config, sort_keys=True))
        return jsonify(ok=True, version=config['version'])

    @app.post('/__pwa_fixture__/retire')
    def retire():
        app.config['PWA_ENABLED'] = False
        return jsonify(ok=True)

    @app.get('/__pwa_fixture__/data')
    def fixture_data():
        return jsonify(ok=True, task_id=task_id, units=units)

    post('/login', {'username': 'admin', 'password': 'pwa-browser-initial'})
    post('/api/password', {'old': 'pwa-browser-initial', 'new': 'pwa-browser-password'})
    post('/api/member', {'name': 'PWA synthetic member B', 'username': 'pwa_member_b', 'password': 'pwa-member-initial'})
    task_id = post('/api/task', {'title': 'PWA-PRIVATE-TASK-SENTINEL'})['id']
    from uuid import uuid4
    campus = post('/api/assets/catalog/campuses', {'name': 'PWA campus', 'request_id': uuid4().hex})['id']
    item = post('/api/assets/catalog/items', {'name': 'PWA label fixture', 'kind': 'returnable',
                                            'unit': '台', 'request_id': uuid4().hex})['id']
    post('/api/assets/units/receive', {'item_id': item, 'campus_id': campus, 'quantity': 1,
                                     'reason': 'PWA isolated test', 'request_id': uuid4().hex})
    units = client.get('/api/assets/units?item_id=' + str(item)).get_json()['units']

    server = make_server('127.0.0.1', 0, app, threaded=True)
    print(f'PWA_URL=http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
