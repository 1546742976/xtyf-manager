# -*- coding: utf-8 -*-
"""
资金管理模块（Flask Blueprint，路径前缀 /funds，仅管理员/项管可访问）

功能：
  - 资金记录：增删改查、筛选排序、CSV 导出
  - 审核流：非 chy 添加 → 待审核；chy 添加 → 自动已审核；导航角标提醒待审数量
  - 统计图表：matplotlib 生成 PNG（月度收支/支出类别/队员排名/来源分布）
  - 台账同步：每次变更自动生成 资金管理记录.txt（含"审核"列）

数据：data/funds/funds.db（records 表）+ 资金管理记录.txt（自动生成）
"""
import base64
import csv
import io
import math
import os
import sqlite3
from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, send_file

from common import current_user

# ---- 数据目录：默认 <项目>/data/funds，可用环境变量 FUNDS_DIR 覆盖 ----
FUNDS_DIR = os.environ.get(
    "FUNDS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "funds"),
)
FUNDS_DB = os.path.join(FUNDS_DIR, "funds.db")
FUNDS_TXT = os.path.join(FUNDS_DIR, "资金管理记录.txt")

os.makedirs(FUNDS_DIR, exist_ok=True)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---- matplotlib 中文字体与负号设置（Windows 命中雅黑，Linux 命中 Noto CJK）----
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

funds_bp = Blueprint("funds", __name__)


def can_review(user=None):
    """审核权限单独配置，默认保留现有财务审核用户名 chy。"""
    user = user or current_user()
    reviewers = {name.strip() for name in os.environ.get("FUNDS_REVIEWERS", "chy").split(",") if name.strip()}
    return bool(user and user.get("role") in ("admin", "manager") and user.get("username") in reviewers)


def valid_record_date(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def get_funds_db():
    conn = sqlite3.connect(FUNDS_DB)
    conn.row_factory = sqlite3.Row
    return conn


# ---- 台账同步：把全部记录生成人类可读的 txt（utf-8-sig，含"审核"列），每次增删改/审核后调用 ----
def sync_txt():
    conn = sqlite3.connect(FUNDS_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM records ORDER BY record_date DESC, id DESC").fetchall()
    records = [dict(r) for r in rows]

    total_in = sum(r["amount"] for r in records if r["record_type"] == "进")
    total_out = sum(r["amount"] for r in records if r["record_type"] == "出")

    lines = []
    lines.append("=" * 70)
    lines.append("  RoboMaster 巡天御风 | 资金管理记录")
    lines.append(f'  更新时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append(f"  总进账:   ￥{total_in:>12.2f}")
    lines.append(f"  总支出:   ￥{total_out:>12.2f}")
    lines.append(f"  结余:     ￥{total_in - total_out:>12.2f}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f'{"ID":>4}  {"类型":4}  {"金额":>10}  {"类别":8}  {"来源/去向":20}  {"申请人":8}  {"发票":4}  {"处理人":8}  {"日期":12}  {"审核":4}  备注')
    lines.append("-" * 70)

    for r in records:
        rt = "进账" if r["record_type"] == "进" else "支出"
        amt = f'￥{r["amount"]:,.2f}'
        cat = r["category"] or "-"
        sd = r["source_dest"] or "-"
        apl = r["applicant"] or "-"
        inv = str(r["invoice_count"])
        hdl = r["handler"] or "-"
        dt = r["record_date"]
        review = "待审" if (dict(r).get("review_status") or "") == "待审核" else "已审"
        notes = r["notes"] or ""
        lines.append(f'{r["id"]:>4}  {rt:4}  {amt:>10}  {cat:8}  {sd:20}  {apl:8}  {inv:>4}  {hdl:8}  {dt:12}  {review:4}  {notes}')

    lines.append("-" * 70)
    lines.append(f"  共 {len(records)} 条记录")
    lines.append("=" * 70)
    lines.append("")

    with open(FUNDS_TXT, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))

    conn.close()


# ---- 建表 + 结构迁移：老库自动补 review_status / reviewed_by / reviewed_at 三列 ----
def init_funds_db():
    conn = sqlite3.connect(FUNDS_DB)
    conn.execute('''CREATE TABLE IF NOT EXISTS records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_type TEXT NOT NULL CHECK(record_type IN ('进','出')),
        amount REAL NOT NULL,
        category TEXT DEFAULT '',
        source_dest TEXT DEFAULT '',
        applicant TEXT DEFAULT '',
        invoice_count INTEGER DEFAULT 0,
        handler TEXT DEFAULT '',
        record_date TEXT NOT NULL,
        notes TEXT DEFAULT '',
        review_status TEXT DEFAULT '',
        reviewed_by TEXT DEFAULT '',
        reviewed_at TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')
    cols = [row[1] for row in conn.execute("PRAGMA table_info(records)").fetchall()]
    for col, ddl in [
        ("review_status", "ALTER TABLE records ADD COLUMN review_status TEXT DEFAULT ''"),
        ("reviewed_by", "ALTER TABLE records ADD COLUMN reviewed_by TEXT DEFAULT ''"),
        ("reviewed_at", "ALTER TABLE records ADD COLUMN reviewed_at TEXT DEFAULT ''"),
    ]:
        if col not in cols:
            conn.execute(ddl)
    conn.commit()
    conn.close()
    sync_txt()


# ---- 支出必填校验：去向 / 申请人 / 处理人 / 发票数 >= 1 ----
def validate_expense_fields(data):
    missing = []
    if not data.get("source_dest", "").strip():
        missing.append("去向")
    if not data.get("applicant", "").strip():
        missing.append("申请人")
    if not data.get("handler", "").strip():
        missing.append("处理人")
    try:
        ic = int(data.get("invoice_count", 0))
    except (TypeError, ValueError):
        ic = 0
    if ic <= 0:
        missing.append("发票数目（必须 >= 1）")
    if missing:
        return False, f'支出时以下字段必填: {", ".join(missing)}'
    return True, ""


# ---- 模块守卫：资金模块整体仅 admin/manager 可访问（API 返回 JSON 错误码）----
@funds_bp.before_request
def _funds_guard():
    user = current_user()
    if user is None:
        if "/api/" in request.path:
            return jsonify({"code": 401, "msg": "未登录"}), 401
        return redirect("/login")
    if user["role"] not in ("admin", "manager"):
        if "/api/" in request.path:
            return jsonify({"code": 403, "msg": "无权限"}), 403
        return redirect("/")
    return None


# ==================== 页面 ====================
@funds_bp.route("/")
def index():
    return render_template("funds.html", me=current_user(), can_review=can_review())


# ==================== 记录查询 API（筛选：类型/关键字/金额范围/日期；可排序）====================
@funds_bp.route("/api/records", methods=["GET"])
def get_records():
    conn = get_funds_db()
    q = "SELECT * FROM records WHERE 1=1"
    params = []

    v = request.args.get("record_type", "").strip()
    if v:
        q += " AND record_type=?"
        params.append(v)
    v = request.args.get("category", "").strip()
    if v:
        q += " AND category=?"
        params.append(v)
    kw = request.args.get("keyword", "").strip()
    if kw:
        q += " AND (source_dest LIKE ? OR applicant LIKE ? OR handler LIKE ? OR notes LIKE ?)"
        params.extend([f"%{kw}%"] * 4)
    for f in ["amount_min", "amount_max"]:
        val = request.args.get(f, "").strip()
        if val:
            op = ">=" if "min" in f else "<="
            q += f" AND amount {op} ?"
            try:
                numeric = float(val)
                if not math.isfinite(numeric):
                    raise ValueError()
            except (TypeError, ValueError):
                conn.close()
                return jsonify({"code": 1, "msg": "金额筛选条件无效"}), 400
            params.append(numeric)
    for f, col in [("date_from", "record_date"), ("date_to", "record_date")]:
        val = request.args.get(f, "").strip()
        if val:
            op = ">=" if "from" in f else "<="
            q += f" AND {col} {op} ?"
            params.append(val)

    sort_by = request.args.get("sort_by", "record_date").strip()
    sort_order = request.args.get("sort_order", "DESC").strip().upper()
    allowed = {"id", "record_type", "amount", "record_date", "created_at"}
    if sort_by not in allowed:
        sort_by = "record_date"
    sort_order = "DESC" if sort_order != "ASC" else "ASC"
    q += f" ORDER BY {sort_by} {sort_order}"

    records = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return jsonify({"code": 0, "data": records})


@funds_bp.route("/api/records", methods=["POST"])
# ---- 新增记录：申请人自动填当前登录人；非 chy 添加 → 待审核（chy 自动已审核）----
def add_record():
    data = request.get_json(silent=True) or {}
    rt = data.get("record_type", "")
    if rt not in ("进", "出"):
        return jsonify({"code": 1, "msg": "类型必须为“进”或“出”"})

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"code": 1, "msg": "金额格式无效"})
    if not math.isfinite(amount) or amount <= 0:
        return jsonify({"code": 1, "msg": "金额必须是大于0的有限数值"}), 400
    try:
        invoice_count = int(data.get("invoice_count", 0))
        if invoice_count < 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({"code": 1, "msg": "发票数目必须是非负整数"}), 400

    rd = data.get("record_date", "")
    if not valid_record_date(rd):
        return jsonify({"code": 1, "msg": "日期格式不正确（YYYY-MM-DD）"}), 400

    user = current_user()
    applicant = (data.get("applicant") or "").strip() or user["name"]
    data["applicant"] = applicant

    if rt == "出":
        ok, err = validate_expense_fields(data)
        if not ok:
            return jsonify({"code": 1, "msg": err})

    auto_review = can_review(user)
    review_status = "已审核" if auto_review else "待审核"
    reviewed_by = user["name"] if auto_review else ""
    reviewed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if auto_review else ""

    conn = get_funds_db()
    conn.execute('''INSERT INTO records
        (record_type,amount,category,source_dest,applicant,invoice_count,handler,record_date,notes,review_status,reviewed_by,reviewed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (
        rt, amount, data.get("category", ""),
        data.get("source_dest", "") if rt == "进" else data.get("source_dest", "").strip(),
        applicant, invoice_count,
        data.get("handler", "").strip(), rd, data.get("notes", ""),
        review_status, reviewed_by, reviewed_at,
    ))
    conn.commit()
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    sync_txt()
    return jsonify({"code": 0, "msg": "添加成功", "id": rid,
                    "applicant": applicant, "review_status": review_status})


@funds_bp.route("/api/records/<int:rid>", methods=["PUT"])
# ---- 编辑记录（不触碰审核字段）----
def update_record(rid):
    """仅在内容实际变化时写库；已审核记录改动后重新进入待审核。"""
    data = request.get_json(silent=True) or {}
    conn = get_funds_db()
    existing = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if existing is None:
        conn.close()
        return jsonify({"code": 1, "msg": "记录不存在"}), 404
    original = dict(existing)
    allowed = ("record_type", "category", "source_dest", "applicant",
               "handler", "record_date", "notes", "amount", "invoice_count")
    candidate = {k: original[k] for k in allowed}
    for key in allowed:
        if key in data:
            candidate[key] = data[key]
    if candidate["record_type"] not in ("进", "出"):
        conn.close()
        return jsonify({"code": 1, "msg": "资金类型不合法"}), 400
    if not valid_record_date(candidate["record_date"]):
        conn.close()
        return jsonify({"code": 1, "msg": "日期格式不正确（YYYY-MM-DD）"}), 400
    try:
        candidate["amount"] = float(candidate["amount"])
        candidate["invoice_count"] = int(candidate["invoice_count"])
        if not math.isfinite(candidate["amount"]) or candidate["amount"] <= 0 or candidate["invoice_count"] < 0:
            raise ValueError()
    except (ValueError, TypeError):
        conn.close()
        return jsonify({"code": 1, "msg": "金额或发票数目不合法"}), 400
    if candidate["record_type"] == "出":
        valid, error = validate_expense_fields(candidate)
        if not valid:
            conn.close()
            return jsonify({"code": 1, "msg": error}), 400

    changes = {key: candidate[key] for key in allowed
               if candidate[key] != original[key]}
    if not changes:
        conn.close()
        return jsonify({"code": 0, "msg": "内容未变化"})
    # 已审核记录修改后重新待审，避免审核通过后改写金额绕过复核。
    if original["review_status"] == "已审核":
        changes.update(review_status="待审核", reviewed_by="", reviewed_at="")
    sets = ", ".join(f"{key}=?" for key in changes)
    conn.execute(f"UPDATE records SET {sets} WHERE id=?", [*changes.values(), rid])
    conn.commit()
    conn.close()
    sync_txt()
    return jsonify({"code": 0, "msg": "已更新", "review_required": "review_status" in changes})


@funds_bp.route("/api/records/<int:rid>", methods=["DELETE"])
# ---- 删除记录 ----
def delete_record(rid):
    conn = get_funds_db()
    conn.execute("DELETE FROM records WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    sync_txt()
    return jsonify({"code": 0, "msg": "已删除"})


@funds_bp.route("/api/records/<int:rid>/review", methods=["POST"])
# ---- 审核通过：待审核 → 已审核，记录审核人/时间（供 chy 处理待审角标）----
def review_record(rid):
    user = current_user()
    if not can_review(user):
        return jsonify({"code": 403, "msg": "只有指定资金审核人可以审核"}), 403
    conn = get_funds_db()
    row = conn.execute("SELECT id, review_status FROM records WHERE id=?", (rid,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"code": 1, "msg": "记录不存在"})
    if row["review_status"] != "待审核":
        conn.close()
        return jsonify({"code": 1, "msg": "该记录无需重复审核"}), 400
    conn.execute(
        "UPDATE records SET review_status='已审核', reviewed_by=?, reviewed_at=? WHERE id=?",
        (user["name"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rid),
    )
    conn.commit()
    conn.close()
    sync_txt()
    return jsonify({"code": 0, "msg": "已审核"})


@funds_bp.route("/api/pending_review")
# ---- 待审核数量：chy 顶栏导航角标轮询此接口 ----
def pending_review():
    if not can_review():
        return jsonify({"code": 403, "msg": "无权查看待审核数量"}), 403
    conn = get_funds_db()
    count = conn.execute("SELECT COUNT(*) FROM records WHERE review_status='待审核'").fetchone()[0]
    conn.close()
    return jsonify({"code": 0, "count": count})


@funds_bp.route("/api/export")
# ---- 导出 CSV（含审核状态列；utf-8-sig 防 Excel 中文乱码）----
def export_csv():
    conn = get_funds_db()
    q = "SELECT * FROM records WHERE 1=1"
    params = []
    v = request.args.get("record_type", "").strip()
    if v:
        q += " AND record_type=?"
        params.append(v)
    kw = request.args.get("keyword", "").strip()
    if kw:
        q += " AND (source_dest LIKE ? OR applicant LIKE ? OR handler LIKE ? OR notes LIKE ?)"
        params.extend([f"%{kw}%"] * 4)
    q += " ORDER BY record_date DESC"
    rows = conn.execute(q, params).fetchall()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["ID", "类型", "金额", "类别", "来源/去向", "申请人", "发票数", "处理人", "日期", "审核状态", "备注", "录入时间"])
    for r in rows:
        d = dict(r)
        review = "待审核" if (d.get("review_status") or "") == "待审核" else "已审核"
        w.writerow([d["id"], d["record_type"], d["amount"], d["category"],
                    d["source_dest"], d["applicant"], d["invoice_count"],
                    d["handler"], d["record_date"], review, d["notes"], d["created_at"]])
    out.seek(0)
    buf = io.BytesIO()
    buf.write(out.getvalue().encode("utf-8-sig"))
    buf.seek(0)
    conn.close()
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name=f'funds_{datetime.now():%Y%m%d}.csv')


@funds_bp.route("/api/stats")
# ---- 统计聚合：总进/总出/结余 + 类别/月度/申请人/来源/处理人分布 ----
def get_stats():
    conn = get_funds_db()
    total_in = conn.execute("SELECT COALESCE(SUM(amount),0) FROM records WHERE record_type='进'").fetchone()[0]
    total_out = conn.execute("SELECT COALESCE(SUM(amount),0) FROM records WHERE record_type='出'").fetchone()[0]

    def query(sql):
        return [dict(r) for r in conn.execute(sql).fetchall()]

    data = {
        "total_in": total_in, "total_out": total_out,
        "balance": total_in - total_out,
        "category_stats": query("SELECT category,SUM(amount) as total FROM records WHERE record_type='出' GROUP BY category ORDER BY total DESC"),
        "monthly_stats": query("SELECT substr(record_date,1,7) as month, SUM(CASE WHEN record_type='进' THEN amount ELSE 0 END) as income, SUM(CASE WHEN record_type='出' THEN amount ELSE 0 END) as expense FROM records GROUP BY month ORDER BY month"),
        "applicant_stats": query("SELECT applicant,SUM(amount) as total,COUNT(*) as cnt FROM records WHERE record_type='出' AND applicant!='' GROUP BY applicant ORDER BY total DESC"),
        "source_stats": query("SELECT source_dest as source,SUM(amount) as total,COUNT(*) as cnt FROM records WHERE record_type='进' AND source_dest!='' GROUP BY source_dest ORDER BY total DESC"),
        "handler_stats": query("SELECT handler,SUM(amount) as total,COUNT(*) as cnt FROM records WHERE record_type='出' AND handler!='' GROUP BY handler ORDER BY total DESC"),
    }
    conn.close()
    return jsonify({"code": 0, "data": data})


@funds_bp.route("/api/chart")
# ---- 图表：matplotlib（Agg 无界面后端）生成 PNG，base64 返回给前端 <img> ----
def get_chart():
    chart_type = request.args.get("type", "monthly")
    conn = get_funds_db()

    try:
        if chart_type == "monthly":
            rows = conn.execute("""SELECT substr(record_date,1,7) as label,
                SUM(CASE WHEN record_type='进' THEN amount ELSE 0 END) as income,
                SUM(CASE WHEN record_type='出' THEN amount ELSE 0 END) as expense
                FROM records GROUP BY label ORDER BY label""").fetchall()
            labels, inc, exp = [r["label"] for r in rows], [r["income"] for r in rows], [r["expense"] for r in rows]
            fig, ax = plt.subplots(figsize=(10, 5))
            x, w = range(len(labels)), 0.35
            b1 = ax.bar([i - w / 2 for i in x], inc, w, label="Income", color="#52c41a")
            b2 = ax.bar([i + w / 2 for i in x], exp, w, label="Expense", color="#ff4d4f")
            ax.set_xticks(list(x))
            ax.set_xticklabels(labels, rotation=45, ha="right")
            ax.legend()
            ax.set_ylabel("Amount (CNY)")
            ax.set_title("Monthly Income vs Expense")
            for bars in [b1, b2]:
                for b in bars:
                    h = b.get_height()
                    if h:
                        ax.text(b.get_x() + b.get_width() / 2., h, f"{h:.0f}", ha="center", va="bottom", fontsize=8)

        elif chart_type == "category":
            rows = conn.execute("SELECT category as label,SUM(amount) as total FROM records WHERE record_type='出' GROUP BY label ORDER BY total DESC").fetchall()
            labels = [r["label"] or "Other" for r in rows]
            values = [r["total"] for r in rows]
            fig, ax = plt.subplots(figsize=(8, 5))
            colors = ["#ff4d4f", "#ff7a45", "#ffa940", "#ffc53d", "#ffec3d", "#bae637", "#36cfc9", "#40a9ff", "#597ef7", "#9254de"]
            ax.bar(labels, values, color=colors[:len(labels)])
            ax.set_ylabel("Amount (CNY)")
            ax.set_title("Expense by Category")
            plt.xticks(rotation=45, ha="right")
            for bar, v in zip(ax.patches, values):
                ax.text(bar.get_x() + bar.get_width() / 2., bar.get_height(), f"{v:.0f}", ha="center", va="bottom", fontsize=9)

        elif chart_type == "applicant":
            rows = conn.execute("SELECT applicant as label,SUM(amount) as total FROM records WHERE record_type='出' AND applicant!='' GROUP BY label ORDER BY total DESC LIMIT 15").fetchall()
            labels = [r["label"] for r in rows]
            values = [r["total"] for r in rows]
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.barh(labels[::-1], values[::-1], color="#597ef7")
            ax.set_xlabel("Amount (CNY)")
            ax.set_title("Member Expense Ranking")
            for bar, v in zip(ax.patches, values[::-1]):
                ax.text(bar.get_width() + 10, bar.get_y() + bar.get_height() / 2., f"{v:.0f}", ha="left", va="center", fontsize=9)

        elif chart_type == "source":
            rows = conn.execute("SELECT source_dest as label,SUM(amount) as total FROM records WHERE record_type='进' AND source_dest!='' GROUP BY label ORDER BY total DESC").fetchall()
            fig, ax = plt.subplots(figsize=(7, 7))
            ax.pie([r["total"] for r in rows], labels=[r["label"] for r in rows],
                   autopct="%1.1f%%", startangle=90,
                   colors=["#52c41a", "#1890ff", "#722ed1", "#fa8c16", "#13c2c2", "#eb2f96", "#a0d911"])
            ax.set_title("Income Source Distribution")
        else:
            return jsonify({"code": 1, "msg": "Unknown chart type"})

        plt.tight_layout()
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        buf.seek(0)
        plt.close(fig)
        return jsonify({"code": 0, "data": f"data:image/png;base64,{base64.b64encode(buf.read()).decode()}"})
    except Exception as e:
        plt.close("all")
        return jsonify({"code": 1, "msg": str(e)})
    finally:
        conn.close()


# ---- 模块导入即初始化（含老库迁移与首次台账同步）----
init_funds_db()
