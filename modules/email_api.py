"""Verified self-service mail binding and administrator delivery controls."""
import hashlib
import hmac
import re
import secrets
from time import time as epoch_now
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request
from common import current_user, get_db, login_required, ROLE_NAMES
from modules.daily_projects import failure, TZ
from modules.mail_service import configured, settings, verification_token, queue

bp = Blueprint('email', __name__)


def admin():
    return current_user()['role'] == 'admin'


@bp.get('/api/account/email')
@login_required
def get_email():
    row = get_db().execute('SELECT address,verified,paused,requested_at,expires_at FROM member_email WHERE member_id=?', (current_user()['id'],)).fetchone()
    return jsonify(ok=True, email=dict(row) if row else None, available=configured(), mode=settings()['mode'])


@bp.post('/api/account/email/request')
@login_required
def request_email():
    if not configured():
        return failure('邮件服务尚未配置，请联系管理员', 503)
    body = request.get_json(silent=True) or {}
    address = body.get('email', '')
    if not isinstance(address, str):
        return failure('邮箱格式不正确')
    address = address.strip()
    if len(address) > 254 or not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', address):
        return failure('请输入有效邮箱地址，不支持显示名称或多个地址')
    local, domain = address.rsplit('@', 1)
    if local.startswith('.') or local.endswith('.') or '..' in local or '..' in domain:
        return failure('邮箱格式不正确')
    address = local + '@' + domain.lower()
    db, mid, epoch = get_db(), current_user()['id'], epoch_now()
    db.execute('BEGIN IMMEDIATE')
    old = db.execute('SELECT * FROM member_email WHERE member_id=?', (mid,)).fetchone()
    count = db.execute("SELECT count(*) FROM mail_jobs WHERE member_id=? AND kind='verify' AND created_at>?", (mid, epoch-3600)).fetchone()[0]
    if (old and epoch-old['requested_at'] < 60) or count >= 5:
        db.rollback(); return failure('请间隔 60 秒重试，每小时最多申请 5 次', 429)
    generation = secrets.token_hex(24)
    token_hash = hashlib.sha256(verification_token(generation).encode()).hexdigest()
    db.execute('INSERT INTO member_email(member_id,address,generation,token_hash,expires_at,requested_at) VALUES(?,?,?,?,?,?) '
               'ON CONFLICT(member_id) DO UPDATE SET address=excluded.address,generation=excluded.generation,token_hash=excluded.token_hash,expires_at=excluded.expires_at,requested_at=excluded.requested_at,verified=0',
               (mid, address, generation, token_hash, epoch+1800, epoch))
    queue(db, mid, 'verify', datetime.now(TZ).date().isoformat(), generation, epoch, 'verify:'+generation)
    db.commit()
    return jsonify(ok=True, message='验证请求已受理，请稍后查看邮箱' if settings()['mode']=='smtp' else '验证请求已受理（模拟模式，不会向真实邮箱发送）'), 202


@bp.get('/account/email/verify')
def verify_page():
    # Keep the token in the URL fragment; neither access logs nor referrers see it.
    return render_template('email_verify.html', me=current_user(), role_names=ROLE_NAMES, page='account')


@bp.post('/api/account/email/verify')
@login_required
def verify():
    body = request.get_json(silent=True) or {}
    token = body.get('token', '')
    if not isinstance(token, str) or len(token) != 64:
        return failure('验证链接无效')
    db, mid = get_db(), current_user()['id']
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM member_email WHERE member_id=?', (mid,)).fetchone()
    if not row or row['verified'] or not row['token_hash'] or row['expires_at'] <= epoch_now() or not hmac.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest()):
        db.rollback(); return failure('验证链接过期、已使用或不属于当前账户')
    db.execute('UPDATE member_email SET verified=1,token_hash=NULL WHERE member_id=?', (mid,))
    db.commit()
    return jsonify(ok=True, message='邮箱已验证；每日提醒' + ('仍由管理员暂停' if row['paused'] else '已开启'))


@bp.get('/admin/mail')
@login_required
def admin_page():
    if not admin():
        return failure('仅管理员可查看', 403)
    return render_template('mail_admin.html', me=current_user(), role_names=ROLE_NAMES, page='mail')


@bp.get('/api/admin/mail')
@login_required
def admin_status():
    if not admin():
        return failure('仅管理员可查看', 403)
    db = get_db()
    try:
        page = max(1, min(int(request.args.get('page', '1')), 100000))
    except ValueError:
        return failure('页码不合法')
    members = [dict(r) for r in db.execute('SELECT m.id,m.name,m.is_active,m.deleted_at,e.address,e.verified,e.paused FROM members m LEFT JOIN member_email e ON e.member_id=m.id ORDER BY m.id')]
    jobs = [dict(r) for r in db.execute('SELECT j.id,j.member_id,m.name,j.kind,j.day,j.state,j.attempts,j.error,j.created_at,j.finished_at FROM mail_jobs j JOIN members m ON m.id=j.member_id ORDER BY j.id DESC LIMIT 31 OFFSET ?', ((page-1)*30,))]
    return jsonify(ok=True, members=members, jobs=jobs[:30], has_more=len(jobs)>30, page=page, mode=settings()['mode'], available=configured())


@bp.post('/api/admin/mail/<int:mid>/pause')
@login_required
def pause(mid):
    if not admin():
        return failure('仅管理员可操作', 403)
    body = request.get_json(silent=True) or {}
    if type(body.get('paused')) is not bool or mid > 2**63-1:
        return failure('暂停状态不合法')
    db = get_db()
    if not db.execute('SELECT 1 FROM members WHERE id=?', (mid,)).fetchone():
        return failure('成员不存在', 404)
    db.execute("INSERT INTO member_email(member_id,address,generation,requested_at,paused) VALUES(?,'','',0,?) ON CONFLICT(member_id) DO UPDATE SET paused=excluded.paused", (mid, int(body['paused'])))
    db.commit()
    return jsonify(ok=True)


@bp.post('/api/admin/mail/test')
@login_required
def test_mail():
    if not admin():
        return failure('仅管理员可操作', 403)
    if not configured():
        return failure('邮件服务尚未配置', 503)
    db, mid, epoch = get_db(), current_user()['id'], epoch_now()
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT * FROM member_email WHERE member_id=? AND verified=1 AND paused=0', (mid,)).fetchone()
    if not row:
        db.rollback(); return failure('请先验证本人邮箱并恢复邮件提醒')
    last = db.execute("SELECT max(created_at) FROM mail_jobs WHERE member_id=? AND kind='test'", (mid,)).fetchone()[0]
    if last and epoch-last < 60:
        db.rollback(); return failure('测试邮件请间隔 60 秒', 429)
    queue(db, mid, 'test', datetime.now(TZ).date().isoformat(), row['generation'], epoch, 'test:'+secrets.token_hex(24))
    db.commit()
    return jsonify(ok=True, message='本人测试邮件已加入队列'), 202
