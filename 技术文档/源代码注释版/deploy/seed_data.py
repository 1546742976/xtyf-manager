# -*- coding: utf-8 -*-
"""
批量导入成员与任务（初始化脚本，可重复执行；按 用户名 / 任务标题+负责人 去重）
用法：/opt/rm-progress/venv/bin/python deploy/seed_data.py
"""
import sqlite3
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "/opt/rm-progress/data.db"
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ---- 成员名单：(姓名, 账号, 组别, 角色)。已存在的账号自动跳过 ----
MEMBERS = [
    ("陈虹宇", "chy", "机械", "admin"),
    ("周奕轩", "zyx", "视觉", "admin"),
    ("林志恒", "lzh", "视觉", "member"),
    ("江金玲", "jjl", "电控", "member"),
    ("王鹏宇", "wpy", "机械", "manager"),
    ("邵辰弈", "scy", "机械", "member"),
    ("蔡道青锋", "cdqf", "电控", "member"),
    ("徐凯程", "xkc", "机械", "manager"),
    ("赵泽霖", "zzl", "电控", "manager"),
    ("吴文东", "wwd", "电控", "member"),
    ("杨洵", "yx", "视觉", "member"),
    ("武文竣", "wwj", "视觉", "member"),
    ("贡丞叶", "gcy", "视觉", "member"),
    ("洪嘉源", "hjy", "视觉", "member"),
    ("董宝龙", "dbl", "硬件", "member"),
    ("殷培栋", "ypd", "机械", "member"),
    ("王天赐", "wtc", "电控", "member"),
    ("严梓匀", "yzy", "宣传", "member"),
    ("王梦瑶", "wmy", "宣传", "member"),
]

# ---- 任务清单：(标题, 负责人账号, 项目, 组别, 状态, progress占位(已停用), 优先级, 说明) ----
TASKS = [
    ("哨兵云台机械部分", "chy", "哨兵", "机械", "进行中", 80, "中",
     "大体完成；现需给云台画一个装载 IMU 的转接件"),
    ("哨兵视觉识别部分", "zyx", "哨兵", "视觉", "进行中", 50, "中",
     "demo 已完成"),
    ("哨兵导航决策", "lzh", "哨兵", "视觉", "进行中", 50, "中",
     "demo 已完成"),
    ("哨兵整车电控部分", "jjl", "哨兵", "电控", "进行中", 80, "中",
     "大体完成；需等整车装好后一起调试"),
    ("哨兵底盘机械部分", "wpy", "哨兵", "机械", "进行中", 80, "中",
     "大体完成；等分电板固定件到货后进行底盘拼接"),
    ("轮腿步兵机械部分", "scy", "轮腿步兵", "机械", "进行中", 30, "中",
     "测试机图大致画好，预计本周审完图后发加工"),
    ("轮腿步兵整车电控部分", "cdqf", "轮腿步兵", "电控", "进行中", 0, "中", ""),
    ("狗洞步兵机械部分", "xkc", "狗洞步兵", "机械", "进行中", 60, "中",
     "已进入组装阶段"),
    ("狗洞步兵整车电控部分", "zzl", "狗洞步兵", "电控", "进行中", 20, "中",
     "等待狗洞拼好后开始调试"),
    ("重装电控部分", "wwd", "重装步兵", "电控", "进行中", 10, "中",
     "重装图尚未画好，暂时服从分配安排"),
    ("重装整车电控部分", "wtc", "重装步兵", "电控", "进行中", 0, "中", ""),
    ("所有兵种分电板与线束", "dbl", "硬件", "硬件", "进行中", 0, "中",
     "负责所有兵种的分电板、焊线等"),
    ("靶车绘制", "ypd", "靶车", "机械", "进行中", 10, "高",
     "急需推进（push）"),
    ("天目湖校区宣传", "yzy", "宣传", "宣传", "进行中", 0, "中",
     "负责队伍天目湖校区的宣传"),
    ("将军路校区宣传", "wmy", "宣传", "宣传", "进行中", 0, "中",
     "负责队伍将军路校区的宣传"),
]


def main():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    created_members = []
    for name, username, group_name, role in MEMBERS:
        exists = db.execute("SELECT id FROM members WHERE username = ?", (username,)).fetchone()
        if exists:
            continue
        db.execute(
            "INSERT INTO members (name, username, password_hash, group_name, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, username, generate_password_hash("123456"), group_name, role, NOW),
        )
        created_members.append(f"{name}（{username}）")
    db.commit()

    admin_row = db.execute("SELECT id FROM members WHERE username = 'admin'").fetchone()
    admin_id = admin_row["id"] if admin_row else None

    created_tasks = []
    for title, username, project, group_name, status, progress, priority, detail in TASKS:
        owner = db.execute("SELECT id FROM members WHERE username = ?", (username,)).fetchone()
        if owner is None:
            print(f"  ! 未找到成员 {username}，跳过任务 {title}")
            continue
        exists = db.execute(
            "SELECT id FROM tasks WHERE title = ? AND owner_id = ?", (title, owner["id"])
        ).fetchone()
        if exists:
            continue
        db.execute(
            "INSERT INTO tasks (title, detail, owner_id, group_name, project, priority, status, progress, "
            "start_date, due_date, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?)",
            (title, detail, owner["id"], group_name, project, priority, status, progress,
             admin_id, NOW, NOW),
        )
        created_tasks.append(f"{title} → {username}")
    db.commit()
    db.close()

    print(f"新增成员 {len(created_members)} 人：")
    for item in created_members:
        print("  +", item)
    print(f"新增任务 {len(created_tasks)} 条：")
    for item in created_tasks:
        print("  +", item)


if __name__ == "__main__":
    main()
