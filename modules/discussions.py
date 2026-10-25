"""Authenticated local team topics, replies, unread positions and private files."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import time as epoch_now

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from common import ROLE_NAMES, current_user, get_db, login_required
from modules.input_validation import clean_text, parse_int_text
from modules import collaboration_storage
from modules.collaboration_storage import (
    UploadError, attachment_path, cleanup_staged, finalize_staged, stage_uploads,
)

discussions_bp = Blueprint('discussions', __name__)
TZ = timezone(timedelta(hours=8))
MAX_ID = 2**63 - 1
TOPIC_PAGE_SIZE = 20
MESSAGE_PAGE_SIZE = 50
MANAGEMENT_ROLES = ('admin', 'manager')
SUBMISSION_ID = re.compile(r'[A-Za-z0-9_-]{8,80}')


def fail(message, status=400):
    return jsonify(ok=False, error=message), status


def stamp(epoch):
    return datetime.fromtimestamp(epoch, TZ).isoformat(timespec='microseconds')


def root():
    return Path(current_app.config['TASK_ATTACHMENTS_DIR']) / 'collaboration'


def integer(value, label, minimum=1, maximum=MAX_ID):
    try:
        value = parse_int_text(value, label)
    except ValueError as exc:
        raise UploadError(str(exc)) from exc
    if not minimum <= value <= maximum:
        raise UploadError(label + '不合法')
    return value


@contextmanager
def snapshot(db):
    # Cursor and content share one snapshot; concurrent withdrawals are either
    # reflected already or delivered by the next changes request.
    db.execute('BEGIN')
    try:
        yield
        db.commit()
    except Exception:
        db.rollback()
        raise


def topic_rows(db, ids, uid):
    """一次取回整页话题（含已读位置与未读数），避免每个话题单独查询。"""
    values = [int(value) for value in ids]
    if not values:
        return []
    placeholders = ','.join('?' for _ in values)
    return db.execute('''SELECT t.*,
      COALESCE(r.message_id, 0) AS read_message_id,
      (SELECT count(*) FROM collaboration_messages m WHERE m.topic_id=t.id) AS message_count,
      (SELECT count(*) FROM collaboration_messages m WHERE m.topic_id=t.id AND m.withdrawn_at IS NULL
       AND m.author_id<>? AND m.id>COALESCE(r.message_id,0)) AS unread_count
      FROM collaboration_topics t
      LEFT JOIN collaboration_reads r ON r.topic_id=t.id AND r.member_id=?
      WHERE t.id IN (''' + placeholders + ''')
      ORDER BY t.last_activity_at DESC, t.id DESC''', (uid, uid, *values)).fetchall()


def topic_row(db, tid, uid):
    integer(tid, '话题 ID')
    return db.execute('''SELECT t.*,
      COALESCE((SELECT message_id FROM collaboration_reads r WHERE r.topic_id=t.id AND r.member_id=?),0) AS read_message_id,
      (SELECT count(*) FROM collaboration_messages m WHERE m.topic_id=t.id) AS message_count,
      (SELECT count(*) FROM collaboration_messages m WHERE m.topic_id=t.id AND m.withdrawn_at IS NULL
       AND m.author_id<>? AND m.id>COALESCE((SELECT message_id FROM collaboration_reads r
         WHERE r.topic_id=t.id AND r.member_id=?),0)) AS unread_count
      FROM collaboration_topics t WHERE t.id=?''', (uid, uid, uid, tid)).fetchone()


def can_withdraw(row, user, now, topic=False):
    if row['withdrawn_at']:
        return False
    if user['role'] in MANAGEMENT_ROLES:
        return True
    return (row['author_id'] == user['id'] and 0 <= now - row['created_epoch'] < 120
            and (not topic or row['message_count'] == 1))


def topic_payload(row, user, now):
    withdrawn = bool(row['withdrawn_at'])
    return dict(id=row['id'], title='话题已撤回' if withdrawn else row['title'],
                author_id=row['author_id'], author_name=row['author_name'], created_at=row['created_at'],
                last_activity_at=row['last_activity_at'], unread_count=0 if withdrawn else row['unread_count'],
                read_message_id=row['read_message_id'], withdrawn=withdrawn,
                can_withdraw=can_withdraw(row, user, now, topic=True))


def _attachments_by_message(db, message_ids):
    """一次取回整页消息的附件，避免每条消息单独查询（列表页曾因此产生 50+ 次查询）。"""
    grouped = {}
    ids = [int(value) for value in message_ids]
    if not ids:
        return grouped
    placeholders = ','.join('?' for _ in ids)
    for row in db.execute('SELECT * FROM collaboration_attachments WHERE message_id IN ('
                          + placeholders + ') ORDER BY id', ids):
        grouped.setdefault(int(row['message_id']), []).append(row)
    return grouped


def message_payload(db, row, topic, user, now, attachments=None):
    withdrawn = bool(row['withdrawn_at'] or topic['withdrawn_at'])
    files = []
    if not withdrawn:
        rows = (attachments.get(int(row['id']), []) if attachments is not None
                else db.execute('SELECT * FROM collaboration_attachments WHERE message_id=? ORDER BY id', (row['id'],)))
        files = [dict(id=a['id'], name=a['original_name'], size=a['size_bytes'],
                      download_url=f'/discussions/api/attachments/{a["id"]}/download',
                      thumbnail_url=f'/discussions/api/attachments/{a["id"]}/thumbnail' if a['thumbnail_name'] else None)
                 for a in rows]
    return dict(id=row['id'], topic_id=row['topic_id'], author_id=row['author_id'], author_name=row['author_name'],
                created_at=row['created_at'], body='' if withdrawn else row['body'], withdrawn=withdrawn,
                can_withdraw=not withdrawn and can_withdraw(row, user, now), attachments=files)


def committed_payload(db, tid, mid, user, now, duplicate=False):
    topic = topic_row(db, tid, user['id'])
    message = db.execute('SELECT * FROM collaboration_messages WHERE id=?', (mid,)).fetchone()
    return dict(ok=True, topic_id=tid, message_id=mid, duplicate=duplicate,
                topic=topic_payload(topic, user, now), message=message_payload(db, message, topic, user, now))


@discussions_bp.errorhandler(UploadError)
def invalid_request(error):
    return fail(str(error), error.status)


@discussions_bp.errorhandler(sqlite3.Error)
def database_failure(error):
    get_db().rollback()
    return fail('协作数据暂时不可用，请保留草稿后重试', 503)


@discussions_bp.route('', strict_slashes=False)
@login_required
def index():
    return render_template('discussions.html', me=current_user(), page='discussions', role_names=ROLE_NAMES)


@discussions_bp.get('/api/items')
@discussions_bp.get('/api/item')
@discussions_bp.get('/api/item/<path:legacy_path>')
@discussions_bp.route('/api/post', methods=['GET', 'POST'])
@login_required
def retired(legacy_path=None):
    return fail('原 GitHub 展示接口已停用，请使用本地协作。', 410)


@discussions_bp.get('/api/topics')
@login_required
def topics():
    page = integer(request.args.get('page', '1'), '页码', maximum=100000)
    query = request.args.get('q', '').strip()
    if len(query) > 120:
        return fail('搜索内容不能超过 120 个字符')
    db, user, now = get_db(), current_user(), epoch_now()
    condition = '' if not query else ' WHERE withdrawn_at IS NULL AND instr(lower(title),lower(?))>0'
    params = () if not query else (query,)
    with snapshot(db):
        total = db.execute('SELECT count(*) FROM collaboration_topics' + condition, params).fetchone()[0]
        ids = db.execute('SELECT id FROM collaboration_topics' + condition + ' ORDER BY last_activity_at DESC,id DESC LIMIT ? OFFSET ?',
                         (*params, TOPIC_PAGE_SIZE, (page - 1) * TOPIC_PAGE_SIZE)).fetchall()
        result = [topic_payload(row, user, now)
                  for row in topic_rows(db, [row['id'] for row in ids], user['id'])]
    return jsonify(ok=True, topics=result, total=total, page=page, has_more=page * TOPIC_PAGE_SIZE < total)


def submission_fields(new_topic):
    if request.is_json:
        body = request.get_json(silent=True)
        uploads = []
    else:
        body = request.form
        uploads = [f for f in request.files.getlist('files') if f.filename]
        if any(key != 'files' for key in request.files):
            raise UploadError('附件字段不合法')
    allowed = {'submission_id', 'body', 'title'} if new_topic else {'submission_id', 'body'}
    if not request.is_json:
        allowed.add('csrf_token')
    if not isinstance(body, Mapping) or any(key not in allowed for key in body):
        raise UploadError('提交字段不合法')
    sid, text = body.get('submission_id'), body.get('body', '')
    title = body.get('title', '') if new_topic else ''
    if not isinstance(sid, str) or not SUBMISSION_ID.fullmatch(sid):
        raise UploadError('发送标识不合法，请保留草稿并刷新页面')
    if not isinstance(title, str) or not isinstance(text, str):
        raise UploadError('标题和正文必须是文字')
    title = title.strip()
    if new_topic and not 1 <= len(title) <= 120:
        raise UploadError('话题标题须为 1–120 个字符')
    try:
        clean_text(text, '正文', 20000, required=False)
    except ValueError as exc:
        raise UploadError(str(exc)) from exc
    if not text.strip() and not uploads:
        raise UploadError('请填写正文或添加文件')
    return sid, title, text, uploads


def post_message(tid=None):
    if tid is not None:
        integer(tid, '话题 ID')
    sid, title, text, uploads = submission_fields(tid is None)
    db, user, staged = get_db(), current_user(), []
    try:
        staged = stage_uploads(root(), uploads)
        fingerprint = hashlib.sha256(json.dumps([tid, title, text,
            [(item.original_name, item.size_bytes, item.sha256) for item in staged]],
            ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
        db.execute('BEGIN IMMEDIATE')
        user = current_user()
        if user is None:
            raise UploadError('登录已失效，请重新登录', 401)
        prior = db.execute('SELECT * FROM collaboration_submissions WHERE member_id=? AND submission_id=?', (user['id'], sid)).fetchone()
        now = epoch_now()
        if prior:
            if prior['fingerprint'] != fingerprint:
                raise UploadError('该发送标识已用于其他内容，请先核对原消息', 409)
            result = committed_payload(db, prior['topic_id'], prior['message_id'], user, now, duplicate=True)
            db.commit(); cleanup_staged(staged)
            return jsonify(result)
        quota = collaboration_storage.MEMBER_QUOTA_BYTES
        used = db.execute('SELECT COALESCE(SUM(a.size_bytes),0) FROM collaboration_attachments a JOIN collaboration_messages m ON m.id=a.message_id WHERE m.author_id=?', (user['id'],)).fetchone()[0]
        if staged and used + sum(item.size_bytes for item in staged) > quota:
            raise UploadError('附件总量已超过成员配额，请联系管理员处理', 413)
        when = stamp(now)
        if tid is None:
            tid = db.execute('INSERT INTO collaboration_topics(title,author_id,author_name,created_at,created_epoch,last_activity_at) VALUES(?,?,?,?,?,?)',
                             (title, user['id'], user['name'], when, now, when)).lastrowid
        else:
            topic = topic_row(db, tid, user['id'])
            if topic is None:
                raise UploadError('话题不存在', 404)
            if topic['withdrawn_at']:
                raise UploadError('话题已撤回，不能继续回复', 409)
        mid = db.execute('INSERT INTO collaboration_messages(topic_id,author_id,author_name,body,created_at,created_epoch) VALUES(?,?,?,?,?,?)',
                         (tid, user['id'], user['name'], text, when, now)).lastrowid
        for item in staged:
            db.execute('INSERT INTO collaboration_attachments(message_id,original_name,stored_name,size_bytes,sha256,thumbnail_name) VALUES(?,?,?,?,?,?)',
                       (mid, item.original_name, item.stored_name, item.size_bytes, item.sha256, item.thumbnail_name))
        db.execute('INSERT INTO collaboration_submissions(member_id,submission_id,fingerprint,topic_id,message_id) VALUES(?,?,?,?,?)',
                   (user['id'], sid, fingerprint, tid, mid))
        db.execute('INSERT INTO collaboration_changes(topic_id,kind,message_id,created_at) VALUES(?,?,?,?)', (tid, 'message_created', mid, when))
        db.execute('UPDATE collaboration_topics SET last_activity_at=? WHERE id=?', (when, tid))
        finalize_staged(staged)
        result = committed_payload(db, tid, mid, user, now)
        db.commit()
        return jsonify(result), 201
    except UploadError:
        db.rollback(); cleanup_staged(staged)
        raise
    except (sqlite3.Error, OSError):
        db.rollback(); cleanup_staged(staged)
        return fail('消息保存失败，草稿未清空，请核对发送结果后重试', 503)
    except Exception:
        db.rollback(); cleanup_staged(staged)
        raise


@discussions_bp.post('/api/topics')
@login_required
def create_topic():
    return post_message()


@discussions_bp.post('/api/topics/<int:tid>/messages')
@login_required
def create_message(tid):
    return post_message(tid)


@discussions_bp.get('/api/topics/<int:tid>/messages')
@login_required
def messages(tid):
    before = integer(request.args.get('before', '0'), '消息游标', minimum=0)
    db, user, now = get_db(), current_user(), epoch_now()
    with snapshot(db):
        topic = topic_row(db, tid, user['id'])
        if topic is None:
            return fail('话题不存在', 404)
        rows = db.execute('SELECT * FROM collaboration_messages WHERE topic_id=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?',
                          (tid, before, before, MESSAGE_PAGE_SIZE + 1)).fetchall()
        selected = list(reversed(rows[:MESSAGE_PAGE_SIZE]))
        files = _attachments_by_message(db, [row['id'] for row in selected])
        result = [message_payload(db, row, topic, user, now, attachments=files) for row in selected]
        cursor = db.execute('SELECT COALESCE(max(id),0) FROM collaboration_changes WHERE topic_id=?', (tid,)).fetchone()[0]
        payload = topic_payload(topic, user, now)
    return jsonify(ok=True, topic=payload, messages=result, has_more=len(rows) > MESSAGE_PAGE_SIZE,
                   before=selected[0]['id'] if selected else None, cursor=cursor)


@discussions_bp.get('/api/topics/<int:tid>/changes')
@login_required
def changes(tid):
    after = integer(request.args.get('after', '0'), '变更游标', minimum=0)
    db, user, now = get_db(), current_user(), epoch_now()
    with snapshot(db):
        topic = topic_row(db, tid, user['id'])
        if topic is None:
            return fail('话题不存在', 404)
        rows = db.execute('SELECT * FROM collaboration_changes WHERE topic_id=? AND id>? ORDER BY id LIMIT ?',
                          (tid, after, MESSAGE_PAGE_SIZE + 1)).fetchall()
        page = rows[:MESSAGE_PAGE_SIZE]
        message_ids = [int(row['message_id']) for row in page if row['message_id']]
        messages_by_id = {}
        if message_ids:
            placeholders = ','.join('?' for _ in message_ids)
            for message in db.execute('SELECT * FROM collaboration_messages WHERE id IN ('
                                      + placeholders + ')', message_ids):
                messages_by_id[int(message['id'])] = message
        files = _attachments_by_message(db, message_ids)
        events = []
        for row in page:
            message = messages_by_id.get(int(row['message_id'])) if row['message_id'] else None
            events.append(dict(id=row['id'], kind=row['kind'], message_id=row['message_id'],
                               message=message_payload(db, message, topic, user, now, attachments=files) if message else None))
        payload = topic_payload(topic, user, now)
    return jsonify(ok=True, topic=payload, events=events, has_more=len(rows) > MESSAGE_PAGE_SIZE,
                   cursor=events[-1]['id'] if events else after)


@discussions_bp.get('/api/submissions/<submission_id>')
@login_required
def submission(submission_id):
    if not SUBMISSION_ID.fullmatch(submission_id):
        return fail('发送标识不合法')
    row = get_db().execute('SELECT topic_id,message_id FROM collaboration_submissions WHERE member_id=? AND submission_id=?',
                           (current_user()['id'], submission_id)).fetchone()
    return jsonify(ok=True, found=bool(row), topic_id=row['topic_id'] if row else None, message_id=row['message_id'] if row else None)


@discussions_bp.get('/api/unread')
@login_required
def unread():
    uid = current_user()['id']
    count = get_db().execute('''SELECT count(*) FROM collaboration_messages m JOIN collaboration_topics t ON t.id=m.topic_id
      WHERE t.withdrawn_at IS NULL AND m.withdrawn_at IS NULL AND m.author_id<>?
      AND m.id>COALESCE((SELECT message_id FROM collaboration_reads r WHERE r.topic_id=t.id AND r.member_id=?),0)''', (uid, uid)).fetchone()[0]
    return jsonify(ok=True, total=count)


@discussions_bp.post('/api/topics/<int:tid>/read')
@login_required
def mark_read(tid):
    integer(tid, '话题 ID')
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {'message_id'}:
        return fail('已读位置不合法')
    mid = integer(body.get('message_id'), '消息 ID')
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        user = current_user()
        if user is None:
            raise UploadError('登录已失效，请重新登录', 401)
        uid = user['id']
        if not db.execute('SELECT 1 FROM collaboration_messages WHERE topic_id=? AND id=?', (tid, mid)).fetchone():
            raise UploadError('消息不属于此话题', 404)
        db.execute('INSERT INTO collaboration_reads(member_id,topic_id,message_id) VALUES(?,?,?) ON CONFLICT(member_id,topic_id) DO UPDATE SET message_id=max(message_id,excluded.message_id)', (uid, tid, mid))
        position = db.execute('SELECT message_id FROM collaboration_reads WHERE member_id=? AND topic_id=?', (uid, tid)).fetchone()[0]
        db.commit()
        return jsonify(ok=True, read_message_id=position)
    except Exception:
        db.rollback()
        raise


def withdraw(identifier, whole_topic=False):
    integer(identifier, '内容 ID')
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    try:
        now = epoch_now()
        user = current_user()
        if not user:
            raise UploadError('登录已失效，请重新登录', 401)
        row = topic_row(db, identifier, user['id']) if whole_topic else db.execute('SELECT * FROM collaboration_messages WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise UploadError('内容不存在', 404)
        if row['withdrawn_at']:
            if row['author_id'] != user['id'] and user['role'] not in MANAGEMENT_ROLES:
                raise UploadError('无权撤回此内容', 403)
            db.commit()
            return jsonify(ok=True)
        if not can_withdraw(row, user, now, topic=whole_topic):
            raise UploadError('无法撤回：仅可在发送后 2 分钟内撤回本人内容，话题还须没有回复', 403)
        tid = identifier if whole_topic else row['topic_id']
        when, table = stamp(now), 'collaboration_topics' if whole_topic else 'collaboration_messages'
        db.execute(f'UPDATE {table} SET withdrawn_at=?,withdrawn_by=? WHERE id=?', (when, user['id'], identifier))
        db.execute('INSERT INTO collaboration_changes(topic_id,kind,message_id,created_at) VALUES(?,?,?,?)',
                   (tid, 'topic_withdrawn' if whole_topic else 'message_withdrawn', None if whole_topic else identifier, when))
        db.execute('UPDATE collaboration_topics SET last_activity_at=? WHERE id=?', (when, tid))
        db.commit()
        return jsonify(ok=True)
    except Exception:
        db.rollback()
        raise


@discussions_bp.post('/api/messages/<int:mid>/withdraw')
@login_required
def withdraw_message(mid):
    return withdraw(mid)


@discussions_bp.post('/api/topics/<int:tid>/withdraw')
@login_required
def withdraw_topic(tid):
    return withdraw(tid, whole_topic=True)


def attachment(aid, thumbnail=False):
    integer(aid, '附件 ID')
    row = get_db().execute('''SELECT a.* FROM collaboration_attachments a
      JOIN collaboration_messages m ON m.id=a.message_id JOIN collaboration_topics t ON t.id=m.topic_id
      WHERE a.id=? AND m.withdrawn_at IS NULL AND t.withdrawn_at IS NULL''', (aid,)).fetchone()
    if not row or (thumbnail and not row['thumbnail_name']):
        return fail('附件不存在或已撤回', 404)
    path = attachment_path(root(), row['thumbnail_name'] if thumbnail else row['stored_name'])
    try:
        response = send_file(path, mimetype='image/png' if thumbnail else 'application/octet-stream',
                             as_attachment=not thumbnail, download_name='preview.png' if thumbnail else row['original_name'],
                             max_age=0, conditional=True)
    except FileNotFoundError:
        return fail('附件文件不可用，请联系管理员', 404)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return response


@discussions_bp.get('/api/attachments/<int:aid>/download')
@login_required
def download(aid):
    return attachment(aid)


@discussions_bp.get('/api/attachments/<int:aid>/thumbnail')
@login_required
def thumbnail(aid):
    return attachment(aid, thumbnail=True)
