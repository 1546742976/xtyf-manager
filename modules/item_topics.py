"""Versioned links to existing discussions; message text stays in its source table."""
import json
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from common import current_user, get_db, login_required

bp = Blueprint('item_topics', __name__)
TABLES = {'task': ('tasks', 'task_topics'), 'daily': ('daily_projects', 'daily_project_topics')}


def migrate(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        for parent, relation in TABLES.values():
            db.execute(f'''CREATE TABLE IF NOT EXISTS {relation} (
              item_id INTEGER NOT NULL REFERENCES {parent}(id),
              topic_id INTEGER NOT NULL REFERENCES collaboration_topics(id),
              conclusion_id INTEGER REFERENCES collaboration_messages(id),
              PRIMARY KEY(item_id,topic_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS item_topic_events (
          id INTEGER PRIMARY KEY, item_type TEXT NOT NULL, item_id INTEGER NOT NULL,
          topic_id INTEGER NOT NULL REFERENCES collaboration_topics(id), actor_id INTEGER NOT NULL REFERENCES members(id),
          created_at TEXT NOT NULL, action TEXT NOT NULL, before_json TEXT, after_json TEXT)''')
        db.commit()
    except Exception:
        db.rollback()
        raise


def can_edit(db, kind, item, user):
    if user['role'] in ('admin', 'manager'): return True
    if kind == 'task':
        return item['owner_id'] == user['id'] or bool(db.execute(
            'SELECT 1 FROM task_participants WHERE task_id=? AND member_id=?', (item['id'], user['id'])).fetchone())
    return bool(db.execute('SELECT 1 FROM daily_owners o JOIN daily_groups g USING(project_id,stage) '
                           'WHERE o.project_id=? AND o.member_id=? AND g.enabled=1', (item['id'], user['id'])).fetchone())


def links(db, kind, item_id):
    table = TABLES[kind][1]
    rows = db.execute(f'''SELECT l.topic_id,l.conclusion_id,
      CASE WHEN t.withdrawn_at IS NULL THEN t.title ELSE '话题已撤回' END AS title,
      t.withdrawn_at IS NOT NULL AS withdrawn,
      CASE WHEN t.withdrawn_at IS NULL AND m.withdrawn_at IS NULL THEN m.body ELSE NULL END AS conclusion,
      CASE WHEN l.conclusion_id IS NOT NULL AND (t.withdrawn_at IS NOT NULL OR m.withdrawn_at IS NOT NULL)
        THEN 1 ELSE 0 END AS conclusion_withdrawn
      FROM {table} l JOIN collaboration_topics t ON t.id=l.topic_id
      LEFT JOIN collaboration_messages m ON m.id=l.conclusion_id WHERE l.item_id=? ORDER BY l.topic_id''', (item_id,))
    return [dict(r) | {'url': f'/discussions?topic={r["topic_id"]}'} for r in rows]


def positive(value):
    if type(value) is not int or not 1 <= value <= 2**63-1: raise ValueError('ID或版本必须为正整数')
    return value


@bp.route('/api/task/<int:item_id>/topics', methods=['GET', 'POST'], defaults={'kind': 'task'})
@bp.route('/api/daily-projects/<int:item_id>/topics', methods=['GET', 'POST'], defaults={'kind': 'daily'})
@login_required
def item_links(kind, item_id):
    db, user = get_db(), current_user()
    parent, relation = TABLES[kind]
    try:
        positive(item_id)
        db.execute('BEGIN IMMEDIATE' if request.method == 'POST' else 'BEGIN')
        item = db.execute(f'SELECT * FROM {parent} WHERE id=? AND deleted_at IS NULL', (item_id,)).fetchone()
        if item is None:
            db.rollback(); return jsonify(ok=False, error='事项不存在'), 404
        editable = can_edit(db, kind, item, user)
        version = item['version']
        if request.method == 'POST':
            if not editable:
                db.rollback(); return jsonify(ok=False, error='无权限'), 403
            body = request.get_json(silent=True)
            if not isinstance(body, dict): raise ValueError('请求格式不正确')
            expected, topic_id = positive(body.get('version')), positive(body.get('topic_id'))
            if expected != version:
                db.rollback(); return jsonify(ok=False, error='事项已被修改，请刷新后重试'), 409
            action = body.get('action', 'link')
            if action not in ('link', 'unlink', 'conclusion'): raise ValueError('关联操作不合法')
            topic = db.execute('SELECT * FROM collaboration_topics WHERE id=?', (topic_id,)).fetchone()
            if topic is None or (topic['withdrawn_at'] and action != 'unlink'): raise ValueError('话题不存在或已撤回')
            old = db.execute(f'SELECT * FROM {relation} WHERE item_id=? AND topic_id=?', (item_id, topic_id)).fetchone()
            if action == 'link':
                db.execute(f'INSERT OR IGNORE INTO {relation}(item_id,topic_id) VALUES(?,?)', (item_id, topic_id))
            elif action == 'unlink':
                db.execute(f'DELETE FROM {relation} WHERE item_id=? AND topic_id=?', (item_id, topic_id))
            else:
                if old is None: raise ValueError('请先关联话题')
                conclusion = body.get('conclusion_id')
                if conclusion is not None:
                    positive(conclusion)
                    if not db.execute('SELECT 1 FROM collaboration_messages WHERE id=? AND topic_id=? AND withdrawn_at IS NULL',
                                      (conclusion, topic_id)).fetchone(): raise ValueError('结论必须是此话题中未撤回的消息')
                db.execute(f'UPDATE {relation} SET conclusion_id=? WHERE item_id=? AND topic_id=?', (conclusion, item_id, topic_id))
            new = db.execute(f'SELECT * FROM {relation} WHERE item_id=? AND topic_id=?', (item_id, topic_id)).fetchone()
            before, after = dict(old) if old else None, dict(new) if new else None
            if before != after:
                version += 1
                db.execute(f'UPDATE {parent} SET version=? WHERE id=?', (version, item_id))
                db.execute('INSERT INTO item_topic_events(item_type,item_id,topic_id,actor_id,created_at,action,before_json,after_json) '
                           'VALUES(?,?,?,?,?,?,?,?)', (kind, item_id, topic_id, user['id'],
                           datetime.now(timezone(timedelta(hours=8))).isoformat(), action,
                           json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
        result = links(db, kind, item_id)
        db.commit()
        return jsonify(ok=True, items=result, version=version, can_edit=editable)
    except ValueError as error:
        db.rollback(); return jsonify(ok=False, error=str(error)), 400
    except Exception:
        db.rollback(); raise


@bp.get('/discussions/api/topics/<int:topic_id>/items')
@login_required
def backlinks(topic_id):
    db = get_db()
    if not 1 <= topic_id <= 2**63-1: return jsonify(ok=False, error='话题不存在'), 404
    result = []
    for kind, (parent, relation) in TABLES.items():
        for row in db.execute(f'SELECT p.id,p.title,l.conclusion_id FROM {relation} l JOIN {parent} p ON p.id=l.item_id '
                              'WHERE l.topic_id=? AND p.deleted_at IS NULL ORDER BY p.id', (topic_id,)):
            result.append(dict(row) | {'type': kind, 'url': f'/tasks?task={row["id"]}' if kind == 'task' else f'/tasks?type=daily&project={row["id"]}'})
    return jsonify(ok=True, items=result)
