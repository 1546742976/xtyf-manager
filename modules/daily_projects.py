"""Independent goals with explicit group ownership and optimistic locking."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sqlite3

from flask import Blueprint, current_app, jsonify, redirect, request, send_file, url_for
from common import current_user, get_db, login_required, manager_required
from modules.image_storage import (prepare_image, stage_images, finalize_staged, cleanup_staged,
                                  attachment_path, ImageValidationError, StorageWriteError, MAX_FILES)

bp = Blueprint('daily_projects', __name__)
TZ = timezone(timedelta(hours=8))
STAGES = {'mechanical': '机械', 'electrical': '电控', 'vision': '视觉'}
STATUSES = ('未开始', '进行中', '已完成', '阻塞', '已取消')
TERMINAL = ('已完成', '已取消')


def now():
    return datetime.now(TZ)


def stamp():
    return now().strftime('%Y-%m-%d %H:%M:%S')


def manager(user):
    return user['role'] in ('admin', 'manager')


def failure(message, status=400):
    return jsonify(ok=False, error=message), status


def integer(value, label, minimum=1, maximum=2**63-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(label + '不合法')
    return value


def active_project(db, pid):
    return db.execute('SELECT * FROM daily_projects WHERE id=? AND deleted_at IS NULL', (pid,)).fetchone()


def describe_many(db, rows):
    """批量组装日常项目：固定 3 条查询，替代每个项目 9 条查询的 N+1。"""
    rows = list(rows)
    if not rows:
        return []
    ids = [int(row['id']) for row in rows]
    placeholders = ','.join('?' for _ in ids)
    groups: dict[int, dict[str, dict]] = {}
    for group in db.execute('SELECT * FROM daily_groups WHERE project_id IN (' + placeholders + ')', ids):
        groups.setdefault(int(group['project_id']), {})[group['stage']] = dict(group)
    owners: dict[tuple[int, str], list[dict]] = {}
    for owner in db.execute(
        'SELECT o.project_id,o.stage,m.id,m.name,m.is_active,m.deleted_at FROM daily_owners o '
        'JOIN members m ON m.id=o.member_id WHERE o.project_id IN (' + placeholders + ') ORDER BY m.id', ids
    ):
        owners.setdefault((int(owner['project_id']), owner['stage']), []).append(
            {key: owner[key] for key in ('id', 'name', 'is_active', 'deleted_at')})
    latest: dict[tuple[int, str], dict] = {}
    for update in db.execute(
        'SELECT project_id,stage,id,actor_name,progress,note,created_at FROM daily_updates '
        'WHERE project_id IN (' + placeholders + ') ORDER BY id DESC', ids
    ):
        latest.setdefault((int(update['project_id']), update['stage']),
                          {key: update[key] for key in ('id', 'actor_name', 'progress', 'note', 'created_at')})
    described = []
    for row in rows:
        project = dict(row)
        pid = int(row['id'])
        project_groups = []
        for stage, label in STAGES.items():
            group = groups.get(pid, {}).get(stage)
            if not group:
                continue
            item = dict(group)
            item['label'] = label
            item['owners'] = owners.get((pid, stage), [])
            item['latest'] = latest.get((pid, stage))
            project_groups.append(item)
        described.append(_finish_description(project, project_groups))
    return described


def describe(db, row):
    """单个项目描述：详情/保存路径复用批量实现。"""
    described = describe_many(db, [row])
    return described[0] if described else None


def _finish_description(project, groups):
    project['groups'] = groups
    enabled = [g for g in groups if g['enabled']]
    project['progress'] = float((Decimal(sum(g['progress'] for g in enabled)) / max(1, len(enabled))).quantize(Decimal('.1'), rounding=ROUND_HALF_UP))
    project['owner_ids'] = sorted({o['id'] for g in enabled for o in g['owners']})
    due_date = project.get('due_date')
    project['days_left'] = (datetime.strptime(due_date, '%Y-%m-%d').date() - now().date()).days if due_date else None
    return project


def reminder_rank(p):
    days = p['days_left']
    return (0 if days is not None and days < 0 else 1 if p['status'] == '阻塞' else
            2 if days is not None and 0 <= days <= 3 else 3, p['due_date'] or '9999-12-31', p['id'])


def dashboard_selection(projects, member_id):
    active = [p for p in projects if p['status'] not in TERMINAL]
    mine = sorted([p for p in active if member_id in p['owner_ids']], key=reminder_rank)
    alerts = sorted([p for p in active if reminder_rank(p)[0] < 3], key=reminder_rank)
    chosen = mine[:3]
    chosen += [p for p in alerts if p['id'] not in {x['id'] for x in chosen}][:3]
    for p in mine + alerts:
        if len(chosen) >= 6:
            break
        if p['id'] not in {x['id'] for x in chosen}:
            chosen.append(p)
    return chosen, len({p['id'] for p in mine + alerts})


@bp.get('/daily-projects')
@bp.get('/daily-projects/<int:pid>')
@login_required
def page(pid=None):
    return redirect(url_for('tasks', type='daily', **({'project': pid} if pid else {})))


@bp.get('/api/daily-projects')
@login_required
def listing():
    db, user = get_db(), current_user()
    if request.args.get('dashboard') == '1':
        rows = db.execute("SELECT * FROM daily_projects WHERE deleted_at IS NULL AND status NOT IN ('已完成','已取消')")
        items, total = dashboard_selection(describe_many(db, rows), user['id'])
        return jsonify(ok=True, items=items, total=total)
    try:
        page_number = integer(int(request.args.get('page', '1')), '页码', maximum=100000)
    except ValueError:
        return failure('页码不合法')
    clauses, args = ['p.deleted_at IS NULL'], []
    if request.args.get('q'):
        clauses.append('instr(lower(p.title), lower(?))>0')
        args.append(request.args['q'].strip())
    if request.args.get('status'):
        clauses.append('p.status=?'); args.append(request.args['status'])
    if request.args.get('mine') == '1':
        clauses.append('EXISTS(SELECT 1 FROM daily_owners o JOIN daily_groups g USING(project_id,stage) '
                       'WHERE o.project_id=p.id AND g.enabled=1 AND o.member_id=?)'); args.append(user['id'])
    where = ' AND '.join(clauses)
    total = db.execute('SELECT count(*) FROM daily_projects p WHERE ' + where, args).fetchone()[0]
    rows = db.execute('SELECT p.* FROM daily_projects p WHERE ' + where + ' ORDER BY p.id DESC LIMIT 20 OFFSET ?', [*args, (page_number-1)*20])
    return jsonify(ok=True, items=describe_many(db, rows), total=total, page=page_number, per_page=20)


@bp.get('/api/daily-projects/<int:pid>')
@login_required
def detail(pid):
    if pid > 2**63-1:
        return failure('项目不存在', 404)
    row = active_project(get_db(), pid)
    return jsonify(ok=True, project=describe(get_db(), row)) if row else failure('项目不存在', 404)


def clean_project(body, db):
    if not isinstance(body, dict):
        raise ValueError('请求格式不正确')
    title, detail = body.get('title'), body.get('detail', '')
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError('目标名称必填，最多 200 字')
    if not isinstance(detail, str) or len(detail) > 10000:
        raise ValueError('目标说明最多 10000 字')
    status, priority = body.get('status', '未开始'), body.get('priority', '中')
    if status not in STATUSES or priority not in ('高', '中', '低'):
        raise ValueError('状态或优先级不合法')
    dates = []
    for field in ('start_date', 'due_date'):
        value = body.get(field) or None
        if value is not None:
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError('日期格式应为 YYYY-MM-DD')
            datetime.strptime(value, '%Y-%m-%d')
        dates.append(value)
    if all(dates) and dates[0] > dates[1]:
        raise ValueError('开始日期不能晚于截止日期')
    groups = body.get('groups')
    if not isinstance(groups, dict) or not groups or groups.keys() - STAGES.keys():
        raise ValueError('至少启用一个有效组别')
    for owners in groups.values():
        if not isinstance(owners, list) or not owners:
            raise ValueError('每个启用组至少指定一位负责人')
        for mid in owners:
            integer(mid, '负责人 ID')
            member = db.execute('SELECT 1 FROM members WHERE id=? AND is_active=1 AND deleted_at IS NULL', (mid,)).fetchone()
            if not member:
                raise ValueError('负责人已停用或不存在，请重新选择')
    return (title.strip(), detail.strip(), status, priority, *dates), groups


@bp.post('/api/daily-projects')
@bp.post('/api/daily-projects/<int:pid>')
@manager_required
def save(pid=None):
    db = get_db()
    try:
        body = request.get_json(silent=True)
        db.execute('BEGIN IMMEDIATE')
        fields, groups = clean_project(body, db)
        when = stamp()
        if pid is not None:
            integer(pid, '项目 ID')
            old = active_project(db, pid)
            if not old:
                db.rollback(); return failure('项目不存在', 404)
            if integer(body.get('version'), '版本') != old['version']:
                db.rollback(); return failure('项目已修改，请重新加载后再保存', 409)
            # Closed goals must first be explicitly reopened.
            if old['status'] in TERMINAL and fields[2] in TERMINAL:
                db.rollback(); return failure('请先重新打开项目', 409)
            completed_at = (when if fields[2] == '已完成' else None) if fields[2] != old['status'] else old['completed_at']
            db.execute('UPDATE daily_projects SET title=?,detail=?,status=?,priority=?,start_date=?,due_date=?,completed_at=?,version=version+1,updated_at=? WHERE id=?', (*fields, completed_at, when, pid))
        else:
            completed_at = when if fields[2] == '已完成' else None
            pid = db.execute('INSERT INTO daily_projects(title,detail,status,priority,start_date,due_date,completed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', (*fields, completed_at, when, when)).lastrowid
        for stage in STAGES:
            old_group = db.execute('SELECT enabled FROM daily_groups WHERE project_id=? AND stage=?', (pid, stage)).fetchone()
            if stage in groups:
                db.execute('INSERT OR IGNORE INTO daily_groups(project_id,stage,enabled_at) VALUES(?,?,?)', (pid, stage, when))
                if old_group and not old_group['enabled']:
                    db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=? AND stage=?', (when, pid, stage))
                db.execute('UPDATE daily_groups SET enabled=1 WHERE project_id=? AND stage=?', (pid, stage))
                db.execute('DELETE FROM daily_owners WHERE project_id=? AND stage=?', (pid, stage))
                db.executemany('INSERT INTO daily_owners VALUES(?,?,?)', [(pid, stage, mid) for mid in set(groups[stage])])
            else:
                db.execute('UPDATE daily_groups SET enabled=0 WHERE project_id=? AND stage=?', (pid, stage))
        db.commit()
        return jsonify(ok=True, id=pid)
    except (ValueError, TypeError) as exc:
        db.rollback(); return failure(str(exc))
    except sqlite3.Error:
        db.rollback(); return failure('项目保存失败，请重试', 500)


@bp.post('/api/daily-projects/<int:pid>/delete')
@manager_required
def delete(pid):
    body = request.get_json(silent=True) or {}
    db = get_db()
    try:
        integer(pid, '项目 ID'); version = integer(body.get('version'), '版本')
        db.execute('BEGIN IMMEDIATE')
        changed = db.execute('UPDATE daily_projects SET deleted_at=?,version=version+1 WHERE id=? AND version=? AND deleted_at IS NULL', (stamp(), pid, version)).rowcount
        db.commit()
        return jsonify(ok=True) if changed else failure('项目已修改或删除，请刷新', 409)
    except (ValueError, AttributeError) as exc:
        db.rollback(); return failure(str(exc))


@bp.route('/api/daily-projects/<int:pid>/groups/<stage>/progress', methods=['GET', 'POST'])
@login_required
def progress(pid, stage):
    db, user = get_db(), current_user()
    if pid > 2**63-1 or stage not in STAGES:
        return failure('项目组不存在', 404)
    if request.method == 'GET':
        if not active_project(db, pid):
            return failure('项目不存在', 404)
        try:
            before = integer(int(request.args.get('before', str(2**63-1))), '游标')
        except ValueError:
            return failure('游标不合法')
        rows = [dict(r) for r in db.execute('SELECT * FROM daily_updates WHERE project_id=? AND stage=? AND id<? ORDER BY id DESC LIMIT 21', (pid, stage, before))]
        more = len(rows) > 20; rows = rows[:20]
        for row in rows:
            row['images'] = [dict(image) | {'url': f'/api/daily-projects/{pid}/attachments/{image["id"]}'} for image in db.execute('SELECT id,original_name FROM daily_attachments WHERE update_id=?', (row['id'],))]
        return jsonify(ok=True, updates=rows, next_before=rows[-1]['id'] if more else None)
    staged = []
    try:
        body = request.get_json(silent=True) if request.is_json else request.form.to_dict()
        if not isinstance(body, dict):
            raise ValueError('请求格式不正确')
        if not request.is_json:
            for key in ('progress', 'version', 'project_version'):
                value = body.get(key, '')
                if not value.isascii() or not value.isdecimal():
                    raise ValueError('进度和版本必须为整数')
                body[key] = int(value)
        percent = integer(body.get('progress'), '进度', 0, 100)
        version = integer(body.get('version'), '组版本')
        project_version = integer(body.get('project_version'), '项目版本')
        note = body.get('note', '')
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError('备注最多 2000 字')
        files = [f for f in request.files.getlist('files') if f.filename]
        if len(files) > MAX_FILES:
            raise ValueError('一次最多上传 5 张图片')
        db.execute('BEGIN IMMEDIATE')
        project = active_project(db, pid)
        group = db.execute('SELECT * FROM daily_groups WHERE project_id=? AND stage=?', (pid, stage)).fetchone()
        if not project or not group:
            db.rollback(); return failure('项目组不存在', 404)
        owner = db.execute('SELECT 1 FROM daily_owners WHERE project_id=? AND stage=? AND member_id=?', (pid, stage, user['id'])).fetchone()
        if not manager(user) and not owner:
            db.rollback(); return failure('只能更新自己负责的组', 403)
        if not group['enabled'] or project['status'] in TERMINAL:
            db.rollback(); return failure('项目或组别为只读状态', 409)
        if project['version'] != project_version or group['version'] != version:
            db.rollback(); return failure('分配或进度已变化，请重新加载并核对后提交', 409)
        if percent == group['progress'] and not note.strip() and not files:
            raise ValueError('请改变进度、填写备注或添加图片')
        images = [prepare_image(f) for f in files]
        staged = stage_images(Path(current_app.config['TASK_ATTACHMENTS_DIR']) / 'daily', images)
        uid = db.execute('INSERT INTO daily_updates(project_id,stage,actor_id,actor_name,progress,note,created_at) VALUES(?,?,?,?,?,?,?)', (pid, stage, user['id'], user['name'], percent, note.strip(), stamp())).lastrowid
        db.execute('UPDATE daily_groups SET progress=?,version=version+1 WHERE project_id=? AND stage=?', (percent, pid, stage))
        for image in images:
            db.execute('INSERT INTO daily_attachments(update_id,original_name,stored_name,mime_type) VALUES(?,?,?,?)', (uid, image.original_name, image.stored_name, image.mime_type))
        finalize_staged(staged)
        db.commit()
        return jsonify(ok=True, version=version+1)
    except (ValueError, ImageValidationError) as exc:
        db.rollback(); cleanup_staged(staged); return failure(str(exc))
    except (sqlite3.Error, OSError, StorageWriteError):
        db.rollback(); cleanup_staged(staged); return failure('进度保存失败，请重试', 500)


@bp.get('/api/daily-projects/<int:pid>/attachments/<int:aid>')
@login_required
def image(pid, aid):
    """日常项目图片对全体登录成员可读（项目对全员可见）；写入按负责人校验。"""
    if max(pid, aid) > 2**63-1:
        return failure('图片不存在', 404)
    db = get_db()
    row = db.execute('SELECT a.* FROM daily_attachments a JOIN daily_updates u ON u.id=a.update_id JOIN daily_projects p ON p.id=u.project_id WHERE a.id=? AND p.id=? AND p.deleted_at IS NULL', (aid, pid)).fetchone()
    if not row:
        return failure('图片不存在', 404)
    path = attachment_path(Path(current_app.config['TASK_ATTACHMENTS_DIR']) / 'daily', row['stored_name'])
    if not path.is_file():
        return failure('图片文件不可用', 404)
    response = send_file(path, mimetype=row['mime_type'], download_name=row['original_name'])
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
