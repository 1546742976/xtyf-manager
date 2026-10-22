# -*- coding: utf-8 -*-
"""
公共工具模块（app.py 与各子模块共用）

内容：
  - BASE_DIR / DB_PATH    进程级路径基准（可用环境变量 PROGRESS_DB 覆盖，便于测试）
  - now_str()             统一时间字符串（服务器本地时间 = 北京时间）
  - get_db()              请求级 SQLite 连接（WAL + 外键，Flask g 缓存）
  - current_user()        当前登录用户（会话 uid → members 表，实时校验停用状态）
  - login_required        登录校验装饰器
  - manager_required      管理员/项管校验装饰器
"""
import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import g, jsonify, redirect, render_template, request, session, url_for

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PROGRESS_DB", os.path.join(BASE_DIR, "data.db"))
ROLE_NAMES = {"admin": "管理员", "manager": "项管", "member": "成员"}


def error_response(status_code, message):
    if '/api/' in request.path or request.path == '/api':
        return jsonify(ok=False, success=False, code=status_code, error=message, msg=message), status_code
    return render_template('error.html', status_code=status_code, title=message, message=message), status_code


# ---- 当前时间字符串（服务器本地时间；线上系统时区已设为 Asia/Shanghai）----
def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---- 请求级数据库连接：Flask g 缓存避免重复连接；WAL 提升读写并发；开启外键校验 ----
def get_db():
    db = getattr(g, "_db", None)
    if db is None:
        # 15s matches the audit connection: longer than any normal write, short
        # enough that a stuck writer surfaces as a retryable error.
        db = g._db = sqlite3.connect(DB_PATH, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
    return db


# ---- 当前登录用户：读取会话 uid 并查库（is_active=1 才有效；返回 dict）----
def avatar_url(key):
    return "/api/account/avatar?v=" + key if key else None


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    row = get_db().execute(
        "SELECT id, name, username, role, group_name, session_version, must_change_password, ui_theme, "
        "theme_mode, light_theme, dark_theme, light_custom, dark_custom, light_accent, dark_accent, avatar_key "
        "FROM members WHERE id = ? AND is_active = 1 AND deleted_at IS NULL",
        (uid,),
    ).fetchone()
    if row is None or session.get("session_version") != row["session_version"]:
        return None
    user = dict(row)
    user["avatar_url"] = avatar_url(user.pop("avatar_key"))
    return user


# ---- 装饰器：要求已登录。API 路径（含 /api/）返回 401 JSON，普通页面重定向到登录页 ----
def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            if "/api/" in request.path:
                return jsonify(ok=False, error="未登录"), 401
            return redirect(url_for("login", next=request.full_path.rstrip('?')))
        return view(*args, **kwargs)
    return wrapper


# ---- 装饰器：要求 admin/manager 角色；无权限页面返回中文 403，API 返回 JSON 403 ----
def manager_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = current_user()
        if user is None:
            if "/api/" in request.path:
                return jsonify(ok=False, error="未登录"), 401
            return redirect(url_for("login"))
        if user["role"] not in ("admin", "manager"):
            if "/api/" in request.path:
                return jsonify(ok=False, error="无权限"), 403
            return error_response(403, '无权限访问此页面')
        return view(*args, **kwargs)
    return wrapper
