"""Shared, self-maintained member introductions with optimistic moderation."""
import io
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request, send_file

from common import current_user, get_db, login_required, now_str
from modules.image_storage import ImageValidationError, attachment_path

bp = Blueprint('member_introductions', __name__)


def migrate(db):
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS member_introductions (
            member_id INTEGER PRIMARY KEY REFERENCES members(id),
            bio TEXT NOT NULL DEFAULT '', repositories TEXT NOT NULL DEFAULT '[]',
            version INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
            moderation_reason TEXT NOT NULL DEFAULT '', moderated_by INTEGER REFERENCES members(id),
            moderated_at TEXT, updated_at TEXT NOT NULL)''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(member_introductions)')}
        for column in ('github_url', 'github_profiles', 'github_repositories'):
            if column not in columns:
                db.execute(f'ALTER TABLE member_introductions ADD COLUMN {column} TEXT')


def manager():
    return current_user()['role'] in ('admin', 'manager')


def text(value, length, label):
    if not isinstance(value, str) or len(value) > length:
        raise ValueError(f'{label}须为不超过 {length} 字的文字')
    return value.strip()


def repositories(value, limit=10):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f'最多填写 {limit} 个 GitHub 仓库')
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {'url', 'name', 'description'}:
            raise ValueError('仓库字段不正确')
        raw = text(entry.get('url'), 500, '仓库地址')
        if any(ord(c) < 33 for c in raw):
            raise ValueError('仓库地址不能包含空白或控制字符')
        if raw.lower().startswith(('github.com/', 'www.github.com/')):
            raw = 'https://' + raw
        parsed = urlsplit(raw)
        if (parsed.scheme not in ('http', 'https') or parsed.netloc.lower() not in ('github.com', 'www.github.com')
                or '?' in raw or '#' in raw):
            raise ValueError('请填写不含凭据或参数的 GitHub 仓库地址')
        match = re.fullmatch(r'/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9_.-]{1,100})/?', parsed.path)
        if not match:
            raise ValueError('请填写 https://github.com/所有者/仓库 格式的地址')
        owner, repo = match.groups()
        repo = repo.removesuffix('.git')
        if not repo or repo in ('.', '..') or owner.lower() in GITHUB_RESERVED:
            raise ValueError('请填写具体仓库地址')
        url = f'https://github.com/{owner}/{repo}'
        name = text(entry.get('name', ''), 100, '展示名')
        description = text(entry.get('description', ''), 200, '仓库说明')
        if url.lower() not in seen:
            seen.add(url.lower())
            result.append(dict(url=url, name=name, description=description))
    return result


GITHUB_RESERVED = frozenset(('about', 'account', 'apps', 'business', 'codespaces', 'collections',
    'contact', 'copilot', 'customer-stories', 'dashboard', 'enterprise', 'events', 'explore',
    'features', 'issues', 'join', 'login', 'logout', 'marketplace', 'new', 'notifications',
    'organizations', 'orgs', 'password_reset', 'pricing', 'pulls', 'readme', 'search',
    'security', 'sessions', 'settings', 'site', 'sponsors', 'topics', 'trending', 'users'))


def github_homepage(value):
    if value is None:
        return None
    raw = text(value, 500, 'GitHub 个人主页')
    if not raw:
        return None
    if any(ord(c) < 33 for c in raw):
        raise ValueError('个人主页地址不能包含空白或控制字符')
    if raw.lower().startswith(('github.com/', 'www.github.com/')):
        raw = 'https://' + raw
    parsed = urlsplit(raw)
    if (parsed.scheme not in ('http', 'https') or parsed.netloc.lower() not in ('github.com', 'www.github.com')
            or '?' in raw or '#' in raw):
        raise ValueError('请填写不含凭据或参数的 GitHub 个人主页')
    match = re.fullmatch(r'/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/?', parsed.path)
    if not match or '--' in match[1] or match[1].lower() in GITHUB_RESERVED:
        raise ValueError('请填写 www.github.com/用户名 格式的个人主页，不是仓库地址')
    return 'https://github.com/' + match[1]


def github_profiles(value):
    if not isinstance(value, list) or len(value) > 3:
        raise ValueError('最多填写 3 个 GitHub 个人主页')
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {'url', 'description'}:
            raise ValueError('个人主页字段不正确')
        url = github_homepage(entry.get('url'))
        if not url:
            raise ValueError('请填写个人主页地址')
        description = text(entry.get('description', ''), 200, '主页介绍')
        if url.lower() not in seen:
            seen.add(url.lower())
            result.append(dict(url=url, description=description))
    return result


SELECT = '''SELECT m.id AS member_id,m.name,m.avatar_key,
    COALESCE((SELECT group_concat(g.name,'、') FROM member_groups mg JOIN groups g ON g.id=mg.group_id WHERE mg.member_id=m.id),m.group_name,'') AS groups,
    COALESCE(p.bio,'') AS bio,COALESCE(p.repositories,'[]') AS repositories,p.github_url,p.github_profiles,p.github_repositories,
    COALESCE(p.version,0) AS version,COALESCE(p.hidden,0) AS hidden,
    COALESCE(p.moderation_reason,'') AS moderation_reason,p.moderated_by
    FROM members m LEFT JOIN member_introductions p ON p.member_id=m.id
    WHERE m.is_active=1 AND m.deleted_at IS NULL'''


def projection(row, private=False, include_legacy=False):
    data = {key: row[key] for key in ('member_id', 'name', 'groups', 'bio', 'version', 'github_url')}
    data.update(repositories=json.loads(row['repositories']) if include_legacy else [], hidden=bool(row['hidden']),
                avatar_url=f'/api/collaboration/profiles/{row["member_id"]}/avatar' if row['avatar_key'] else None)
    data['github_profiles'] = json.loads(row['github_profiles']) if row['github_profiles'] is not None else ([dict(url=row['github_url'], description='')] if row['github_url'] else [])
    data['github_repositories'] = json.loads(row['github_repositories'] or '[]')
    if private:
        data.update(moderation_reason=row['moderation_reason'], moderated_by=row['moderated_by'])
    return data


def own(db):
    return projection(db.execute(SELECT + ' AND m.id=?', (current_user()['id'],)).fetchone(), True, True)


def body_with_version(fields):
    body = request.get_json(silent=True)
    allowed = fields if isinstance(fields, tuple) else (fields,)
    if not isinstance(body, dict) or set(body) not in allowed:
        raise ValueError('提交字段不正确')
    if type(body['version']) is not int or not 0 <= body['version'] < 2**63-1:
        raise ValueError('请读取有效的资料版本后再保存')
    return body


@bp.route('/api/account/introduction', methods=['GET', 'POST'])
@login_required
def account():
    db = get_db()
    if request.method == 'GET':
        return jsonify(ok=True, profile=own(db))
    try:
        body = body_with_version(({'version', 'bio', 'repositories'}, {'version', 'bio', 'github_url'}, {'version', 'bio', 'github_profiles', 'github_repositories'}))
        bio = text(body['bio'], 1000, '个人简介')
        modern = 'github_profiles' in body
        links = github_profiles(body['github_profiles']) if modern else None
        published_repos = repositories(body['github_repositories'], limit=3) if modern else None
        legacy = 'repositories' in body
        repos = repositories(body['repositories']) if legacy else None
        homepage = github_homepage(body['github_url']) if not legacy and not modern else None
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    db.execute('BEGIN IMMEDIATE')
    prior = own(db)
    if prior['version'] != body['version']:
        db.rollback()
        return jsonify(ok=False, error='资料或隐藏状态已变化，草稿已保留；请重新核对最新资料'), 409
    repos = repos if legacy else prior['repositories']
    if not modern:
        links = prior['github_profiles']
        published_repos = prior['github_repositories']
        if not legacy and homepage != prior['github_url']:
            links = github_profiles(([dict(url=homepage, description='')] if homepage else []) + links[1:])
    homepage = links[0]['url'] if links else None
    db.execute('''INSERT INTO member_introductions(member_id,bio,repositories,github_url,github_profiles,github_repositories,version,updated_at)
        VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(member_id) DO UPDATE SET bio=excluded.bio,repositories=excluded.repositories,
        github_url=excluded.github_url,github_profiles=excluded.github_profiles,github_repositories=excluded.github_repositories,
        version=member_introductions.version+1,updated_at=excluded.updated_at''',
        (current_user()['id'], bio, json.dumps(repos, ensure_ascii=False), homepage,
         json.dumps(links, ensure_ascii=False), json.dumps(published_repos, ensure_ascii=False), now_str()))
    db.commit()
    return jsonify(ok=True, profile=own(db))


@bp.get('/api/collaboration/profiles')
@login_required
def profiles():
    try:
        q = text(request.args.get('q', ''), 100, '搜索词')
        raw = request.args.get('page', '1')
        if not raw.isascii() or not raw.isdigit() or len(raw) > 6 or int(raw) < 1:
            raise ValueError('页码不正确')
        page = int(raw)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    include_hidden = request.args.get('include_hidden') == '1' and manager()
    sql = SELECT + " AND (COALESCE(p.bio,'')<>'' OR COALESCE(p.github_url,'')<>'' OR COALESCE(p.github_repositories,'[]')<>'[]'"
    sql += ' OR p.hidden=1)' if include_hidden else ')'
    if not include_hidden:
        sql += ' AND COALESCE(p.hidden,0)=0'
    sql = 'SELECT * FROM (' + sql + ') WHERE (instr(lower(name),lower(?))>0 OR instr(lower(groups),lower(?))>0)'
    db = get_db()
    total = db.execute('SELECT count(*) FROM (' + sql + ')', (q, q)).fetchone()[0]
    rows = db.execute(sql + ' ORDER BY member_id LIMIT 12 OFFSET ?', (q, q, (page-1)*12)).fetchall()
    return jsonify(ok=True, profiles=[projection(row, include_hidden) for row in rows], total=total, page=page,
                   has_more=page*12 < total, can_moderate=manager())


@bp.post('/api/collaboration/profiles/<int:mid>/moderation')
@login_required
def moderate(mid):
    if not manager():
        return jsonify(ok=False, error='仅超级管理员或项管可隐藏、恢复简介'), 403
    try:
        body = body_with_version({'version', 'hidden', 'reason'})
        reason = text(body['reason'], 1000, '管理原因')
        if not reason or type(body['hidden']) is not bool or mid >= 2**63:
            raise ValueError('请填写原因并选择有效的隐藏状态')
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
    db = get_db()
    db.execute('BEGIN IMMEDIATE')
    row = db.execute(SELECT + ' AND m.id=?', (mid,)).fetchone()
    if not row or not row['version']:
        db.rollback()
        return jsonify(ok=False, error='成员简介不存在'), 404
    if row['version'] != body['version']:
        db.rollback()
        return jsonify(ok=False, error='资料已变化，请刷新后核对；原因仍保留'), 409
    db.execute('UPDATE member_introductions SET hidden=?,moderation_reason=?,moderated_by=?,moderated_at=?,version=version+1 WHERE member_id=?',
               (int(body['hidden']), reason, current_user()['id'], now_str(), mid))
    db.commit()
    return jsonify(ok=True)


@bp.get('/api/collaboration/profiles/<int:mid>/avatar')
@login_required
def profile_avatar(mid):
    if mid >= 2**63:
        return jsonify(ok=False, error='成员不存在'), 404
    row = get_db().execute(SELECT + ' AND m.id=?', (mid,)).fetchone()
    if not row or not row['avatar_key'] or (row['hidden'] and mid != current_user()['id'] and not manager()):
        return jsonify(ok=False, error='头像不可用'), 404
    try:
        content = attachment_path(Path(current_app.config['TASK_ATTACHMENTS_DIR']) / 'avatars', row['avatar_key']).read_bytes()
    except (OSError, ImageValidationError):
        return jsonify(ok=False, error='头像不可用'), 404
    response = send_file(io.BytesIO(content), mimetype='image/webp', max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    return response
