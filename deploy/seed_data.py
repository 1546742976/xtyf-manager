# -*- coding: utf-8 -*-
"""
批量导入成员与任务（初始化脚本，可重复执行；按 用户名 / 任务标题+负责人 去重）
用法：python deploy/seed_data.py [数据库路径] [本地导入 JSON 路径]
真实清单保存在 Git 忽略的 data/seed-import.json，不随源码分发。
"""
import sqlite3
import secrets
import sys
import json
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.task_catalogs import _set_member_groups, _set_member_projects
from modules.task_mutations import create_task

from werkzeug.security import generate_password_hash

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/rm-progress/data.db"
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---- 成员名单：(姓名, 账号, 组别, 角色)。已存在的账号自动跳过 ----
MEMBERS = []

# ---- 任务清单：(标题, 负责人账号, 项目, 组别, 状态, progress占位(已停用), 优先级, 说明) ----
TASKS = []


def _catalog(db, table, names):
    identifiers = []
    for name in dict.fromkeys(part.strip() for part in names.split(';') if part.strip()):
        row = db.execute(f'SELECT id,deleted_at FROM {table} WHERE name=?', (name,)).fetchone()
        if row and row['deleted_at'] is not None:
            raise RuntimeError(f'目录项已删除，拒绝导入：{table}/{name}')
        if row is None:
            identifier = db.execute(f'INSERT INTO {table}(name) VALUES(?)', (name,)).lastrowid
        else:
            identifier = row['id']
        identifiers.append(identifier)
    return identifiers


def main(data_path=None):
    members, tasks = MEMBERS, TASKS
    if data_path is not None:
        payload = json.loads(Path(data_path).read_text(encoding='utf-8'))
        members, tasks = payload['members'], payload['tasks']
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    # Keep the whole import atomic, including catalog rows and credentials.
    db.execute('BEGIN IMMEDIATE')
    created_members = []
    created_tasks = []
    try:
        actor = db.execute("SELECT id,name,role FROM members WHERE role='admin' AND is_active=1 "
                           "AND deleted_at IS NULL AND must_change_password=0 ORDER BY id LIMIT 1").fetchone()
        if actor is None:
            raise RuntimeError('缺少已完成初始改密的有效管理员，拒绝导入')
        # Refuse known-invalid inputs before creating accounts whose credentials
        # would otherwise be lost when a later task aborts the command.
        new_usernames = {username for _, username, _, _ in members
                         if not db.execute('SELECT 1 FROM members WHERE username=?', (username,)).fetchone()}
        catalogs = [('groups', groups) for _, username, groups, _ in members if username in new_usernames]
        for title, username, project, groups, *_ in tasks:
            owner = db.execute('SELECT id,is_active,deleted_at FROM members WHERE username=?', (username,)).fetchone()
            if owner and db.execute('SELECT 1 FROM tasks WHERE title=? AND owner_id=?', (title, owner['id'])).fetchone():
                continue
            if (owner is None and username not in new_usernames) or (owner and (not owner['is_active'] or owner['deleted_at'])):
                raise RuntimeError(f'负责人不存在或不可用，拒绝导入任务：{title}')
            catalogs.extend([('groups', groups), ('projects', project)])
        for table, names in catalogs:
            for name in (part.strip() for part in names.split(';') if part.strip()):
                row = db.execute(f'SELECT deleted_at FROM {table} WHERE name=?', (name,)).fetchone()
                if row and row['deleted_at'] is not None:
                    raise RuntimeError(f'目录项已删除，拒绝导入：{table}/{name}')
        for name, username, group_name, role in members:
            if db.execute('SELECT 1 FROM members WHERE username=?', (username,)).fetchone():
                continue
            group_ids = _catalog(db, 'groups', group_name)
            initial_password = secrets.token_urlsafe(10)
            member_id = db.execute(
                'INSERT INTO members(name,username,password_hash,group_name,role,created_at,must_change_password) '
                'VALUES(?,?,?,?,?,?,1)',
                (name, username, generate_password_hash(initial_password), '', role, NOW)).lastrowid
            _set_member_groups(db, member_id, group_ids)
            created_members.append(f'{name}（{username} / {initial_password}）')
        for title, username, project, group_name, status, progress, priority, detail in tasks:
            owner = db.execute('SELECT id FROM members WHERE username=? AND is_active=1 '
                               'AND deleted_at IS NULL', (username,)).fetchone()
            if owner is None:
                raise RuntimeError(f'负责人不存在或不可用，拒绝导入任务：{title}')
            if db.execute('SELECT 1 FROM tasks WHERE title=? AND owner_id=?', (title, owner['id'])).fetchone():
                continue
            group_ids = _catalog(db, 'groups', group_name)
            project_ids = _catalog(db, 'projects', project)
            if len(project_ids) > 1:
                raise RuntimeError('每个任务只能选择一个造车组别')
            task_id, version, error, status_code = create_task(db, dict(actor), {
                'title': title, 'detail': detail, 'owner_id': owner['id'],
                'project_id': project_ids[0] if project_ids else None,
                'group_ids': group_ids, 'priority': priority, 'status': status}, manage_transaction=False)
            if error:
                raise RuntimeError(f'任务导入失败：{title}：{error}')
            existing = [row[0] for row in db.execute(
                'SELECT project_id FROM member_projects WHERE member_id=?', (owner['id'],))]
            _set_member_projects(db, owner['id'], sorted(set(existing + project_ids)))
            created_tasks.append(f'{title} → {username}')
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print("新成员初始密码只在本次运行输出中显示，请妥善保存并分别通知；已有用户不会重置。")
    print(f"新增成员 {len(created_members)} 人：")
    for item in created_members:
        print("  +", item)
    print(f"新增任务 {len(created_tasks)} 条：")
    for item in created_tasks:
        print("  +", item)


if __name__ == "__main__":
    main(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / 'data' / 'seed-import.json')
