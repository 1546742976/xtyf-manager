# -*- coding: utf-8 -*-
"""
资金管理模块（Flask Blueprint，路径前缀 /funds，仅管理员/项管可访问）

功能：
  - 资金记录：增删改查、筛选排序、CSV 导出
  - 审核流：未获审核授权的人添加 → 待审核；审核人添加 → 自动已审核；导航角标提醒待审数量
  - 统计图表：matplotlib 生成 PNG（月度收支/支出类别/队员排名/来源分布）
  - 台账同步：每次变更自动生成 资金管理记录.txt（含"审核"列）

数据：data/funds/funds.db（records 表）+ 资金管理记录.txt（自动生成）
"""
import base64
import csv
import io
import json
import os
from contextlib import contextmanager
from modules.input_validation import clean_text
import sqlite3
import tempfile
from datetime import datetime

from flask import current_app, Blueprint, g, jsonify, redirect, render_template, request, send_file

from common import current_user, error_response
from modules.security import has_permission
from modules.money import to_cents, format_cents, money_fields, money_json
from modules.csv_security import safe_csv_cell
from modules.funds_migrations import migrate as migrate_funds_money
from modules.funds_revisions import migrate as migrate_funds_revisions
from modules.funds_requests import migrate as migrate_funds_requests, valid_request_id, fingerprint
from modules.task_validation import InvalidIdError, parse_id
from modules.invoice_storage import InvoiceFileLock
from modules import funds_ledger

# ---- 数据目录：默认 <项目>/data/funds，可用环境变量 FUNDS_DIR 覆盖 ----
FUNDS_DIR = os.environ.get(
    "FUNDS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "funds"),
)
FUNDS_DB = os.path.join(FUNDS_DIR, "funds.db")
FUNDS_TXT = os.path.join(FUNDS_DIR, "资金管理记录.txt")

os.makedirs(FUNDS_DIR, exist_ok=True)
_ledger_lock = InvoiceFileLock(os.path.join(FUNDS_DIR, ".funds-ledger.lock"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# These are the corresponding text/surface/control/primary tokens in themes.css.
# The query chooses a known palette, never arbitrary matplotlib color input.
CHART_PALETTES = {
    'silver': ('#fffefd', '#26231f', '#625c55', '#958678', '#9b5733', 'light'),
    'rm-light': ('#ffffff', '#182947', '#52627a', '#879cb9', '#0752cf', 'light'),
    'mist': ('#ffffff', '#1d3028', '#52685e', '#9aafa3', '#176b51', 'light'),
    'paw': ('#fffaf9', '#4b2034', '#785165', '#b8889d', '#edb0c7', 'light'),
    'space': ('#0d1117', '#e6edf3', '#9da7b3', '#6e7681', '#3fb950', 'dark'),
    'rm-dark': ('#181818', '#f5f5f5', '#b0b0b0', '#737373', '#f5f5f5', 'dark'),
    'graphite': ('#27282c', '#f5f0e7', '#c2bbb0', '#8f877b', '#e7c483', 'dark'),
    'xueli': ('#352730', '#fff0f6', '#d4b4c4', '#ac8297', '#edb0cf', 'dark'),
}


def chart_palette(requested, user):
    if requested not in CHART_PALETTES:
        mode = user.get('theme_mode')
        if mode not in ('light', 'dark'):
            mode = 'dark' if user.get('ui_theme') == 'dark' else 'light'
        requested = user.get(mode + '_theme')
        if requested not in CHART_PALETTES or CHART_PALETTES[requested][5] != mode:
            requested = 'space' if mode == 'dark' else 'silver'
    surface, text, muted, border, primary, mode = CHART_PALETTES[requested]
    return requested, {'surface': surface, 'text': text, 'muted': muted, 'border': border,
                       'primary': primary, 'income': '#80d99a' if mode == 'dark' else '#248348',
                       'expense': '#ff6b66' if mode == 'dark' else '#c93432'}


def _pie_ink(rgba):
    channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in rgba[:3]]
    luminance = sum(value * weight for value, weight in zip(channels, (.2126, .7152, .0722)))
    return '#000000' if (luminance + .05) / .05 >= 1.05 / (luminance + .05) else '#ffffff'

# ---- matplotlib 中文字体与负号设置（Windows 命中雅黑，Linux 命中 Noto CJK）----
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

funds_bp = Blueprint("funds", __name__)


def can_review(user=None):
    """Independent permission bound to the immutable member ID."""
    user = user or current_user()
    return bool(user and user.get("role") in ("admin", "manager") and has_permission("funds.review", user))


def _validated_count(value):
    """Accept only non-negative integral invoice counts; do not silently truncate."""
    if isinstance(value, bool):
        raise ValueError("发票数目必须是非负整数")
    if isinstance(value, int):
        count = value
    elif isinstance(value, str) and value.isascii() and value.isdecimal():
        count = int(value)
    else:
        # money_json returns Decimal for JSON floating-point literals.
        from decimal import Decimal
        if not isinstance(value, Decimal) or not value.is_finite() or value != value.to_integral_value():
            raise ValueError("发票数目必须是非负整数")
        count = int(value)
    if not 0 <= count <= 2**63 - 1:
        raise ValueError("发票数目必须是非负整数")
    return count


def _valid_text_fields(data):
    for field, limit in {'category':64,'source_dest':200,'applicant':64,'handler':64,'notes':2000}.items():
        if not isinstance(data.get(field, ''), str):
            return False
        try:
            clean_text(data.get(field, ''), field, limit, required=False)
        except ValueError:
            return False
    return True


def valid_record_date(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def get_funds_db():
    conn = sqlite3.connect(FUNDS_DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def summarize_records(records):
    """One snapshot and Python integer sums for every total, grouping and chart."""
    stats = {'total_in': 0, 'total_out': 0, 'record_count': len(records)}
    groups = {key: {} for key in ('category_stats', 'monthly_stats', 'applicant_stats', 'source_stats', 'handler_stats')}
    def add(key, label, value, name):
        bucket = groups[key].setdefault(label, {name: label, 'total': 0, 'cnt': 0})
        bucket['total'] += value
        bucket['cnt'] += 1
    for row in records:
        amount = row['amount_cents']
        income = row['record_type'] == '进'
        stats['total_in' if income else 'total_out'] += amount
        month = row['record_date'][:7]
        monthly = groups['monthly_stats'].setdefault(month, {'month': month, 'income': 0, 'expense': 0})
        monthly['income' if income else 'expense'] += amount
        if income:
            add('source_stats', row['source_dest'] or '', amount, 'source')
        else:
            add('category_stats', row['category'] or '', amount, 'category')
            for group, column in [('applicant_stats', 'applicant'), ('handler_stats', 'handler')]:
                add(group, row[column] or '', amount, column)
    stats['balance'] = stats['total_in'] - stats['total_out']
    for key, values in groups.items():
        stats[key] = (sorted(values.values(), key=lambda row: row['month']) if key == 'monthly_stats'
                      else sorted(values.values(), key=lambda row: -row['total']))
    return stats


def record_payload(row):
    result = dict(row)
    result.update(money_fields(row['amount_cents']))
    return result


def stats_payload(stats):
    result = {'record_count': stats['record_count']}
    for key in ('total_in', 'total_out', 'balance'):
        result.update(money_fields(stats[key], key))
    for key in ('category_stats', 'monthly_stats', 'applicant_stats', 'source_stats', 'handler_stats'):
        result[key] = []
        for row in stats[key]:
            item = dict(row)
            for field in ('total', 'income', 'expense'):
                if field in item:
                    item.update(money_fields(row[field], field))
            result[key].append(item)
    return result


# ---- 台账同步：与 API 共用整数分统计，串行快照、原子替换 ----
@contextmanager
def ledger_access():
    if not _ledger_lock.acquire(timeout=5):
        raise TimeoutError('资金台账同步等待超时')
    try:
        yield
    finally:
        _ledger_lock.release()


def sync_txt():
    with ledger_access():
        conn = get_funds_db()
        try:
            conn.execute('BEGIN')
            revision = funds_ledger.status(conn)['revision']
            records = conn.execute('SELECT * FROM records ORDER BY record_date DESC,id DESC').fetchall()
        finally:
            conn.close()
        stats = summarize_records(records)
        lines = ['=' * 70, '  RoboMaster 巡天御风 | 资金管理记录',
                 f'  更新时间: {datetime.now():%Y-%m-%d %H:%M:%S}',
                 f"  总进账:   ￥{format_cents(stats['total_in']):>12}",
                 f"  总支出:   ￥{format_cents(stats['total_out']):>12}",
                 f"  结余:     ￥{format_cents(stats['balance']):>12}", '=' * 70, '',
                 f'{"ID":>4}  {"类型":4}  {"金额":>10}  {"类别":8}  {"来源/去向":20}  {"申请人":8}  {"发票":4}  {"处理人":8}  {"日期":12}  {"审核":4}  备注', '-' * 70]
        for row in records:
            rt = '进账' if row['record_type'] == '进' else '支出'
            amount = '￥' + format_cents(row['amount_cents'], grouping=True)
            review = '待审' if row['review_status'] == '待审核' else '已审'
            lines.append(f"{row['id']:>4}  {rt:4}  {amount:>10}  {row['category'] or '-':8}  {row['source_dest'] or '-':20}  {row['applicant'] or '-':8}  {str(row['invoice_count']):>4}  {row['handler'] or '-':8}  {row['record_date']:12}  {review:4}  {row['notes'] or ''}")
        lines.extend(['-' * 70, f'  共 {len(records)} 条记录', '=' * 70, ''])
        fd, temporary = tempfile.mkstemp(prefix='.ledger-', suffix='.tmp', dir=FUNDS_DIR)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8-sig') as stream:
                stream.write('\n'.join(lines))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, FUNDS_TXT)
            conn = get_funds_db()
            try:
                funds_ledger.synced(conn, revision)
            finally:
                conn.close()
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)


def init_funds_db():
    conn = sqlite3.connect(FUNDS_DB, timeout=30)
    try:
        migrate_funds_money(conn)
        migrate_funds_revisions(conn)
        migrate_funds_requests(conn)
        funds_ledger.migrate(conn)
        # The review badge and the ledger list scan these columns on every page
        # load; without indexes both degrade to full table scans.
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_records_review_status ON records(review_status);
            CREATE INDEX IF NOT EXISTS idx_records_record_date ON records(record_date DESC, id DESC);
        """)
        conn.commit()
    finally:
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
    if 'rid' in (request.view_args or {}):
        try:
            parse_id(request.view_args['rid'], '记录 ID', False)
        except InvalidIdError as exc:
            return jsonify(code=1, msg=str(exc)), 400
    user = current_user()
    if user is None:
        if "/api/" in request.path:
            return jsonify({"code": 401, "msg": "未登录"}), 401
        return redirect("/login")
    if user["role"] not in ("admin", "manager"):
        if "/api/" in request.path:
            return jsonify({"code": 403, "msg": "无权限"}), 403
        return error_response(403, "无权限访问此页面")
    return None


# ==================== 页面 ====================
@funds_bp.route("/")
def index():
    return render_template("funds.html", me=current_user(), page="funds",
                           role_names={"admin": "超级管理员", "manager": "项管", "member": "成员"},
                           can_review=can_review())


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
            q += f" AND amount_cents {op} ?"
            try:
                numeric = to_cents(val)
            except (TypeError, ValueError):
                conn.close()
                return jsonify({"code": 1, "msg": "金额筛选条件无效"}), 400
            params.append(numeric)
    for f, col in [("date_from", "record_date"), ("date_to", "record_date")]:
        val = request.args.get(f, "").strip()
        if val:
            if not valid_record_date(val):
                conn.close()
                return jsonify(code=1, msg='日期筛选条件无效（YYYY-MM-DD）'), 400
            op = ">=" if "from" in f else "<="
            q += f" AND {col} {op} ?"
            params.append(val)

    sort_by = request.args.get("sort_by", "record_date").strip()
    sort_order = request.args.get("sort_order", "DESC").strip().upper()
    allowed = {"id", "record_type", "amount", "record_date", "created_at"}
    if sort_by not in allowed:
        sort_by = "record_date"
    if sort_by == "amount":
        sort_by = "amount_cents"
    sort_order = "DESC" if sort_order != "ASC" else "ASC"
    q += f" ORDER BY {sort_by} {sort_order}"

    records = [record_payload(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return jsonify({"code": 0, "data": records})


@funds_bp.route("/api/records/<int:rid>", methods=["GET"])
# ---- 单条记录：编辑时按 ID 取一条，不再整表下载 ----
def get_record(rid):
    conn = get_funds_db()
    row = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"code": 1, "msg": "记录不存在"}), 404
    return jsonify({"code": 0, "data": record_payload(row)})


@funds_bp.route("/api/records", methods=["POST"])
# ---- 新增记录：申请人自动填当前登录人；未获审核授权的人添加 → 待审核（审核人自动已审核）----
def add_record():
    data = money_json()
    request_id = data.get('request_id')
    if not valid_request_id(request_id):
        message = '新增资金记录需要防重复提交标识，请刷新适配页面或升级客户端'
        return jsonify(code=1, msg=message, error=message, reason='idempotency_required'), 400

    def rejected(message):
        return jsonify(code=1, msg=message, reason='validation_failed', request_id=request_id), 400

    rt = data.get("record_type", "")
    if rt not in ("进", "出"):
        return rejected('类型必须为“进”或“出”')

    try:
        amount = to_cents(data.get("amount", 0), positive=True)
    except ValueError as exc:
        return rejected(str(exc))
    try:
        invoice_count = _validated_count(data.get("invoice_count", 0))
    except (TypeError, ValueError):
        return rejected('发票数目必须是非负整数')
    if not _valid_text_fields(data):
        return rejected('资金文字字段格式或长度不合法（分类64、来源200、申请人/经手人64、备注2000字）')

    rd = data.get("record_date", "")
    if not valid_record_date(rd):
        return rejected('日期格式不正确（YYYY-MM-DD）')

    user = current_user()
    if 'expected_member_id' in data and (type(data['expected_member_id']) is not int
                                        or data['expected_member_id'] != user['id']):
        return jsonify(code=1, msg='账号已变化，请使用原账号核对待确认请求', reason='account_changed'), 403
    payload_hash = fingerprint(data, amount)
    applicant = (data.get("applicant") or "").strip() or user["name"]
    data["applicant"] = applicant

    if rt == "出":
        ok, err = validate_expense_fields(data)
        if not ok:
            return rejected(err)

    auto_review = can_review(user)
    review_status = "已审核" if auto_review else "待审核"
    reviewed_by = user["name"] if auto_review else ""
    reviewed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if auto_review else ""

    conn = get_funds_db()
    rid = None
    try:
        conn.execute('BEGIN IMMEDIATE')
        receipt = conn.execute('SELECT payload_hash,result_json FROM funds_requests WHERE actor_id=? AND request_id=?',
                               (user['id'], request_id)).fetchone()
        if receipt:
            if receipt['payload_hash'] != payload_hash:
                conn.rollback()
                message = '该提交标识已用于其他内容，请先核对原请求'
                return jsonify(code=1, msg=message, error=message, reason='request_id_conflict'), 409
            result = json.loads(receipt['result_json'])
        else:
            rid = conn.execute('''INSERT INTO records
                (record_type,amount_cents,category,source_dest,applicant,invoice_count,handler,record_date,notes,review_status,reviewed_by,reviewed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (
                rt, amount, data.get('category', ''),
                data.get('source_dest', '') if rt == '进' else data.get('source_dest', '').strip(),
                applicant, invoice_count, data.get('handler', '').strip(), rd, data.get('notes', ''),
                review_status, reviewed_by, reviewed_at,
            )).lastrowid
            result = dict(code=0, msg='添加成功', id=rid, applicant=applicant,
                          review_status=review_status, version=1, request_id=request_id, **money_fields(amount))
            conn.execute('INSERT INTO funds_requests VALUES(?,?,?,?,?)',
                         (user['id'], request_id, payload_hash, json.dumps(result, ensure_ascii=False),
                          datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        current_app.logger.error('资金提交失败 stage=database record_id=%s request_id=%s error_type=%s',
                                 rid, request_id, type(exc).__name__)
        raise
    finally:
        conn.close()
    result['replayed'] = bool(receipt)
    return committed_response(result, result['id'], request_id)


@funds_bp.get('/api/capabilities')
def capabilities():
    return jsonify(code=0, create_idempotency=1, member_id=current_user()['id'])


def committed_response(result, record_id, request_id=None):
    """The database commit is final; projection failures cannot imply rollback."""
    try:
        sync_txt()
        result['ledger_sync'] = 'ok'
    except Exception as exc:
        _record_sync_failure(exc)
        result['ledger_sync'] = 'failed'
        result['msg'] = ('记录已保存' if request_id else '操作已完成') + '，台账文件同步失败；请勿重复提交'
        current_app.logger.error('资金同步失败 stage=ledger_sync record_id=%s request_id=%s error_type=%s',
                                 record_id, request_id, type(exc).__name__)
    return jsonify(result)


def _record_sync_failure(exc):
    try:
        conn = get_funds_db()
        try:
            funds_ledger.failed(conn, exc)
        finally:
            conn.close()
    except sqlite3.Error:
        current_app.logger.exception('资金台账失败状态未能持久化；请核对数据库')


@funds_bp.route('/api/ledger-sync', methods=['GET', 'POST'])
def ledger_sync():
    if request.method == 'POST':
        if not can_review():
            return jsonify(code=1, msg='重新同步台账需要资金审核管理权限'), 403
        try:
            sync_txt()
        except Exception as exc:
            _record_sync_failure(exc)
            current_app.logger.error('资金主动同步失败 stage=ledger_sync error_type=%s', type(exc).__name__)
            return jsonify(code=1, state='failed', msg='数据库记录不变，台账仍未同步；请检查目录权限及磁盘后重试'), 503
    conn = get_funds_db()
    try:
        state = funds_ledger.status(conn)
    finally:
        conn.close()
    return jsonify(code=0, can_sync=can_review(), **state)


@funds_bp.route("/api/records/<int:rid>", methods=["PUT"])
# ---- 编辑记录（不触碰审核字段）----
def update_record(rid):
    """仅在内容实际变化时写库；已审核记录改动后重新进入待审核。"""
    data = money_json()
    conn = get_funds_db()
    conn.execute("BEGIN IMMEDIATE")
    existing = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if existing is None:
        conn.close()
        return jsonify({"code": 1, "msg": "记录不存在"}), 404
    original = dict(existing)
    try:
        expected = parse_id(data.get('version'), 'version', False)
    except (InvalidIdError, ValueError):
        conn.close()
        return jsonify({"code": 1, "msg": "编辑必须携带记录版本号，请刷新后重试"}), 400
    if original["version"] != expected:
        conn.close()
        return jsonify({"code": 1, "msg": "记录已变更，请刷新后重试"}), 409
    allowed = ("record_type", "category", "source_dest", "applicant",
               "handler", "record_date", "notes", "amount", "invoice_count")
    candidate = {k: original[k] for k in allowed}
    for key in allowed:
        if key in data:
            candidate[key] = data[key]
    if not _valid_text_fields(candidate):
        conn.close()
        return jsonify({"code": 1, "msg": "资金文字字段格式或长度不合法（分类64、来源200、申请人/经手人64、备注2000字）"}), 400
    if candidate["record_type"] not in ("进", "出"):
        conn.close()
        return jsonify({"code": 1, "msg": "资金类型不合法"}), 400
    if not valid_record_date(candidate["record_date"]):
        conn.close()
        return jsonify({"code": 1, "msg": "日期格式不正确（YYYY-MM-DD）"}), 400
    try:
        amount_cents = to_cents(candidate["amount"], positive=True)
        candidate["amount"] = format_cents(amount_cents)
        candidate["invoice_count"] = _validated_count(candidate["invoice_count"])
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
        return jsonify({"code": 0, "msg": "内容未变化", "version": original["version"], **money_fields(amount_cents)})
    if "amount" in changes:
        changes.pop("amount")
        changes["amount_cents"] = amount_cents
    # 已审核记录修改后重新待审，避免审核通过后改写金额绕过复核。
    if original["review_status"] == "已审核":
        changes.update(review_status="待审核", reviewed_by="", reviewed_at="")
    sets = ", ".join(f"{key}=?" for key in changes)
    conn.execute(f"UPDATE records SET {sets},version=version+1 WHERE id=?", [*changes.values(), rid])
    conn.commit()
    conn.close()
    return committed_response({"code": 0, "msg": "已更新", "version": original["version"] + 1,
                               "review_required": "review_status" in changes, **money_fields(amount_cents)}, rid)


@funds_bp.route("/api/records/<int:rid>", methods=["DELETE"])
# ---- 删除记录：必须携带版本号；已审核记录只有审核人可以删除 ----
def delete_record(rid):
    try:
        expected = parse_id(request.args.get('version'), 'version', False)
    except (InvalidIdError, ValueError):
        return jsonify({"code": 1, "msg": "删除必须携带记录版本号，请刷新后重试"}), 400
    conn = get_funds_db()
    # Serialize with edits/reviews so a stale tab cannot delete a changed record.
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"code": 1, "msg": "记录不存在"}), 404
    if row["version"] != expected:
        conn.close()
        return jsonify({"code": 1, "msg": "记录已变更，请刷新后重试"}), 409
    if row["review_status"] == "已审核" and not can_review(current_user()):
        conn.close()
        return jsonify({"code": 403, "msg": "已审核记录只能由资金审核人删除"}), 403
    audit = getattr(g, "audit_details", None)
    if isinstance(audit, dict):
        audit["deleted"] = {
            "id": rid, "amount": row["amount"], "record_type": row["record_type"],
            "review_status": row["review_status"],
        }
    conn.execute("DELETE FROM records WHERE id=? AND version=?", (rid, expected))
    conn.commit()
    conn.close()
    return committed_response({"code": 0, "msg": "已删除"}, rid)


@funds_bp.route("/api/records/<int:rid>/review", methods=["POST"])
# ---- 审核通过：待审核 → 已审核，记录审核人/时间（供获授权审核人处理待审角标）----
def review_record(rid):
    user = current_user()
    if not can_review(user):
        return jsonify({"code": 403, "msg": "只有指定资金审核人可以审核"}), 403
    data = request.get_json(silent=True)
    try:
        expected = parse_id(data.get('version') if isinstance(data, dict) else None, 'version', False)
    except (InvalidIdError, ValueError):
        return jsonify(code=1, msg='审核必须携带有效版本号，请刷新记录'), 400
    conn = get_funds_db()
    # Serialize review with edits/deletes; a stale read must never approve a later edit.
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT id, review_status, version FROM records WHERE id=?", (rid,)).fetchone()
    if row is None:
        conn.close()
        return jsonify({"code": 1, "msg": "记录不存在"}), 404
    if row['version'] != expected:
        conn.close()
        return jsonify(code=1, msg='记录已变更，请刷新并重新确认后审核'), 409
    if row["review_status"] != "待审核":
        conn.close()
        return jsonify({"code": 1, "msg": "该记录无需重复审核"}), 400
    conn.execute(
        "UPDATE records SET review_status='已审核', reviewed_by=?, reviewed_at=?,version=version+1 WHERE id=? AND version=?",
        (user["name"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rid, expected),
    )
    conn.commit()
    conn.close()
    return committed_response({"code": 0, "msg": "已审核", "version": expected + 1}, rid)


@funds_bp.route("/api/pending_review")
# ---- 待审核数量：获授权审核人的导航角标轮询此接口 ----
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
        w.writerow([safe_csv_cell(value) for value in
                    (d["id"], d["record_type"], format_cents(d["amount_cents"]), d["category"],
                     d["source_dest"], d["applicant"], d["invoice_count"],
                     d["handler"], d["record_date"], review, d["notes"], d["created_at"])])
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
    try:
        stats = summarize_records(conn.execute('SELECT * FROM records ORDER BY id').fetchall())
    finally:
        conn.close()
    return jsonify({'code': 0, 'data': stats_payload(stats)})



@funds_bp.route("/api/chart")
# ---- 图表：matplotlib（Agg 无界面后端）生成 PNG，base64 返回给前端 <img> ----
def get_chart():
    chart_type = request.args.get("type", "monthly")
    palette, colors = chart_palette(request.args.get('palette'), current_user())
    conn = get_funds_db()

    try:
        pie_labels = []
        stats = summarize_records(conn.execute('SELECT * FROM records ORDER BY id').fetchall())
        if chart_type == 'monthly':
            rows = stats['monthly_stats']
            labels = [r['month'] for r in rows]
            # Float conversion is restricted to plot geometry; labels use exact cents.
            inc, exp = [r['income'] / 100 for r in rows], [r['expense'] / 100 for r in rows]
            fig, ax = plt.subplots(figsize=(10, 5))
            x, w = range(len(labels)), 0.35
            b1 = ax.bar([i - w / 2 for i in x], inc, w, label='收入', color=colors['income'])
            b2 = ax.bar([i + w / 2 for i in x], exp, w, label='支出', color=colors['expense'])
            ax.set_xticks(list(x))
            ax.set_xticklabels(labels, rotation=45, ha='right')
            ax.legend()
            ax.set_ylabel('金额（元）')
            ax.set_title('月度收支')
            for bars, field in [(b1, 'income'), (b2, 'expense')]:
                for bar, row in zip(bars, rows):
                    if row[field]:
                        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                                format_cents(row[field]), ha='center', va='bottom', fontsize=8)
        elif chart_type in ('category', 'applicant', 'source'):
            key, label, title = {
                'category': ('category_stats', 'category', '支出分类'),
                'applicant': ('applicant_stats', 'applicant', '成员支出排行'),
                'source': ('source_stats', 'source', '收入来源分布'),
            }[chart_type]
            rows = stats[key][:15] if chart_type == 'applicant' else stats[key]
            fig, ax = plt.subplots(figsize=(8, 5))
            if chart_type == 'source':
                if rows:
                    wedges, _, percentages = ax.pie([r['total'] / 100 for r in rows],
                           labels=[(r[label] or '其他') + ' · ' + format_cents(r['total']) for r in rows],
                           colors=[colors['primary'], colors['expense'], colors['income']],
                           autopct='%1.1f%%', startangle=90)
                    pie_labels = list(zip(percentages, wedges))
                else:
                    ax.text(0.5, 0.5, '暂无数据', ha='center', va='center')
            elif chart_type == 'applicant':
                rows = rows[::-1]
                bars = ax.barh([r[label] or '未指定' for r in rows], [r['total'] / 100 for r in rows], color=colors['primary'])
                ax.set_xlabel('金额（元）')
                for bar, row in zip(bars, rows):
                    ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                            format_cents(row['total']), ha='left', va='center', fontsize=9)
            else:
                bars = ax.bar([r[label] or '其他' for r in rows], [r['total'] / 100 for r in rows], color=colors['primary'])
                ax.set_ylabel('金额（元）')
                plt.xticks(rotation=45, ha='right')
                for bar, row in zip(bars, rows):
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                            format_cents(row['total']), ha='center', va='bottom', fontsize=9)
            ax.set_title(title)
        else:
            return jsonify({'code': 1, 'msg': '未知图表类型'}), 400

        # The browser supplies its resolved palette for follow-system mode.
        fig.patch.set_alpha(0)
        ax.patch.set_alpha(0)
        ax.tick_params(colors=colors['muted'])
        for spine in ax.spines.values():
            spine.set_color(colors['border'])
        for label in (ax.title, ax.xaxis.label, ax.yaxis.label, *ax.texts):
            label.set_color(colors['text'])
        for label, wedge in pie_labels:
            label.set_color(_pie_ink(wedge.get_facecolor()))
        legend = ax.get_legend()
        if legend is not None:
            legend.get_frame().set_alpha(0)
            for label in legend.get_texts():
                label.set_color(colors['text'])

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", transparent=True)
        buf.seek(0)
        plt.close(fig)
        return jsonify({"code": 0, "palette": palette, "data": f"data:image/png;base64,{base64.b64encode(buf.read()).decode()}"})
    except Exception:
        current_app.logger.exception("资金图表生成失败")
        plt.close("all")
        return jsonify({"code": 1, "msg": "图表生成失败，请稍后重试"}), 500
    finally:
        conn.close()


# ---- 模块导入即初始化（含老库迁移与首次台账同步）----
init_funds_db()
