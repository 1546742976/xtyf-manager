"""Management-only exception read model, independent of personal digest selection."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request

from common import ROLE_NAMES, current_user, get_db, manager_required
from modules.daily_projects import describe_many

bp = Blueprint('task_alerts', __name__)
TZ = timezone(timedelta(hours=8))
LABELS = {'blocked': '阻塞', 'overdue': '已逾期', 'unassigned': '缺少负责人', 'stale': '超过48小时未更新'}


def local_time(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def stale(progress, baseline, at):
    return progress < 100 and bool(baseline) and (at - local_time(baseline)).total_seconds() > 48 * 3600


def collect_alerts(db, at=None):
    at = at or datetime.now(TZ)
    at = at.replace(tzinfo=TZ) if at.tzinfo is None else at.astimezone(TZ)
    today = at.date().isoformat()
    members = {r['id']: dict(r) for r in db.execute('SELECT id,name,is_active,deleted_at FROM members')}
    valid = {mid for mid, m in members.items() if m['is_active'] and not m['deleted_at']}
    member_groups = {}
    for mid, gid in db.execute('SELECT member_id,group_id FROM member_groups'):
        member_groups.setdefault(mid, []).append(gid)
    result = []
    for row in db.execute("SELECT t.*,p.name AS vehicle_name,u.created_at AS last_progress "
                          "FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
                          "LEFT JOIN task_progress_updates u ON u.id=(SELECT max(id) FROM task_progress_updates WHERE task_id=t.id) "
                          "WHERE t.deleted_at IS NULL AND t.status NOT IN ('已完成','已取消')"):
        item = dict(row)
        flags = []
        if item['status'] == '阻塞': flags.append('blocked')
        if item['due_date'] and item['due_date'] < today: flags.append('overdue')
        if item['owner_id'] not in valid: flags.append('unassigned')
        if stale(item['progress'], item['last_progress'] or item['created_at'], at): flags.append('stale')
        if flags:
            item.update(type='task', flags=flags, group_ids=member_groups.get(item['owner_id'], []),
                        owner_ids=[item['owner_id']] if item['owner_id'] else [], affected_groups=[],
                        url=f'/tasks?task={item["id"]}')
            result.append(item)
    rows = db.execute("SELECT * FROM daily_projects WHERE deleted_at IS NULL AND status NOT IN ('已完成','已取消')")
    for project in describe_many(db, rows):
        flags, affected = [], []
        if project['status'] == '阻塞': flags.append('blocked')
        if project['due_date'] and project['due_date'] < today: flags.append('overdue')
        enabled = [g for g in project['groups'] if g['enabled']]
        for group in enabled:
            causes = []
            if not any(o['id'] in valid for o in group['owners']): causes.append('unassigned')
            baseline = max(filter(None, [group['enabled_at'], (group['latest'] or {}).get('created_at')]), key=local_time)
            if stale(group['progress'], baseline, at): causes.append('stale')
            if causes:
                affected.append({'stage': group['stage'], 'label': group['label'], 'flags': causes})
                flags.extend(causes)
        if flags:
            project.update(type='daily', flags=list(dict.fromkeys(flags)), affected_groups=affected,
                           project_id=None, vehicle_name=None, group_ids=[g['group_id'] for g in enabled if g.get('group_id')],
                           url=f'/tasks?type=daily&project={project["id"]}')
            result.append(project)
    for item in result:
        item['owner_names'] = [members[mid]['name'] for mid in item['owner_ids'] if mid in members]
        item['blocker_helper_name'] = members.get(item.get('blocker_helper_id'), {}).get('name', '')
    return result


def alert_page(db, args, at=None):
    page = int(args.get('page', '1'))
    if not 1 <= page <= 100000: raise ValueError('页码不合法')
    kind, category = args.get('type', ''), args.get('category', '')
    if kind not in ('', 'task', 'daily') or category not in ('', *LABELS): raise ValueError('筛选条件不合法')
    filters = {key: int(args[key]) for key in ('project_id', 'group_id', 'owner_id') if args.get(key)}
    if any(value < 1 for value in filters.values()): raise ValueError('筛选ID不合法')
    items = collect_alerts(db, at)
    items = [x for x in items if (not kind or x['type'] == kind)
             and ('project_id' not in filters or x['project_id'] == filters['project_id'])
             and ('group_id' not in filters or filters['group_id'] in x['group_ids'])
             and ('owner_id' not in filters or filters['owner_id'] in x['owner_ids'])]
    counts = Counter(flag for item in items for flag in item['flags'])
    if category: items = [x for x in items if category in x['flags']]
    priority = list(LABELS)
    items.sort(key=lambda x: (min(priority.index(f) for f in x['flags']), x['due_date'] or '9999-12-31', x['type'], x['id']))
    return dict(items=items[(page-1)*20:page*20], total=len(items), counts={k: counts[k] for k in LABELS}, page=page, per_page=20)


@bp.get('/task-alerts')
@manager_required
def page():
    return render_template('task_alerts.html', me=current_user(), page='tasks', role_names=ROLE_NAMES)


@bp.get('/api/task-alerts')
@manager_required
def listing():
    try:
        return jsonify(ok=True, **alert_page(get_db(), request.args))
    except (ValueError, OverflowError):
        return jsonify(ok=False, error='筛选条件或页码不合法'), 400
