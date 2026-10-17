"""Isolated loopback demo for silver/dark timeline and settings visual QA."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

with tempfile.TemporaryDirectory(prefix='xtyf-theme-demo-') as root:
    for key, relative in {
        'PROGRESS_DB':'data.db', 'FUNDS_DIR':'funds', 'INVOICES_DIR':'invoices',
        'TASK_ATTACHMENTS_DIR':'attachments', 'PROGRESS_SECRET_PATH':'secret.key',
        'MPLCONFIGDIR':'matplotlib',
    }.items():
        os.environ[key] = str(Path(root) / relative)
    os.environ['PROGRESS_INITIAL_ADMIN_PASSWORD'] = '123456'
    from app import app
    from modules import invoices
    from werkzeug.serving import make_server
    app.jinja_env.auto_reload = True
    invoices.start_invoice_worker = lambda: None
    # This disposable visual fixture uses the shared manual-test admin password.
    # Password-change flows are covered by server.py and dedicated member tests.
    with app.app_context():
        from common import get_db
        db = get_db()
        db.execute("UPDATE members SET must_change_password=0 WHERE username='admin'")
        db.commit()
    client = app.test_client()

    def post(url, body):
        token = client.get('/api/csrf').get_json()['csrf_token']
        response = client.post(url, json=body, headers={'X-CSRF-Token':token})
        if response.status_code != 200:
            raise RuntimeError((url, response.get_json()))
        return response.get_json()

    post('/login', {'username':'admin','password':'123456'})
    groups = {row['name']:row['id'] for row in client.get('/api/groups').get_json()['groups']}
    project = next(row['id'] for row in client.get('/api/projects').get_json()['projects'] if row['name']=='哨兵')
    for stage, name, tasks in (
        ('机械','机械负责人', [('底盘结构设计','2026-10-14','2026-10-22','未开始'),('云台结构装配','2026-10-14','2026-10-22','进行中'),('机械设计评审','2026-10-22','2026-10-22','未开始')]),
        ('电控','电控负责人', [('底盘控制调试','2026-10-22','2026-10-24','进行中'),('云台控制联调','2026-10-22','2026-10-26','阻塞')]),
        ('视觉','视觉负责人', [('目标识别优化','2026-10-26','2026-10-28','未开始'),('联合验收','2026-10-26','2026-10-29','未开始')]),
    ):
        member = post('/api/member', {'name':name,'username':stage,'password':'demo-member-password','group_ids':[groups[stage]],'project_ids':[project]})['id']
        for title, start, end, status in tasks:
            post('/api/task', {'title':title,'owner_id':member,'project_id':project,'start_date':start,'due_date':end,'status':status})
    post('/api/task', {'title':'待分配任务','project_id':project})
    post('/api/account/preferences', {'theme': os.environ.get('THEME_DEMO_THEME', 'light')})
    server = make_server('127.0.0.1', int(os.environ.get('THEME_DEMO_PORT', '0')), app, threaded=True)
    print(f'THEME_DEMO_URL=http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
