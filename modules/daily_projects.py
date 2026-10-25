"""Independent goals with explicit group ownership and optimistic locking."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sqlite3
import json
from modules.input_validation import parse_int_text
from modules.task_assignment_mail import enqueue_assignments, executors
import re

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
    """Batch project groups, owners, progress and optional blocker helpers."""
    rows = list(rows)
    if not rows:
        return []
    ids = [int(row['id']) for row in rows]
    helper_ids = sorted({row['blocker_helper_id'] for row in rows if row['blocker_helper_id'] is not None})
    helpers = {row['id']: row['name'] for row in db.execute(
        'SELECT id,name FROM members WHERE id IN (' + ','.join('?' for _ in helper_ids) + ')', helper_ids)} if helper_ids else {}
    placeholders = ','.join('?' for _ in ids)
    groups: dict[int, dict[str, dict]] = {}
    for group in db.execute('SELECT d.*, COALESCE(c.name,NULLIF(d.label_snapshot,\'\'),d.stage) AS label, '
                            'CASE WHEN c.deleted_at IS NOT NULL THEN 1 ELSE 0 END AS catalog_archived '
                            'FROM daily_groups d LEFT JOIN groups c ON c.id=d.group_id '
                            'WHERE d.project_id IN (' + placeholders + ') ORDER BY d.rowid', ids):
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
        project['blocker_helper_name'] = helpers.get(project.get('blocker_helper_id'), '')
        pid = int(row['id'])
        project_groups = []
        for stage, group in groups.get(pid, {}).items():
            item = dict(group)
            item['owners'] = owners.get((pid, stage), [])
            item['latest'] = latest.get((pid, stage))
            project_groups.append(item)
        described.append(_finish_description(project, project_groups))
    return described


def describe(db, row):
    """单个项目描述：详情/保存路径复用批量实现。"""
    described = describe_many(db, [row])
    if not described:
        return None
    project = described[0]
    project['changes'] = [dict(event) for event in db.execute(
        'SELECT actor_name,action,note,before_json,after_json,created_at FROM daily_changes WHERE project_id=? ORDER BY id DESC LIMIT 50', (project['id'],))]
    project['blocker_events'] = [dict(event) for event in db.execute(
        'SELECT e.*,m.name AS helper_name FROM daily_blocker_events e LEFT JOIN members m ON m.id=e.helper_id WHERE e.project_id=? ORDER BY e.id DESC', (project['id'],))]
    return project


def _finish_description(project, groups):
    project['groups'] = groups
    enabled = [g for g in groups if g['enabled']]
    complete = bool(enabled) and all(g['progress'] == 100 for g in enabled)
    average = (Decimal(sum(g['progress'] for g in enabled)) / max(1, len(enabled))).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)
    project['progress'] = 100.0 if complete else float(min(average, Decimal('99.9')))
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


def clean_project(body, db, pid=None):
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
    if not isinstance(groups, dict) or not groups:
        raise ValueError('至少启用一个有效组别')
    if body.get('groups_mode') not in (None, 'replace'):
        raise ValueError('组别保存方式不合法')
    links = body.get('group_links', {})
    if not isinstance(links, dict) or any(stage not in groups for stage in links):
        raise ValueError('专业组关联不合法')
    metadata = {}
    existing = {r['stage']: r for r in db.execute('SELECT * FROM daily_groups WHERE project_id=?', (pid,))} if pid else {}
    for stage, owners in groups.items():
        previous = existing.get(stage)
        match = re.fullmatch(r'group-([1-9][0-9]*)', stage) if isinstance(stage, str) else None
        if stage not in STAGES and not previous and not match:
            raise ValueError('组别不存在')
        group_id = previous['group_id'] if previous else None
        if match:
            group_id = integer(int(match[1]), '专业组 ID')
        if stage in links:
            if stage not in STAGES:
                raise ValueError('只有历史组别需要关联专业组')
            chosen = integer(links[stage], '专业组 ID')
            if group_id is not None and group_id != chosen:
                raise ValueError('已关联的专业组不能替换，请停用后选择新组')
            group_id = chosen
        label = previous['label_snapshot'] if previous else STAGES.get(stage, '')
        if group_id is not None:
            catalog = db.execute('SELECT * FROM groups WHERE id=?', (group_id,)).fetchone()
            if not catalog:
                raise ValueError('专业组不存在')
            if catalog['deleted_at'] and not (previous and previous['enabled'] and previous['group_id'] == group_id):
                raise ValueError('已停用的专业组不能新增或重新启用')
            label = catalog['name']
            if any(other['stage'] != stage and other['group_id'] == group_id for other in existing.values()):
                raise ValueError('项目已包含该专业组，请使用已有组别')
            if any(value[0] == group_id for value in metadata.values()):
                raise ValueError('不能重复选择同一专业组')
        metadata[stage] = (group_id, label)
        if not isinstance(owners, list) or not owners:
            raise ValueError('每个启用组至少指定一位负责人')
        for mid in owners:
            integer(mid, '负责人 ID')
            member = db.execute('SELECT 1 FROM members WHERE id=? AND is_active=1 AND deleted_at IS NULL', (mid,)).fetchone()
            if not member:
                raise ValueError('负责人已停用或不存在，请重新选择')
    return (title.strip(), detail.strip(), status, priority, *dates), groups, metadata


def blocker_fields(body, db, previous=None):
    previous = dict(previous) if previous else {}
    values = []
    for key, label in (('blocker_reason', '阻塞原因'), ('blocker_next_action', '下一步')):
        value = body.get(key, previous.get(key, ''))
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(label + '必填，最多 2000 字')
        values.append(value.strip())
    helper = body.get('blocker_helper_id', previous.get('blocker_helper_id'))
    if helper is not None:
        integer(helper, '协助人 ID')
        if helper != previous.get('blocker_helper_id') and not db.execute('SELECT 1 FROM members WHERE id=? AND is_active=1 AND deleted_at IS NULL', (helper,)).fetchone():
            raise ValueError('协助人已停用或不存在')
    follow_up = body.get('blocker_follow_up', previous.get('blocker_follow_up')) or None
    if follow_up is not None:
        if not isinstance(follow_up, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', follow_up):
            raise ValueError('跟进日期格式应为 YYYY-MM-DD')
        datetime.strptime(follow_up, '%Y-%m-%d')
    return (*values, helper, follow_up)


def record_blocker(db, pid, action, values, resolution='', when=None):
    user = current_user()
    db.execute('INSERT INTO daily_blocker_events(project_id,actor_id,actor_name,action,reason,next_action,helper_id,follow_up,resolution,created_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,?)', (pid, user['id'], user['name'], action, *values, resolution, when or stamp()))


def close_blocker(db, project, resolution, when, action='resolve'):
    if project['status'] != '阻塞':
        return
    record_blocker(db, project['id'], action, tuple(project[key] for key in
                   ('blocker_reason', 'blocker_next_action', 'blocker_helper_id', 'blocker_follow_up')), resolution, when)
    db.execute("UPDATE daily_projects SET blocker_reason='',blocker_next_action='',blocker_helper_id=NULL,blocker_follow_up=NULL WHERE id=?", (project['id'],))


def group_progress(db, pid, stage, percent, note, when):
    user = current_user()
    db.execute('INSERT INTO daily_updates(project_id,stage,actor_id,actor_name,progress,note,created_at) VALUES(?,?,?,?,?,?,?)',
               (pid, stage, user['id'], user['name'], percent, note, when))
    db.execute('UPDATE daily_groups SET progress=?,version=version+1 WHERE project_id=? AND stage=?', (percent, pid, stage))


def effective_status(db, pid, requested):
    values = [r[0] for r in db.execute('SELECT progress FROM daily_groups WHERE project_id=? AND enabled=1', (pid,))]
    if requested == '已取消':
        if values and all(value == 100 for value in values):
            raise ValueError('全部启用组已达到 100%，不能保存为已取消；请核对实际进度后再取消')
        return requested
    if values and all(value == 100 for value in values):
        return '已完成'
    if requested == '未开始' and any(value > 0 for value in values):
        return '进行中'
    return requested


@bp.post('/api/daily-projects')
@bp.post('/api/daily-projects/<int:pid>')
@login_required
def save(pid=None):
    body = request.get_json(silent=True)
    if pid is not None and (not manager(current_user()) or isinstance(body, dict) and body.get('action') in ('cancel', 'reopen')):
        return save_personal(pid, body)
    db = get_db()
    try:
        body = request.get_json(silent=True)
        db.execute('BEGIN IMMEDIATE')
        fields, groups, metadata = clean_project(body, db, pid)
        if not isinstance(body.get('note', ''), str) or len(body.get('note', '')) > 2000:
            raise ValueError('修改原因最多 2000 字')
        when = stamp()
        old = None
        previous_executors = executors(db, 'daily', pid) if pid is not None else {}
        if pid is not None:
            integer(pid, '项目 ID')
            old = active_project(db, pid)
            if not old:
                db.rollback(); return failure('项目不存在', 404)
            if integer(body.get('version'), '版本') != old['version']:
                db.rollback(); return failure('项目已修改，请重新加载后再保存', 409)
            if 'created_by' in body:
                db.rollback(); return failure('创建者身份不能修改', 403)
            if old['status'] in TERMINAL and fields[2] in TERMINAL and not body.get('correction'):
                db.rollback(); return failure('请先重新打开项目', 409)
            if old['status'] in TERMINAL or fields[2] == '已取消' and old['status'] != '已取消':
                note = body.get('note', '')
                if not isinstance(note, str) or not note.strip() or len(note) > 2000:
                    raise ValueError('取消、重新打开或纠错必须填写原因，最多 2000 字')
            # Closed goals must first be explicitly reopened.
            if old['status'] in TERMINAL and fields[2] in TERMINAL and fields[2] != old['status']:
                db.rollback(); return failure('终态变更须先重开', 409)
            versions = body.get('group_versions')
            previous_groups = list(db.execute('SELECT stage,version,enabled FROM daily_groups WHERE project_id=?', (pid,)))
            previously_enabled = {group['stage'] for group in previous_groups if group['enabled']}
            selected = set(groups)
            if body.get('groups_mode') != 'replace':
                selected.update(stage for stage in previously_enabled if stage not in STAGES)
            if versions is None and (fields[2] == '已完成' or old['status'] in TERMINAL or selected != previously_enabled):
                raise ValueError('完成、重新打开或启停组时须提交组版本，请刷新项目或更新客户端')
            if versions is not None:
                if not isinstance(versions, dict):
                    raise ValueError('组版本不合法')
                for value in versions.values():
                    integer(value, '组版本')
                expected = {g['stage']: g['version'] for g in previous_groups}
                if versions != expected:
                    db.rollback(); return failure('组进度已变化，请重新加载并核对后提交', 409)
            db.execute('UPDATE daily_projects SET title=?,detail=?,status=?,priority=?,start_date=?,due_date=?,version=version+1,updated_at=? WHERE id=?', (*fields, when, pid))
        else:
            pid = db.execute('INSERT INTO daily_projects(title,detail,status,priority,start_date,due_date,created_at,updated_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)', (*fields, when, when, current_user()['id'])).lastrowid
        stages = set(groups)
        stages.update(r['stage'] for r in db.execute('SELECT stage FROM daily_groups WHERE project_id=?', (pid,))
                      if body.get('groups_mode') == 'replace' or r['stage'] in STAGES)
        for stage in sorted(stages, key=lambda key: (list(STAGES).index(key) if key in STAGES else 3, key)):
            old_group = db.execute('SELECT enabled FROM daily_groups WHERE project_id=? AND stage=?', (pid, stage)).fetchone()
            if stage in groups:
                db.execute('INSERT OR IGNORE INTO daily_groups(project_id,stage,enabled_at) VALUES(?,?,?)', (pid, stage, when))
                if old_group and not old_group['enabled']:
                    db.execute('UPDATE daily_groups SET enabled_at=? WHERE project_id=? AND stage=?', (when, pid, stage))
                db.execute('UPDATE daily_groups SET enabled=1,group_id=?,label_snapshot=? WHERE project_id=? AND stage=?', (*metadata[stage], pid, stage))
                db.execute('DELETE FROM daily_owners WHERE project_id=? AND stage=?', (pid, stage))
                db.executemany('INSERT INTO daily_owners VALUES(?,?,?)', [(pid, stage, mid) for mid in set(groups[stage])])
            else:
                db.execute('UPDATE daily_groups SET enabled=0 WHERE project_id=? AND stage=?', (pid, stage))
        reopening = old is not None and old['status'] in TERMINAL and fields[2] not in TERMINAL
        if reopening:
            reopen = body.get('reopen_progress')
            if not isinstance(reopen, dict) or not reopen:
                raise ValueError('重新打开时须填写至少一个启用组的实际进度（0–99）')
            for stage, percent in reopen.items():
                integer(percent, '重新打开进度', 0, 99)
                if not db.execute('SELECT 1 FROM daily_groups WHERE project_id=? AND stage=? AND enabled=1', (pid, stage)).fetchone():
                    raise ValueError('重新打开进度必须属于启用组')
                group_progress(db, pid, stage, percent, '重新打开项目，确认实际进度', when)
        elif body.get('reopen_progress'):
            raise ValueError('只有已结束项目需要填写重新打开进度')
        if fields[2] == '已完成' and not (old is not None and old['status'] == '已完成'):
            for group in db.execute('SELECT stage,progress FROM daily_groups WHERE project_id=? AND enabled=1', (pid,)).fetchall():
                if group['progress'] != 100:
                    group_progress(db, pid, group['stage'], 100, '手动完成项目，同步组进度', when)
        reconcile = old is None or fields[2] != old['status'] or selected != previously_enabled
        status = effective_status(db, pid, fields[2]) if reconcile else fields[2]
        if status == '阻塞':
            blocker_changed = old is None or any((body.get(key, old[key]) or None) != (old[key] or None) for key in
                ('blocker_reason', 'blocker_next_action', 'blocker_helper_id', 'blocker_follow_up'))
            if old is None or old['status'] != '阻塞' or blocker_changed:
                values = blocker_fields(body, db, old)
                db.execute('UPDATE daily_projects SET blocker_reason=?,blocker_next_action=?,blocker_helper_id=?,blocker_follow_up=? WHERE id=?', (*values, pid))
                record_blocker(db, pid, 'update' if old is not None and old['status'] == '阻塞' else 'open', values, when=when)
        elif old is not None and old['status'] == '阻塞':
            resolution = '全部启用组进度达到 100%，自动完成' if status == '已完成' else '项目已取消，结束阻塞跟进' if status == '已取消' else body.get('blocker_result', body.get('resolution', ''))
            if not isinstance(resolution, str) or not resolution.strip() or len(resolution) > 2000:
                raise ValueError('解除阻塞时请填写处理结果，最多 2000 字')
            close_blocker(db, old, resolution.strip(), when, 'cancel' if status == '已取消' else 'resolve')
            if status not in TERMINAL:
                status = '进行中'
        completed_at = (old['completed_at'] if old is not None and old['status'] == '已完成' else when) if status == '已完成' else None
        if old is not None and not reconcile:
            completed_at = old['completed_at']
        db.execute('UPDATE daily_projects SET status=?,completed_at=? WHERE id=?', (status, completed_at, pid))
        record_change(db, pid, 'create' if old is None else 'update', body.get('note', ''), dict(old) if old else {}, dict(active_project(db, pid)))
        enqueue_assignments(db, 'daily', pid, current_user(), previous_executors)
        db.commit()
        return jsonify(ok=True, id=pid, project=describe(db, active_project(db, pid)))
    except (ValueError, TypeError) as exc:
        db.rollback(); return failure(str(exc))
    except sqlite3.Error:
        db.rollback(); return failure('项目保存失败，请重试', 500)


def record_change(db, pid, action, note, before, after):
    user = current_user()
    db.execute('INSERT INTO daily_changes(project_id,actor_id,actor_name,action,note,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
               (pid, user['id'], user['name'], action, note, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), stamp()))


def save_personal(pid, body):
    """Limited creator edits and explicit executor lifecycle operations."""
    db, user = get_db(), current_user()
    try:
        integer(pid, '项目 ID')
        if not isinstance(body, dict):
            raise ValueError('请求格式不正确')
        db.execute('BEGIN IMMEDIATE')
        old = active_project(db, pid)
        if old is None:
            db.rollback(); return failure('项目不存在', 404)
        own = {r[0] for r in db.execute('SELECT o.stage FROM daily_owners o JOIN daily_groups g USING(project_id,stage) WHERE o.project_id=? AND o.member_id=? AND g.enabled=1', (pid, user['id']))}
        action = body.get('action')
        if action not in (None, 'cancel', 'reopen'):
            raise ValueError('操作不合法')
        if action is None:
            if old['created_by'] != user['id'] or set(body) - {'version', 'title', 'detail', 'start_date', 'due_date', 'note'}:
                db.rollback(); return failure('创建者只能修改标题、说明及计划日期', 403)
        elif not manager(user) and not own:
            db.rollback(); return failure('只有执行负责人可以取消或重开', 403)
        elif set(body) - {'version', 'action', 'note', 'group_versions', 'reopen_progress'}:
            db.rollback(); return failure('流程操作不能同时改动其他字段', 403)
        if integer(body.get('version'), '版本') != old['version']:
            db.rollback(); return failure('项目已修改，请重新核对', 409)
        note = body.get('note', '')
        if not isinstance(note, str) or len(note) > 2000 or action and not note.strip():
            raise ValueError('取消或重开原因必填，最多 2000 字')
        when = stamp()
        if action is None:
            if old['status'] in TERMINAL:
                db.rollback(); return failure('请先重新打开项目', 409)
            from modules.input_validation import clean_text
            from modules.task_validation import validate_dates
            title = clean_text(body.get('title', old['title']), '目标名称', 200, allow_newlines=False)
            detail = clean_text(body.get('detail', old['detail']), '目标说明', 10000, required=False)
            dates = [body.get(key, old[key]) or '' for key in ('start_date', 'due_date')]
            if any(not isinstance(value, str) for value in dates):
                raise ValueError('日期格式不合法')
            error = validate_dates(*dates)
            if error:
                raise ValueError(error)
            db.execute('UPDATE daily_projects SET title=?,detail=?,start_date=?,due_date=? WHERE id=?', (title, detail, *dates, pid))
        elif action == 'cancel':
            if old['status'] in TERMINAL:
                db.rollback(); return failure('已结束项目不能再次取消', 409)
            close_blocker(db, old, note.strip(), when, 'cancel')
            db.execute("UPDATE daily_projects SET status='已取消',completed_at=NULL WHERE id=?", (pid,))
        else:
            if old['status'] not in TERMINAL:
                db.rollback(); return failure('只有已结束项目可重开', 409)
            groups = {r['stage']: dict(r) for r in db.execute('SELECT * FROM daily_groups WHERE project_id=?', (pid,))}
            versions = body.get('group_versions')
            if not isinstance(versions, dict) or any(type(v) is not int for v in versions.values()) or versions != {key: g['version'] for key, g in groups.items()}:
                db.rollback(); return failure('组进度已变化，请重新核对', 409)
            progress = body.get('reopen_progress')
            if not isinstance(progress, dict) or not progress:
                raise ValueError('请填写至少一组实际进度（0–99）')
            for stage, percent in progress.items():
                integer(percent, '实际进度', 0, 99)
                if stage not in groups or not groups[stage]['enabled']:
                    raise ValueError('只能重开启用组')
                if not manager(user) and stage not in own:
                    db.rollback(); return failure('只能重开自己负责的组', 403)
                group_progress(db, pid, stage, percent, note.strip(), when)
            db.execute("UPDATE daily_projects SET status='进行中',completed_at=NULL WHERE id=?", (pid,))
        db.execute('UPDATE daily_projects SET version=version+1,updated_at=? WHERE id=?', (when, pid))
        record_change(db, pid, action or 'plan', note.strip(), dict(old), dict(active_project(db, pid)))
        db.commit()
        return jsonify(ok=True, id=pid, project=describe(db, active_project(db, pid)))
    except (TypeError, ValueError) as exc:
        db.rollback(); return failure(str(exc))
    except sqlite3.Error:
        db.rollback(); return failure('项目保存失败，请重试', 500)


@bp.route('/api/daily-projects/<int:pid>/blocker', methods=['GET', 'POST'])
@login_required
def blocker(pid):
    db, user = get_db(), current_user()
    if pid > 2**63-1:
        return failure('项目不存在', 404)
    if request.method == 'GET':
        project = active_project(db, pid)
        if project is None:
            return failure('项目不存在', 404)
        return jsonify(ok=True, blocker_events=describe(db, project)['blocker_events'])
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('请求格式不正确')
        blocked = body.get('blocked')
        if 'blocker_action' in body:
            if body['blocker_action'] not in ('open', 'update', 'resolve'):
                raise ValueError('阻塞操作不合法')
            blocked = body['blocker_action'] != 'resolve'
        if type(blocked) is not bool:
            raise ValueError('请指定阻塞操作')
        version = integer(body.get('version'), '项目版本')
        db.execute('BEGIN IMMEDIATE')
        project = active_project(db, pid)
        if project is None:
            db.rollback(); return failure('项目不存在', 404)
        owner = db.execute('SELECT 1 FROM daily_owners o JOIN daily_groups g USING(project_id,stage) '
                           'WHERE o.project_id=? AND o.member_id=? AND g.enabled=1', (pid, user['id'])).fetchone()
        if not manager(user) and not owner:
            db.rollback(); return failure('只能维护自己负责的启用组所属项目的阻塞', 403)
        if project['status'] in TERMINAL or project['version'] != version:
            db.rollback(); return failure('项目已结束或版本已变化，请刷新后重试', 409)
        when = stamp()
        if blocked:
            values = blocker_fields(body, db, project)
            db.execute("UPDATE daily_projects SET status='阻塞',blocker_reason=?,blocker_next_action=?,blocker_helper_id=?,blocker_follow_up=?,version=version+1,updated_at=? WHERE id=?", (*values, when, pid))
            record_blocker(db, pid, 'update' if project['status'] == '阻塞' else 'open', values, when=when)
        else:
            if project['status'] != '阻塞':
                db.rollback(); return failure('项目当前没有阻塞', 409)
            result = body.get('blocker_result', body.get('resolution', ''))
            if not isinstance(result, str) or not result.strip() or len(result) > 2000:
                raise ValueError('解除阻塞时请填写处理结果，最多 2000 字')
            close_blocker(db, project, result.strip(), when)
            db.execute("UPDATE daily_projects SET status='进行中',version=version+1,updated_at=? WHERE id=?", (when, pid))
        db.commit()
        return jsonify(ok=True, project=describe(db, active_project(db, pid)))
    except (ValueError, TypeError) as exc:
        db.rollback(); return failure(str(exc))
    except sqlite3.Error:
        db.rollback(); return failure('阻塞保存失败，请重试', 500)


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
    if pid > 2**63-1:
        return failure('项目组不存在', 404)
    if request.method == 'GET':
        if not active_project(db, pid):
            return failure('项目不存在', 404)
        if not db.execute('SELECT 1 FROM daily_groups WHERE project_id=? AND stage=?', (pid, stage)).fetchone():
            return failure('项目组不存在', 404)
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
                body[key] = parse_int_text(value, '进度和版本')
        percent = integer(body.get('progress'), '进度', 0, 100)
        version = integer(body.get('version'), '组版本')
        project_version = integer(body.get('project_version'), '项目版本')
        note = body.get('note', '')
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError('备注最多 2000 字')
        files = [f for f in request.files.getlist('files') if f.filename]
        if len(files) > MAX_FILES:
            raise ValueError('一次最多上传 5 张图片')
        images = [prepare_image(f) for f in files]
        staged = stage_images(Path(current_app.config['TASK_ATTACHMENTS_DIR']) / 'daily', images)
        db.execute('BEGIN IMMEDIATE')
        user = current_user()
        if user is None:
            db.rollback(); cleanup_staged(staged); return failure('登录已失效', 401)
        project = active_project(db, pid)
        group = db.execute('SELECT * FROM daily_groups WHERE project_id=? AND stage=?', (pid, stage)).fetchone()
        if not project or not group:
            db.rollback(); cleanup_staged(staged); return failure('项目组不存在', 404)
        owner = db.execute('SELECT 1 FROM daily_owners WHERE project_id=? AND stage=? AND member_id=?', (pid, stage, user['id'])).fetchone()
        if not manager(user) and not owner:
            db.rollback(); cleanup_staged(staged); return failure('只能更新自己负责的组', 403)
        if not group['enabled'] or project['status'] in TERMINAL:
            db.rollback(); cleanup_staged(staged); return failure('项目或组别为只读状态', 409)
        if project['version'] != project_version or group['version'] != version:
            db.rollback(); cleanup_staged(staged); return failure('分配或进度已变化，请重新加载并核对后提交', 409)
        if percent == group['progress'] and not note.strip() and not files:
            raise ValueError('请改变进度、填写备注或添加图片')
        uid = db.execute('INSERT INTO daily_updates(project_id,stage,actor_id,actor_name,progress,note,created_at) VALUES(?,?,?,?,?,?,?)', (pid, stage, user['id'], user['name'], percent, note.strip(), stamp())).lastrowid
        db.execute('UPDATE daily_groups SET progress=?,version=version+1 WHERE project_id=? AND stage=?', (percent, pid, stage))
        when = stamp()
        status = effective_status(db, pid, project['status']) if percent != group['progress'] else project['status']
        if status == '已完成':
            close_blocker(db, project, '全部启用组进度达到 100%，自动完成', when)
        status_changed = status != project['status']
        completed_at = project['completed_at'] if percent == group['progress'] else when if status == '已完成' else None
        db.execute('UPDATE daily_projects SET status=?,completed_at=?,version=version+?,updated_at=? WHERE id=?',
                   (status, completed_at, int(status_changed), when, pid))
        for image in images:
            db.execute('INSERT INTO daily_attachments(update_id,original_name,stored_name,mime_type) VALUES(?,?,?,?)', (uid, image.original_name, image.stored_name, image.mime_type))
        finalize_staged(staged)
        db.commit()
        return jsonify(ok=True, version=version+1, project_version=project_version+int(status_changed),
                       project=describe(db, active_project(db, pid)))
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
