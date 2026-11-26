"""Unauthenticated, non-enumerating password recovery through a previously verified email."""
import hashlib
import hmac
import re
import secrets
from datetime import datetime
from time import time as epoch_now

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from common import get_db
from modules.client_address import client_address
from modules.daily_projects import TZ
from modules.input_validation import valid_password
from modules.mail_service import configured, queue, verification_code

bp = Blueprint("password_recovery", __name__)

# Deliberately identical for missing users, unverified email, disabled mail and throttling.
ACCEPTED = "如账号已绑定经过验证的邮箱且邮件服务可用，验证码将发送到该邮箱。"
INVALID = "验证码无效或已过期，请重新申请。"
CODE_LIFETIME = 15 * 60
SOURCE_WINDOW = 60 * 60
SOURCE_MAX = 8
ACCOUNT_COOLDOWN = 60
ACCOUNT_HOURLY_MAX = 5
CODE_ATTEMPTS = 5


def _source_key():
    """Do not persist identifiable IP addresses in the throttle table."""
    message = ("password-recovery-ip:" + client_address()).encode("utf-8")
    return hmac.new(current_app.secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _consume_source_limit(db, now):
    key = _source_key()
    db.execute("DELETE FROM password_recovery_limits WHERE window_start < ?", (now - SOURCE_WINDOW,))
    row = db.execute(
        "SELECT window_start,requests FROM password_recovery_limits WHERE source_key=?", (key,)
    ).fetchone()
    if row is None:
        db.execute("INSERT INTO password_recovery_limits VALUES(?,?,1)", (key, now))
        return True
    if row["requests"] >= SOURCE_MAX:
        return False
    db.execute("UPDATE password_recovery_limits SET requests=requests+1 WHERE source_key=?", (key,))
    return True


@bp.post("/api/password-recovery/request")
def request_reset():
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    username = username.strip() if isinstance(username, str) else ""
    now = epoch_now()
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        allowed = _consume_source_limit(db, now)
        if allowed and username and len(username) <= 64 and configured():
            member = db.execute(
                "SELECT m.id,m.session_version,e.generation,e.verified FROM members m "
                "JOIN member_email e ON e.member_id=m.id "
                "WHERE m.username=? AND m.is_active=1 AND m.deleted_at IS NULL",
                (username,),
            ).fetchone()
            if member and member["verified"] and member["generation"]:
                # The active challenge is deleted after a successful reset, so it
                # cannot be the source of truth for account cooldown. Mail-job history
                # survives challenge consumption and records every accepted request.
                last_request = db.execute(
                    "SELECT max(created_at) FROM mail_jobs WHERE member_id=? AND kind='password_recovery'",
                    (member["id"],),
                ).fetchone()[0]
                recent = db.execute(
                    "SELECT count(*) FROM mail_jobs WHERE member_id=? AND kind='password_recovery' "
                    "AND created_at>?",
                    (member["id"], now - SOURCE_WINDOW),
                ).fetchone()[0]
                if (last_request is None or now - last_request >= ACCOUNT_COOLDOWN) and recent < ACCOUNT_HOURLY_MAX:
                    nonce = secrets.token_hex(24)
                    db.execute(
                        "INSERT INTO password_recovery(member_id,nonce,email_generation,session_version,"
                        "requested_at,expires_at,attempts) VALUES(?,?,?,?,?,?,0) "
                        "ON CONFLICT(member_id) DO UPDATE SET nonce=excluded.nonce,"
                        "email_generation=excluded.email_generation,session_version=excluded.session_version,"
                        "requested_at=excluded.requested_at,expires_at=excluded.expires_at,attempts=0",
                        (member["id"], nonce, member["generation"], member["session_version"],
                         now, now + CODE_LIFETIME),
                    )
                    queue(db, member["id"], "password_recovery", datetime.now(TZ).date().isoformat(),
                          member["generation"], now, "password-recovery:" + nonce)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify(ok=True, message=ACCEPTED), 202


@bp.post("/api/password-recovery/confirm")
def confirm_reset():
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    code = body.get("code")
    password = body.get("new_password")
    if not isinstance(password, str) or not valid_password(password):
        return jsonify(ok=False, error="新密码必须为 8 至 1024 位"), 400
    if (not isinstance(username, str) or not username.strip() or len(username.strip()) > 64
            or not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code)):
        return jsonify(ok=False, error=INVALID), 400
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        member = db.execute(
            "SELECT m.id,m.password_hash,m.session_version,e.generation,e.verified,"
            "r.nonce,r.email_generation,r.session_version AS issued_version,"
            "r.expires_at,r.attempts "
            "FROM members m JOIN member_email e ON e.member_id=m.id "
            "JOIN password_recovery r ON r.member_id=m.id "
            "WHERE m.username=? AND m.is_active=1 AND m.deleted_at IS NULL",
            (username.strip(),),
        ).fetchone()
        if (not member or not member["verified"]
                or member["generation"] != member["email_generation"]
                or member["session_version"] != member["issued_version"]
                or member["expires_at"] <= epoch_now() or member["attempts"] >= CODE_ATTEMPTS):
            db.rollback()
            return jsonify(ok=False, error=INVALID), 400
        expected = verification_code("recovery:" + member["nonce"])
        if not secrets.compare_digest(code, expected):
            db.execute(
                "UPDATE password_recovery SET attempts=attempts+1 WHERE member_id=?",
                (member["id"],),
            )
            db.commit()
            return jsonify(ok=False, error=INVALID), 400
        if check_password_hash(member["password_hash"], password):
            db.rollback()
            return jsonify(ok=False, error="新密码不能与当前密码相同"), 400
        db.execute(
            "UPDATE members SET password_hash=?,must_change_password=0,"
            "session_version=session_version+1 WHERE id=?",
            (generate_password_hash(password), member["id"]),
        )
        db.execute("DELETE FROM password_recovery WHERE member_id=?", (member["id"],))
        db.commit()
    except Exception:
        db.rollback()
        raise
    # Only a committed reset may identify the target to the request audit hook.
    g.recovered_member_id = member["id"]
    return jsonify(ok=True, message="密码已重置，请使用新密码登录")
