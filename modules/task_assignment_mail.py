"""Assignment events share the task transaction and durable mail queue."""
import json
from datetime import datetime, timedelta, timezone
from html import escape

TZ = timezone(timedelta(hours=8))
AGGREGATION_SECONDS = 600


def executors(db, kind, item_id):
    roles = {}
    if kind == 'vehicle':
        row = db.execute('SELECT owner_id FROM tasks WHERE id=?', (item_id,)).fetchone()
        if row and row['owner_id']:
            roles[row['owner_id']] = ['负责人']
        for row in db.execute('SELECT member_id FROM task_participants WHERE task_id=?', (item_id,)):
            roles.setdefault(row[0], []).append('参与人')
    elif kind == 'daily':
        for row in db.execute("SELECT o.member_id,COALESCE(c.name,NULLIF(g.label_snapshot,''),g.stage) AS label FROM daily_owners o JOIN daily_groups g USING(project_id,stage) LEFT JOIN groups c ON c.id=g.group_id WHERE o.project_id=? AND g.enabled=1", (item_id,)):
            roles.setdefault(row['member_id'], []).append(row['label'] + '负责人')
    return roles


def enqueue_assignments(db, kind, item_id, actor, previous=()):
    table = 'tasks' if kind == 'vehicle' else 'daily_projects'
    item = db.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone()
    if item is None or item['status'] in ('已完成', '已取消') or item['deleted_at']:
        return
    at = datetime.now(TZ)
    for mid, roles in executors(db, kind, item_id).items():
        if mid in previous or mid == actor['id']:
            continue
        email = db.execute('SELECT e.* FROM member_email e JOIN members m ON m.id=e.member_id WHERE e.member_id=? AND m.is_active=1 AND m.deleted_at IS NULL AND e.verified=1 AND e.paused=0 AND e.assignment_enabled=1', (mid,)).fetchone()
        if email is None:
            continue
        payload = {'kind': kind, 'id': item_id, 'version': item['version'], 'actor': actor['name'],
                   'roles': roles, 'title': item['title']}
        key = f'assignment:{kind}:{item_id}:v{item["version"]}:member:{mid}'
        # The task write transaction serializes enqueue with the worker's claim.
        # Never append to sending/unknown/retrying jobs, nor move the first deadline.
        pending = db.execute("SELECT * FROM mail_jobs WHERE member_id=? AND kind='assignment' AND generation=? AND state='pending' AND attempts=0 AND created_at>? AND next_at=created_at+? ORDER BY id LIMIT 1",
                             (mid, email['generation'], at.timestamp()-AGGREGATION_SECONDS, AGGREGATION_SECONDS)).fetchone()
        payload['created_at'] = at.timestamp()
        if pending:
            batch = json.loads(pending['payload_json'])
            if key in batch['event_keys']:
                continue
            batch['event_keys'].append(key)
            batch['events'] = [event for event in batch['events'] if (event['kind'], event['id']) != (kind, item_id)] + [payload]
            db.execute('UPDATE mail_jobs SET payload_json=? WHERE id=?', (json.dumps(batch, ensure_ascii=False), pending['id']))
        else:
            db.execute('INSERT OR IGNORE INTO mail_jobs(member_id,kind,day,dedupe_key,generation,next_at,created_at,payload_json) VALUES(?,?,?,?,?,?,?,?)',
                       (mid, 'assignment', at.date().isoformat(), key, email['generation'], at.timestamp()+AGGREGATION_SECONDS, at.timestamp(), json.dumps({'events': [payload], 'event_keys': [key]}, ensure_ascii=False)))


def assignment_content(db, job, member, epoch, base_url):
    try:
        payload = json.loads(job['payload_json'])
        if not isinstance(payload, dict):
            raise ValueError()
    except (ValueError, TypeError):
        return None, '指派事件数据不可用'
    if 'events' not in payload:
        return _single_content(db, job, member, epoch, base_url)
    contents = []
    seen = set()
    for event in payload['events']:
        key = (event.get('kind'), event.get('id'))
        if key in seen:
            continue
        seen.add(key)
        content, _ = _single_content(db, dict(job, payload_json=json.dumps(event), created_at=event.get('created_at', job['created_at'])), member, epoch, base_url)
        if content:
            contents.append(content[0])
    if not contents:
        return None, '汇总中的指派均已失效或提醒已关闭'
    text = '任务指派汇总（10 分钟窗口）\n\n' + '\n\n'.join(contents)
    return (text, '<html><body><h2>任务指派汇总</h2><p>' + escape(text).replace('\n', '<br>') + '</p></body></html>'), None


def _single_content(db, job, member, epoch, base_url):
    """Return content or a skip reason, never contact GitHub or SMTP here."""
    if not member['assignment_enabled'] or epoch - job['created_at'] >= 86400:
        return None, '指派提醒已关闭或超过 24 小时'
    try:
        payload = json.loads(job['payload_json'])
        kind, item_id = payload['kind'], payload['id']
        if kind not in ('vehicle', 'daily') or type(item_id) is not int or not 0 < item_id <= 2**63-1:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        return None, '指派事件数据不可用'
    table = 'tasks' if kind == 'vehicle' else 'daily_projects'
    task = db.execute(f'SELECT * FROM {table} WHERE id=?', (item_id,)).fetchone()
    roles = executors(db, kind, item_id).get(job['member_id'])
    if not task or task['deleted_at'] or task['status'] in ('已完成', '已取消') or not roles:
        return None, '任务已结束或指派已撤销'
    url = base_url + (f'/tasks?task={item_id}' if kind == 'vehicle' else f'/tasks?type=daily&project={item_id}')
    text = (f'任务：{task["title"]}\n类型：{"造车任务" if kind == "vehicle" else "日常任务"}\n'
            f'职责：{"、".join(roles)}\n指派人：{payload.get("actor", "")}\n'
            f'计划：{task["start_date"] or "未设置"} 至 {task["due_date"] or "未设置"}\n{url}\n'
            '可在账户设置中关闭指派汇总邮件。')
    html = '<html><body><h2>任务指派</h2><p>' + escape(text).replace('\n', '<br>') + '</p><a href="' + escape(url, quote=True) + '">查看任务</a></body></html>'
    return (text, html), None
