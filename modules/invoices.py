# -*- coding: utf-8 -*-
"""
发票管理模块（Flask Blueprint，路径前缀 /invoices）

来源：由独立版"发票管理系统 v6"（原 发票/app.py，约 2160 行）机械转换而来，
      核心业务逻辑保持不变，做了以下集成适配：

  [1] BASE_DIR 指向 data/invoices（数据与代码分离，部署时不被覆盖）
  [2] Windows 的 os.startfile（本地打开文件）改为浏览器预览：
      /api/open_file 返回可预览 URL；/file/<path> 直接下发 PDF
  [3] 前端 fetch / EventSource 路径统一加 /invoices 前缀
  [4] 权限体系：
      - before_request 守卫：登录即可访问（页面级）
      - finance_required：导出/整理/查重/批量/删除等管理接口需 invoices.manage 权限
      - 数据级：成员在列表/统计/预览接口中按 submitter_id==本人 ID 过滤
  [5] 上传入库时服务端强制 submitter=登录人、status=未报销（防伪造）
  [6] 元数据键统一用正斜杠（兼容从 Windows 迁移的老数据，启动时自动归一化）
  [7] 后台任务与审计保存至 data.db，跨进程文件锁串行执行，SSE 从数据库读取进度
  [8] 新增 /api/export_zip：一键打包全部发票下载（排除 .uploads、往届发票.zip）

前端：templates/invoices.html 由 Jinja2 渲染，通过 tojson 注入
      window.IS_FINANCE / window.ME_NAME，保证用户名的脚本上下文转义。
      "成员视角"与"财务视角"仅控制显示；数据权限继续由后端校验。
"""
import os
import re
import json
import logging
import csv
import io
import shutil
import hashlib
import uuid
import time
import threading
import tempfile
import zipfile
from datetime import datetime
from decimal import Decimal
from functools import wraps
from flask import (Blueprint, Response, g, has_request_context, jsonify, redirect, request, render_template,
                   send_file, send_from_directory, stream_with_context)
from urllib.parse import quote

from common import ROLE_NAMES, current_user, get_db
from modules.security import has_permission
from modules import invoice_jobs
from modules.invoice_storage import InvoiceFileLock
from modules.csv_security import safe_csv_cell
from modules.money import MAX_CENTS, to_cents, format_cents, decimal_amount, money_fields, money_json

BASE_DIR = os.environ.get(
    'INVOICES_DIR',
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'invoices'),
)
os.makedirs(BASE_DIR, exist_ok=True)
CACHE_FILE = os.path.join(BASE_DIR, '.invoice_cache.json')
META_FILE = os.path.join(BASE_DIR, '.invoice_meta.json')
UPLOAD_DIR = os.path.join(BASE_DIR, '.uploads')

bp = Blueprint('invoices', __name__)

STATUS_NAMES = ['未报销', '正在报销', '已报销']
MONTH_PATTERN = re.compile(r'^(\d{4}\.\d{1,2})$')
SKIP_DIRS = {'__pycache__', '.uploads'}


# ---- 权限判断：是否获发票管理授权（即发票模块的"财务视角"）----
def _is_finance(user=None):
    user = user or current_user()
    return has_permission('invoices.manage', user)


# ---- 装饰器：仅获发票管理授权的人可调用（导出/整理/查重/批量/维护类接口）----
def finance_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _is_finance():
            return jsonify({'success': False, 'error': '无权限'}), 403
        return view(*args, **kwargs)
    return wrapper


# ---- 数据级权限：未获财务授权者仅能查看 submitter_id 为本人的发票文件 ----
def _submitter_id(name):
    """新记录仅绑定姓名唯一的有效账号，重名不擅自认领。"""
    if not isinstance(name, str) or not name.strip():
        return 0
    rows = get_db().execute(
        "SELECT id FROM members WHERE name=?", (name.strip(),)
    ).fetchall()
    return rows[0]['id'] if len(rows) == 1 else 0


def _belongs_to_user(entry, user):
    """Names are display snapshots, never authorization evidence."""
    owner_id = entry.get('submitter_id')
    return bool(user and type(owner_id) is int and owner_id > 0 and owner_id == user['id'])


def _resolve_submitter(data, existing=None):
    """Prefer explicit IDs; preserve established ownership on legacy unchanged-name edits."""
    if 'submitter_id' in data:
        member_id = data['submitter_id']
        if type(member_id) is not int or not 0 <= member_id <= 2**63 - 1:
            raise ValueError('提交人成员 ID 不合法')
        if member_id == 0:
            return '', 0
        row = get_db().execute('SELECT id,name FROM members WHERE id=?', (member_id,)).fetchone()
        if row is None:
            raise ValueError('提交人成员不存在')
        return row['name'], row['id']
    name = data.get('submitter', '')
    if not isinstance(name, str):
        raise ValueError('提交人必须是文本')
    name = name.strip()
    if existing and name == existing.get('submitter', ''):
        return name, existing.get('submitter_id') or 0
    if not name:
        return '', 0
    member_id = _submitter_id(name)
    if not member_id:
        raise ValueError('提交人姓名不唯一或没有对应账号，请选择带成员 ID 的提交人')
    return name, member_id


def migrate_invoice_owners(db):
    """Bind unambiguous historical names once; unresolved invoices stay finance-only."""
    with _invoice_lock:
        db.execute('BEGIN IMMEDIATE')
        try:
            if db.execute('SELECT 1 FROM security_migrations WHERE version=2').fetchone():
                db.commit()
                return
            by_name = {}
            for member_id, name in db.execute('SELECT id,name FROM members'):
                by_name.setdefault(name.strip(), []).append(member_id)
            meta = load_meta()
            known_ids = {member_id for ids in by_name.values() for member_id in ids}
            for entry in meta.values():
                owner_id = entry.get('submitter_id')
                if owner_id is not None:
                    if isinstance(owner_id, str) and owner_id.isascii() and owner_id.isdigit():
                        owner_id = int(owner_id)
                    entry['submitter_id'] = owner_id if type(owner_id) is int and owner_id in known_ids else 0
                    continue
                candidates = by_name.get(str(entry.get('submitter') or '').strip(), [])
                entry['submitter_id'] = candidates[0] if len(candidates) == 1 else 0
                entry['ownership_source'] = 'legacy_unique_name' if len(candidates) == 1 else 'needs_confirmation'
            save_meta(meta)
            db.execute('INSERT INTO security_migrations VALUES(2)')
            db.commit()
        except Exception:
            db.rollback()
            raise


def _user_can_view(rel_path):
    user = current_user()
    if _is_finance(user):
        return True
    rel = (rel_path or '').replace('\\', '/')
    meta = load_meta()
    for key, value in meta.items():
        if key.replace('\\', '/') == rel and _belongs_to_user(value, user):
            return True
    return False

# ========== 后台任务管理 ==========
_invoice_lock = InvoiceFileLock(os.path.join(BASE_DIR, '.invoice.lock'))
_worker_guard = threading.Lock()
_worker_thread = None
_worker_pid = None
_worker_wakeup = threading.Event()


def _reset_worker_after_fork():
    global _worker_guard, _worker_thread, _worker_pid, _worker_wakeup
    _worker_guard = threading.Lock()
    _worker_thread = None
    _worker_pid = None
    _worker_wakeup = threading.Event()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_reset_worker_after_fork)


def start_invoice_worker():
    """One dispatcher per process; the shared file lock elects the executor."""
    global _worker_thread, _worker_pid
    with _worker_guard:
        if _worker_pid == os.getpid() and _worker_thread and _worker_thread.is_alive():
            return
        _worker_pid = os.getpid()
        _worker_thread = threading.Thread(target=_invoice_worker, name='invoice-queue', daemon=True)
        _worker_thread.start()


def _invoice_worker():
    handlers = {'scan_dups': _do_scan_dups, 'organize': _do_organize,
                'refresh': _do_refresh, 'delete_dups': _do_delete_dups}
    while True:
        claimed = None
        try:
            if _invoice_lock.acquire(blocking=False):
                try:
                    claimed = invoice_jobs.recover_and_claim()
                    if claimed:
                        try:
                            result = handlers[claimed['type']](claimed['id'])
                        except Exception as exc:
                            logging.exception('Invoice job failed: %s', claimed['id'])
                            invoice_jobs.finish_job(claimed['id'], error=exc)
                        else:
                            invoice_jobs.finish_job(claimed['id'], result=result)
                finally:
                    _invoice_lock.release()
        except Exception:
            logging.exception('Invoice queue dispatch failed; will retry')
        # Yield between jobs so pending browser writes can acquire the file lock.
        _worker_wakeup.wait(0.2 if claimed else 2)
        _worker_wakeup.clear()


def _update_job(jid, progress, message):
    invoice_jobs.update_job(jid, progress, message)


def _job_sse(jid):
    """Read persisted progress without blocking invoice writes or other workers."""
    def generate():
        previous = None
        heartbeat = time.monotonic()
        while True:
            # Revalidate the session and permission while a long-lived stream is open.
            if not _is_finance():
                yield 'data: ' + json.dumps({'status': 'error', 'message': '登录或财务授权已失效'}, ensure_ascii=False) + '\n\n'
                break
            job = invoice_jobs.get_job(jid)
            if job is None:
                yield 'data: ' + json.dumps({'status': 'error', 'message': '任务不存在'}, ensure_ascii=False) + '\n\n'
                break
            payload = {key: job[key] for key in ('id', 'type', 'status', 'progress', 'message')}
            if job['status'] in invoice_jobs.TERMINAL:
                payload['result'] = job['result']
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != previous:
                yield f'data: {encoded}\n\n'
                previous = encoded
                heartbeat = time.monotonic()
            elif time.monotonic() - heartbeat >= 15:
                yield ': keep-alive\n\n'
                heartbeat = time.monotonic()
            if job['status'] in invoice_jobs.TERMINAL:
                break
            time.sleep(1)
    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


def _normalize_month(s):
    if not s: return s
    parts = s.split('.')
    if len(parts) == 2:
        try: return f"{parts[0]}.{int(parts[1])}"
        except ValueError: return s
    return s


def _validated_invoice_month(value):
    """Accept only real invoice folders; canonicalize e.g. 2026.01 -> 2026.1."""
    if not isinstance(value, str):
        raise ValueError('月份格式无效，应为 YYYY.M')
    value = value.strip()
    if value == '未归类':
        return value
    matched = MONTH_PATTERN.fullmatch(value)
    if not matched or not 1 <= int(matched.group(1).split('.')[1]) <= 12:
        raise ValueError('月份格式无效，应为 YYYY.M（1-12 月）')
    return _normalize_month(value)


def _validated_invoice_date(value):
    """Keep manually corrected issue dates separate from filename/PDF parsing."""
    if not isinstance(value, str):
        raise ValueError('开票日期格式无效')
    if not value:
        return ''
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('开票日期格式无效，应为 YYYY-MM-DD')
    try:
        datetime.strptime(value, '%Y-%m-%d')
    except ValueError as exc:
        raise ValueError('开票日期不是有效日期') from exc
    return value


def _file_hash(filepath):
    """计算文件 MD5 用于精确去重"""
    h = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
    except:
        return ''
    return h.hexdigest()


# ---- JSON 读写（.invoice_meta.json 元数据 / .invoice_cache.json 金额缓存）----
def load_json(path, default=None, *, decimal_numbers=False):
    if default is None: default = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f, parse_float=Decimal) if decimal_numbers else json.load(f)
        except (ValueError, OSError) as exc:
            raise RuntimeError(f'无法读取发票元数据文件：{os.path.basename(path)}') from exc
    return default


def save_json(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path), suffix='.tmp', dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


load_meta = lambda: load_json(META_FILE)
save_meta = lambda d: save_json(META_FILE, d)
def load_cache():
    cache = load_json(CACHE_FILE, decimal_numbers=True)
    for key, entry in cache.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f'发票金额缓存格式错误：{key}')
        if 'amount_cents' in entry:
            cents = entry['amount_cents']
            if type(cents) is not int or not 0 <= cents <= MAX_CENTS:
                raise RuntimeError(f'发票缓存分值无效：{key}')
            entry['amount'] = Decimal(format_cents(cents))
        elif entry.get('amount') is not None:
            try:
                entry['legacy_amount'] = str(entry['amount'])
                entry['amount'] = decimal_amount(entry['amount'])
            except ValueError as exc:
                raise RuntimeError(f'发票历史金额无效：{key}') from exc
    return cache


def save_cache(cache):
    encoded = {}
    for key, value in cache.items():
        entry = dict(value)
        amount = entry.pop('amount', None)
        if amount is not None:
            entry['amount_cents'] = to_cents(amount)
        encoded[key] = entry
    save_json(CACHE_FILE, encoded)


def _migrate_cache_amounts():
    raw = load_json(CACHE_FILE, decimal_numbers=True)
    if any(isinstance(entry, dict) and entry.get('amount') is not None for entry in raw.values()):
        save_cache(load_cache())


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def is_skipped(name):
    return name.startswith('.') or name in SKIP_DIRS


# ---- 路径安全：拼接路径并校验结果仍在数据目录内（防目录穿越）----
def _safe_join(*parts):
    """安全拼接路径: 解析真实路径并确保位于 BASE_DIR 内, 否则返回 None"""
    base = os.path.realpath(BASE_DIR)
    p = os.path.realpath(os.path.join(BASE_DIR, *parts))
    if p != base and not p.startswith(base + os.sep):
        return None
    return p


# ---- 相对路径合法性校验：拒绝绝对路径/盘符/.. 组件 ----
def _is_safe_rel(rel):
    """校验客户端传入的相对路径: 无 '..' 组件且无盘符/绝对路径特征"""
    if not rel or '\x00' in rel:
        return False
    s = rel.replace('\\', '/')
    if s.startswith('/') or re.match(r'^[A-Za-z]:', s):
        return False
    return '..' not in [p for p in s.split('/') if p]


def _user_upload_dir():
    """上传暂存文件按成员 ID 隔离。"""
    directory = os.path.join(UPLOAD_DIR, str(current_user()['id']))
    ensure_dir(directory)
    return directory


def _own_temp_path(rel):
    """仅允许操作本人的普通临时 PDF 文件。"""
    if not isinstance(rel, str) or not _is_safe_rel(rel):
        return None
    path = _safe_join(rel)
    if not path or not path.lower().endswith('.pdf'):
        return None
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(_user_upload_dir()):
        return None
    return path if os.path.isfile(path) else None


def _save_temp_pdf(file):
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', file.filename)
    path = os.path.join(_user_upload_dir(), uuid.uuid4().hex + '_' + safe_name)
    file.save(path)
    with open(path, 'rb') as stream:
        valid_pdf = stream.read(5) == b'%PDF-'
    if not valid_pdf:
        os.remove(path)
        raise ValueError('文件不是有效的 PDF 格式')
    return path


def _visible_duplicates(items):
    """成员只看到重复类型，不泄漏其他成员的路径。"""
    if _is_finance():
        return items
    return [{'type': entry.get('type', '重复发票'), 'path': ''} for entry in items]


def _valid_amount(value):
    try:
        return decimal_amount(value)
    except ValueError:
        return None


def _migrate_cache_key(old_rel, new_rel):
    """文件移动后把金额缓存键从旧路径迁移到新路径"""
    try:
        cache = load_cache()
        old_norm = os.path.normcase(old_rel.replace('\\', '/'))
        for k in list(cache.keys()):
            if os.path.normcase(k.replace('\\', '/')) == old_norm:
                cache[new_rel] = cache.pop(k)
                save_cache(cache)
                break
    except Exception:
        pass


def cleanup_uploads(max_age_hours=24 * 7):
    """把 .uploads 中超过时长的孤儿临时文件移入 _待清理 子文件夹(隔离, 不直接删除)"""
    if not os.path.isdir(UPLOAD_DIR):
        return 0
    quarantine = os.path.join(UPLOAD_DIR, '_待清理')
    ensure_dir(quarantine)
    moved = 0
    cutoff = time.time() - max_age_hours * 3600
    for root, dirs, files in os.walk(UPLOAD_DIR):
        dirs[:] = [d for d in dirs if d != '_待清理']
        for name in files:
            fp = os.path.join(root, name)
            if not os.path.isfile(fp) or os.path.islink(fp) or os.path.getmtime(fp) >= cutoff:
                continue
            try:
                shutil.move(fp, os.path.join(quarantine, uuid.uuid4().hex + '_' + name))
                moved += 1
            except OSError:
                pass
    return moved


def _remove_empty_dirs(path):
    """递归删除空目录（从叶子向上）"""
    removed = 0
    try:
        for entry in sorted(os.listdir(path), reverse=True):
            fp = os.path.join(path, entry)
            if os.path.isdir(fp) and not is_skipped(entry):
                removed += _remove_empty_dirs(fp)
        if not os.listdir(path):
            try:
                os.rmdir(path)
                removed += 1
            except OSError:
                pass
    except PermissionError:
        pass
    return removed


# ===================== PDF 解析 =====================

# ---- PDF 文本提取（pdfplumber）----
def _read_pdf_text(filepath):
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            parts = []
            for page in pdf.pages:
                t = page.extract_text()
                if t: parts.append(t)
            return ' '.join(parts)
    except Exception:
        return ''


# ---- 从 PDF 内容提取价税合计金额 ----
def extract_amount_from_pdf(filepath):
    text = _read_pdf_text(filepath)
    if not text: return None
    for pat in [
        r'价税合计[（(]大写[）)][^¥]*[¥￥]\s*([\d,]+\.?\d*)',
        r'价税合计.*?[¥￥]\s*([\d,]+\.?\d*)',
    ]:
        m = re.search(pat, text)
        if m:
            try: return decimal_amount(m.group(1).replace(',', ''))
            except ValueError: continue
    return None


# ---- 从 PDF 内容提取发票号码 ----
def extract_invoice_number(filepath):
    text = _read_pdf_text(filepath)
    if not text: return None
    m = re.search(r'发票号码[：:]\s*(\d{10,30})', text)
    if m: return m.group(1).strip()
    return None


def extract_invoice_info(filepath):
    text = _read_pdf_text(filepath)
    result = {'amount': None, 'date': '', 'month': '', 'vendor': '', 'invoice_number': ''}

    n = re.search(r'发票号码[：:]\s*(\d{10,30})', text)
    if n: result['invoice_number'] = n.group(1).strip()

    for pat in [r'价税合计[（(]大写[）)][^¥]*[¥￥]\s*([\d,]+\.?\d*)',
                r'价税合计.*?[¥￥]\s*([\d,]+\.?\d*)']:
        m = re.search(pat, text)
        if m:
            try: result['amount'] = decimal_amount(m.group(1).replace(',', '')); break
            except ValueError: continue

    # 开票日期: 容忍 "2026年 07月03日" / "2026-07-03" / "2026/07/03" 等空格与分隔符变体
    m = re.search(r'开票日期[：:]\s*(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?', text)
    if m:
        result['date'] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        result['month'] = _normalize_month(f"{m.group(1)}.{int(m.group(2))}")

    # 销售方名称: 兼容数电票竖版布局 "销 名称：xxx" (销/售/方 分散、名称带空格)
    vendor = ''
    m = re.search(r'销\s*售?\s*方?\s*信息?\s*名称[：:]\s*([^\n]+)', text)
    if not m:
        m = re.search(r'销\s*售?\s*方.*?名称[：:]\s*([^\n]+)', text, re.DOTALL)
    if m:
        vendor = re.sub(r'\s+', '', m.group(1)).strip()
        end = re.search(r'(有限责任公司|有限公司|公司|商行|经营部|中心|商店|工厂|销售部|旗舰店|专营店|店|厂|社)', vendor)
        if end: vendor = vendor[:end.end()]
    if not vendor:
        # 兜底: 取文本中最后一个 "名称: xxx公司" 样式(销售方通常在购买方之后)
        names = re.findall(r'名称[：:]\s*([\u4e00-\u9fa5A-Za-z0-9（）()\s]{2,40}?(?:公司|商行|经营部|中心|商店|旗舰店|专营店|工厂|厂|店|社))', text)
        if names:
            vendor = re.sub(r'\s+', '', names[-1]).strip()
    result['vendor'] = vendor

    return result


# ===================== 文件名解析 =====================

# ---- 文件名解析：标准命名 {类别}-{金额}元-{供应商}-{发票号}-{日期}-发票.pdf ----
def parse_invoice_filename(filename):
    if not filename.lower().endswith('.pdf'): return None
    name = filename[:-4]
    # {序号-}{类别}-{金额}元-{供应商}[-{发票号}]-{日期}-发票
    m = re.match(r'^(\d+-)?(.+?)-([\d,.]+)元-(.+?)(?:-(\d{10,30}))?-(\d{4}\.\d{2}\.\d{2})-发票$', name)
    if m:
        return {'seq': m.group(1).rstrip('-') if m.group(1) else '',
                'category': m.group(2), 'amount': decimal_amount(m.group(3).replace(',', '')),
                'vendor': m.group(4), 'date': m.group(6).replace('.', '-'), 'from_filename': True}
    m2 = re.search(r'([\d,.]+)元', name)
    if m2:
        return {'seq': '', 'category': '', 'amount': decimal_amount(m2.group(1).replace(',', '')),
                'vendor': name, 'date': '', 'from_filename': True}
    return {'seq': '', 'category': '', 'amount': Decimal('0.00'), 'vendor': name, 'date': '', 'from_filename': False}


# ---- 金额取值优先级：文件名 → 金额缓存 → 实时解析 PDF ----
def resolve_amount(inv, use_cache=True):
    cache = load_cache() if use_cache else {}
    cache = {k.replace('\\', '/'): v for k, v in cache.items()}
    key = inv['id'].replace('\\', '/')
    if inv.get('from_filename') and inv['amount'] > 0:
        return inv['amount'], '文件名'
    if key in cache and cache[key].get('amount') is not None:
        return cache[key]['amount'], 'PDF(缓存)'
    filepath = os.path.join(BASE_DIR, inv.get('rel_path', ''))
    if os.path.isfile(filepath):
        pdf_amount = extract_amount_from_pdf(filepath)
        if pdf_amount is not None:
            cache[key] = {'amount': pdf_amount, 'vendor': inv['vendor'],
                          'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            save_cache(cache)
            return pdf_amount, 'PDF'
    return inv.get('amount', Decimal('0.00')), '未知'


# ===================== 文件夹管理 =====================

def get_month_folder(month):
    return os.path.join(BASE_DIR, month)


def get_status_folder(month, status):
    return os.path.join(BASE_DIR, month, status)


# ---- 预创建 月份/状态 目录骨架 ----
def build_folder_structure():
    for entry in os.listdir(BASE_DIR):
        if not os.path.isdir(ep := os.path.join(BASE_DIR, entry)): continue
        if is_skipped(entry): continue
        if MONTH_PATTERN.match(entry):
            for status in STATUS_NAMES:
                ensure_dir(os.path.join(ep, status))


# ---- 状态流转：移动 PDF 到新状态目录 + 同步元数据（键统一正斜杠）----
def move_invoice_file(inv_id, new_status):
    if not _is_safe_rel(inv_id) or new_status not in STATUS_NAMES:
        return False
    inv_id = inv_id.replace('\\', '/')
    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    inv_meta = meta.get(inv_id, {})

    filename = inv_meta.get('filename', '')
    month = inv_meta.get('month', '')
    if not month or not filename:
        filename = os.path.basename(inv_id)
        parts = inv_id.replace('\\', '/').split('/')
        if parts:
            m = re.match(r'^(\d{4}\.\d{1,2})', parts[0])
            if m: month = _normalize_month(m.group(1))
        if not month or not filename: return False

    filename = _safe_name(filename)
    if not MONTH_PATTERN.match(month):
        return False

    old_path = _safe_join(inv_id)
    if not old_path:
        old_rel = inv_meta.get('rel_path', '')
        if _is_safe_rel(old_rel):
            old_path = _safe_join(old_rel)
    if not old_path:
        old_path = _find_existing_file(month, filename) or ''
    if not old_path or not os.path.isfile(old_path): return False

    ensure_dir(os.path.join(BASE_DIR, month, new_status))
    new_path = os.path.join(BASE_DIR, month, new_status, filename)

    if os.path.normcase(old_path) == os.path.normcase(new_path): return True

    counter = 1
    while os.path.exists(new_path):
        name, ext = os.path.splitext(filename)
        new_path = os.path.join(BASE_DIR, month, new_status, f"{name}_{counter}{ext}")
        counter += 1

    try:
        old_rel = os.path.relpath(old_path, BASE_DIR).replace('\\', '/')
        if os.path.isfile(old_path): shutil.move(old_path, new_path)
        new_rel = os.path.relpath(new_path, BASE_DIR).replace('\\', '/')
        inv_meta['rel_path'] = new_rel
        inv_meta['filename'] = os.path.basename(new_path)
        meta.pop(inv_id, None)
        meta[new_rel] = inv_meta
        save_meta(meta)
        _migrate_cache_key(old_rel, new_rel)
        return True
    except Exception as e:
        print(f'[移动失败] {old_path} -> {new_path}: {e}')
        return False


def _find_existing_file(month, filename):
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if not is_skipped(d)]
        if filename in files: return os.path.join(root, filename)
    return None


def organize_all_files():
    return _organize_impl()


def _safe_name(s):
    """文件名字符安全化"""
    if not s: return '未知'
    return re.sub(r'[\\/:*?"<>|]', '_', str(s))


# ---- 生成标准文件名（供整理/入库复用）----
def _generate_standard_name(filepath, confirmed_amount=None):
    """根据PDF内容和文件名生成标准化名称: {category}-{amount}元-{vendor}-{inv_num}-{date}-发票.pdf"""
    info = extract_invoice_info(filepath)
    fn_info = parse_invoice_filename(os.path.basename(filepath))

    # 金额：优先文件名，其次PDF内容
    amount = Decimal('0.00')
    if fn_info and fn_info.get('from_filename') and fn_info.get('amount'):
        amount = fn_info['amount']
    if not amount and info.get('amount'):
        amount = info['amount']

    if confirmed_amount is not None:
        amount = confirmed_amount

    # 日期
    date_str = ''
    if fn_info and fn_info.get('date'):
        date_str = fn_info['date'].replace('-', '.')
    if not date_str and info.get('date'):
        date_str = info['date'].replace('-', '.')
    if not date_str:
        # 从文件名尝试提取
        m = re.search(r'(\d{4}\.\d{2}\.\d{2})', os.path.basename(filepath))
        if m: date_str = m.group(1)

    # 供应商
    vendor = (fn_info or {}).get('vendor', '') if fn_info else ''
    if not vendor or vendor == os.path.basename(filepath)[:-4] or '销名称' in vendor or '销 名称' in vendor:
        vendor = info.get('vendor', '')
    vendor = _safe_name(vendor)

    # 类别
    category = (fn_info or {}).get('category', '') if fn_info else ''
    if not category: category = '其他'

    # 发票号码
    inv_num = info.get('invoice_number', '')
    inv_num_safe = _safe_name(inv_num)[:16] if inv_num else ''

    # 组装
    parts = []
    if category: parts.append(category)
    if amount > 0 or confirmed_amount is not None: parts.append(f'{amount:.2f}元')
    if vendor: parts.append(vendor)
    if inv_num_safe: parts.append(inv_num_safe)
    if date_str: parts.append(date_str)
    parts.append('发票.pdf')
    return '-'.join(parts)


def _do_move(results, meta, inv_id, old_path, new_path, month, status):
    # 尝试生成标准化文件名
    try:
        std_name = _generate_standard_name(old_path)
    except:
        std_name = os.path.basename(new_path)
    target_dir = os.path.dirname(new_path)
    new_path = os.path.join(target_dir, std_name)

    counter = 1
    orig = new_path
    while os.path.exists(new_path):
        name, ext = os.path.splitext(os.path.basename(orig))
        new_path = os.path.join(os.path.dirname(orig), f"{name}_{counter}{ext}")
        counter += 1

    inv_num = ''
    try:
        inv_num = extract_invoice_info(old_path).get('invoice_number', '')
    except Exception:
        pass

    try:
        if os.path.isfile(old_path):
            shutil.move(old_path, new_path)
            results['moved'] += 1
            old_rel = os.path.relpath(old_path, BASE_DIR)
            new_rel = os.path.relpath(new_path, BASE_DIR)
            old_meta = meta.pop(inv_id, {})
            old_meta.update({
                'rel_path': new_rel, 'month': month,
                'filename': os.path.basename(new_path),
                'status': status if not old_meta.get('status') else old_meta['status'],
                'invoice_number': old_meta.get('invoice_number') or inv_num,
                'organized': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
            meta[new_rel] = old_meta
            _migrate_cache_key(old_rel, new_rel)
        else:
            results['errors'] += 1
            results['details'].append(f'文件不存在: {old_path}')
    except PermissionError as e:
        results['errors'] += 1
        results['details'].append(f'权限不足: {old_path} ({e})')
    except Exception as e:
        results['errors'] += 1
        results['details'].append(f'{os.path.basename(old_path)}: {e}')


# ===================== 查重 =====================

def _build_invoice_number_index():
    """建立发票号码索引"""
    index = {}
    for entry in os.listdir(BASE_DIR):
        entry_path = os.path.join(BASE_DIR, entry)
        if not os.path.isdir(entry_path) or is_skipped(entry): continue
        for root, dirs, files in os.walk(entry_path):
            dirs[:] = [d for d in dirs if not is_skipped(d)]
            for f in files:
                if not f.lower().endswith('.pdf'): continue
                fp = os.path.join(root, f)
                inv_num = extract_invoice_number(fp)
                if inv_num:
                    if inv_num not in index:
                        index[inv_num] = []
                    index[inv_num].append(os.path.relpath(fp, BASE_DIR))
    return index


# ---- 查重：按发票号索引 + 文件内容 MD5 双重校验 ----
def check_duplicates(rel_path):
    """检查单个文件是否与已有文件重复"""
    fp = os.path.join(BASE_DIR, rel_path)
    if not os.path.isfile(fp): return None

    inv_num = extract_invoice_number(fp)
    candidates = []

    # 1. 按发票号码查重
    if inv_num:
        index = _build_invoice_number_index()
        existing = index.get(inv_num, [])
        for ep in existing:
            if os.path.normcase(ep) != os.path.normcase(rel_path):
                candidates.append({'type': '发票号码相同', 'path': ep})

    # 2. 按文件内容 MD5 查重
    fp_hash = _file_hash(fp)
    if fp_hash:
        meta = load_meta()
        for mid, mdata in meta.items():
            existing_path = os.path.join(BASE_DIR, mdata.get('rel_path', mid))
            if os.path.isfile(existing_path) and os.path.normcase(existing_path) != os.path.normcase(fp):
                if _file_hash(existing_path) == fp_hash:
                    candidates.append({'type': '文件内容完全相同', 'path': mdata.get('rel_path', mid)})

    return candidates if candidates else None


# ===================== 发票扫描 =====================

# ---- 全量扫描：遍历文件夹并合并元数据 → 发票列表（元数据键做了反斜杠→正斜杠归一化）----
def scan_invoices(use_cache=True):
    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    invoices = []

    for entry in os.listdir(BASE_DIR):
        entry_path = os.path.join(BASE_DIR, entry)
        if not os.path.isdir(entry_path) or is_skipped(entry): continue

        if MONTH_PATTERN.match(entry):
            month = entry
            for status in STATUS_NAMES:
                status_dir = os.path.join(entry_path, status)
                if not os.path.isdir(status_dir): continue
                for f in os.listdir(status_dir):
                    if not f.lower().endswith('.pdf'): continue
                    _add_invoice(invoices, meta, month, status, os.path.join(status_dir, f), use_cache)
            for f in os.listdir(entry_path):
                fp = os.path.join(entry_path, f)
                if os.path.isfile(fp) and f.lower().endswith('.pdf'):
                    _add_invoice(invoices, meta, month, '未报销', fp, use_cache)
        else:
            for root, dirs, files in os.walk(entry_path):
                dirs[:] = [d for d in dirs if not is_skipped(d)]
                for f in files:
                    if not f.lower().endswith('.pdf'): continue
                    folder_hint = '已报' in entry
                    found_status = None
                    for mid, mdata in meta.items():
                        if mdata.get('filename') == f:
                            found_status = mdata.get('status'); break
                    if not found_status or found_status not in STATUS_NAMES:
                        found_status = '已报销' if folder_hint else '未报销'
                    m = re.match(r'^(\d{4}\.\d{1,2})', entry)
                    month = _normalize_month(m.group(1)) if m else entry
                    _add_invoice(invoices, meta, month, found_status, os.path.join(root, f), use_cache)

    seen = set()
    unique = []
    for inv in invoices:
        key = os.path.normcase(inv['rel_path'])
        if key not in seen:
            seen.add(key)
            unique.append(inv)
    unique.sort(key=lambda x: (x['month'], x.get('date', '')), reverse=True)
    if has_request_context():
        names = {row['id']: row['name'] for row in get_db().execute('SELECT id,name FROM members')}
        for invoice in unique:
            owner_id = invoice.get('submitter_id')
            if owner_id in names:
                invoice['submitter_original'] = invoice['submitter']
                invoice['submitter'] = names[owner_id]
    return unique


# ---- 单文件 → 发票字典（合并元数据字段，ID 使用正斜杠相对路径）----
def _add_invoice(invoices, meta, month, status, filepath, use_cache=True):
    filename = os.path.basename(filepath)
    rel_path = os.path.relpath(filepath, BASE_DIR).replace('\\', '/')
    inv_id = rel_path
    info = parse_invoice_filename(filename)
    if info is None: return
    inv_meta = meta.get(inv_id, {})
    final_status = inv_meta.get('status', status)
    if final_status not in STATUS_NAMES: final_status = status

    inv = {
        'id': inv_id, 'month': inv_meta.get('month', month),
        'folder': os.path.dirname(rel_path), 'rel_path': rel_path, 'filename': filename,
        'category': inv_meta.get('category_override') or info['category'],
        'amount': info['amount'], 'vendor': info['vendor'], 'date': inv_meta.get('date', info['date']),
        'seq': info['seq'], 'status': final_status,
        'submitter': inv_meta.get('submitter', ''), 'submitter_id': inv_meta.get('submitter_id'),
        'ownership_unresolved': not bool(inv_meta.get('submitter_id')),
        'notes': inv_meta.get('notes', ''),
        'from_filename': info['from_filename'], 'source': '',
        'reimbursed_date': inv_meta.get('reimbursed_date', ''),
        'tags': inv_meta.get('tags', []),
        'invoice_number': inv_meta.get('invoice_number', ''),
    }
    confirmed = inv_meta.get('confirmed_amount_cents')
    if confirmed is not None:
        if type(confirmed) is not int or not 0 <= confirmed <= MAX_CENTS:
            raise RuntimeError(f'发票确认金额分值无效：{inv_id}')
        inv['amount'], inv['source'] = Decimal(format_cents(confirmed)), '人工确认'
    else:
        inv['amount'], inv['source'] = resolve_amount(inv, use_cache=use_cache)
    inv['amount_cents'] = str(to_cents(inv['amount']))
    invoices.append(inv)


# ===================== 统计 =====================

# ---- 统计聚合：总数/总额/状态/提交人/类别/月份 ----
def get_stats(invoices):
    total_cents = 0
    groups = {'status_stats': {status: {'count': 0, 'cents': 0} for status in STATUS_NAMES},
              'submitter_stats': {}, 'category_stats': {}, 'month_stats': {}}
    for invoice in invoices:
        cents = to_cents(invoice['amount'])
        total_cents += cents
        submitter = (f"{invoice['submitter']} (#{invoice['submitter_id']})" if invoice.get('submitter_id')
                     else (invoice['submitter'] + '（待确认）' if invoice['submitter'] else '未指定'))
        labels = {'status_stats': invoice['status'], 'submitter_stats': submitter,
                  'category_stats': invoice['category'] or '未分类', 'month_stats': invoice['month']}
        for group, label in labels.items():
            bucket = groups[group].setdefault(label, {'count': 0, 'cents': 0})
            bucket['count'] += 1
            bucket['cents'] += cents
    for group in groups.values():
        for bucket in group.values():
            bucket.update(money_fields(bucket.pop('cents')))
    groups['month_stats'] = dict(sorted(groups['month_stats'].items(), reverse=True))
    return {'total_count': len(invoices), **money_fields(total_cents, 'total_amount'), **groups}


# ===================== 路由 =====================

@bp.route('/')
def index():
    user = current_user()
    # Jinja tojson protects the user name inside the inline app config script.
    return render_template("invoices.html", me=user, page="invoices",
                           role_names=ROLE_NAMES, is_finance=_is_finance(user))


# ==================== 列表 / 统计 / 提交人 ====================
@bp.route('/api/invoices')
# ---- 列表：成员仅返回自己的（服务端过滤 submitter）----
def api_invoices():
    user = current_user()
    invoices = scan_invoices()
    if not _is_finance(user):
        invoices = [i for i in invoices if _belongs_to_user(i, user)]
    result = invoices
    if request.args.get('ownership') == 'unresolved':
        result = [i for i in result if i['ownership_unresolved']]
    if request.args.get('submitter_id'):
        try:
            owner_id = int(request.args['submitter_id'])
            if not 0 <= owner_id <= 2**63 - 1:
                raise ValueError()
        except ValueError:
            return jsonify(error='提交人筛选参数无效'), 400
        result = [i for i in result if (i['submitter_id'] or 0) == owner_id]

    for key in ['month', 'category', 'status', 'submitter']:
        val = request.args.get(key, '').strip()
        if val: result = [i for i in result if i[key] == val]

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    if date_from: result = [i for i in result if i['date'] >= date_from]
    if date_to: result = [i for i in result if i['date'] <= date_to]

    search = request.args.get('search', '').lower()
    if search:
        result = [i for i in result if
                  search in i['vendor'].lower() or search in i['category'].lower() or
                  search in i['filename'].lower() or search in str(i['amount']) or
                  search in str(i.get('notes', '')).lower() or
                  search in str(i.get('submitter', '')).lower() or
                  search in str(i.get('invoice_number', '')).lower()]

    min_amt = request.args.get('min_amount', '')
    max_amt = request.args.get('max_amount', '')
    try:
        low = decimal_amount(min_amt) if min_amt else None
        high = decimal_amount(max_amt) if max_amt else None
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 50))
        if page < 1 or per_page < 1 or per_page > 100:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'error': '筛选或分页参数无效'}), 400
    if low is not None: result = [i for i in result if i['amount'] >= low]
    if high is not None: result = [i for i in result if i['amount'] <= high]

    inv_id = request.args.get('id', '')
    if inv_id: result = [i for i in result if i['id'] == inv_id]
    total = len(result)
    start = (page - 1) * per_page
    end = start + per_page
    return jsonify({'data': result[start:end], 'total': total, 'page': page, 'per_page': per_page})


@bp.route('/api/stats')
# ---- 统计：成员仅统计自己的 ----
def api_stats():
    user = current_user()
    invoices = scan_invoices()
    if not _is_finance(user):
        invoices = [i for i in invoices if _belongs_to_user(i, user)]
    return jsonify(get_stats(invoices))


@bp.route('/api/submitters')
# ---- 提交人下拉：成员只返回自己（财务返回全部）----
def api_submitters():
    user = current_user()
    if not _is_finance(user):
        return jsonify([user['name']])
    meta = load_meta()
    subs = set()
    for v in meta.values():
        s = v.get('submitter', '').strip()
        if s: subs.add(s)
    return jsonify(sorted(subs))


@bp.get('/api/owners')
@finance_required
def api_owners():
    return jsonify(members=[dict(row) for row in get_db().execute(
        'SELECT id,name,username,is_active,deleted_at FROM members ORDER BY name,id')])


@bp.route('/api/invoice/update', methods=['POST'])
@finance_required
# ---- 修改发票：支持人工校正月份/开票日期，同时保证文件路径和元数据同步 ----
def api_invoice_update():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(success=False, error='参数格式无效'), 400
    inv_id = data.get('id', '')
    if not isinstance(inv_id, str) or not _is_safe_rel(inv_id):
        return jsonify(success=False, error='无效发票 ID'), 400
    inv_id = inv_id.replace('\\', '/')
    source = _safe_join(inv_id)
    if not source or not os.path.isfile(source):
        return jsonify(success=False, error='发票文件不存在'), 404

    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    existing = meta.get(inv_id, {})
    parts = inv_id.split('/')
    old_month = existing.get('month') or parts[0]
    old_status = existing.get('status')
    if old_status not in STATUS_NAMES:
        old_status = parts[1] if len(parts) > 2 and parts[1] in STATUS_NAMES else '未报销'
    new_status = data.get('status', old_status)
    if new_status not in STATUS_NAMES:
        return jsonify(success=False, error='无效报销状态'), 400

    try:
        new_month = _validated_invoice_month(data['month']) if 'month' in data else old_month
        corrected_date = _validated_invoice_date(data['date']) if 'date' in data else None
        owner = (_resolve_submitter(data, existing)
                 if 'submitter_id' in data or 'submitter' in data else None)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400

    updated = dict(existing)
    if owner is not None:
        updated.update(submitter=owner[0], submitter_id=owner[1], ownership_source='manual')
    for field in ('notes', 'category_override', 'reimbursed_date', 'tags', 'invoice_number'):
        if field in data:
            updated[field] = data[field]
    if corrected_date is not None:
        updated['date'] = corrected_date
    updated['month'] = new_month
    updated['status'] = new_status
    updated['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # The month is also the on-disk directory. Do not leave a saved metadata
    # month pointing at the old directory; status changes use the same path.
    old_dir = os.path.dirname(inv_id).replace('\\', '/')
    target_dir_rel = f'{new_month}/{new_status}'
    move_needed = (('month' in data or 'status' in data) and old_dir != target_dir_rel)
    new_rel = inv_id
    dest = source
    if move_needed:
        directory = _safe_join(new_month, new_status)
        if not directory:
            return jsonify(success=False, error='月份目录无效'), 400
        try:
            ensure_dir(directory)
            dest = os.path.join(directory, os.path.basename(source))
            stem, ext = os.path.splitext(os.path.basename(source))
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(directory, f'{stem}_{counter}{ext}')
                counter += 1
            shutil.move(source, dest)
            new_rel = os.path.relpath(dest, BASE_DIR).replace('\\', '/')
        except OSError as exc:
            return jsonify(success=False, error=f'发票文件移动失败: {exc}'), 500

    updated['rel_path'] = new_rel
    updated['filename'] = os.path.basename(dest)
    new_meta = dict(meta)
    new_meta.pop(inv_id, None)
    new_meta[new_rel] = updated
    try:
        save_meta(new_meta)
    except Exception:
        if move_needed:
            try:
                shutil.move(dest, source)
            except OSError:
                logging.exception('Failed to roll back invoice move: %s', inv_id)
        raise
    if move_needed:
        _migrate_cache_key(inv_id, new_rel)
    return jsonify(success=True, moved=move_needed, id=new_rel, rel_path=new_rel)


@bp.route('/api/invoice/batch', methods=['POST'])
@finance_required
# ---- 批量修改发票（管理）----
def api_invoice_batch():
    data = request.get_json(force=True)
    ids = data.get('ids', [])
    updates = data.get('updates', {})
    allowed = {'status', 'submitter', 'submitter_id', 'notes', 'category_override',
               'reimbursed_date', 'tags', 'invoice_number'}
    if not isinstance(ids, list) or not isinstance(updates, dict) or not ids or len(ids) > 500:
        return jsonify({'success': False, 'error': '批量参数格式无效'}), 400
    if not updates or set(updates) - allowed:
        return jsonify({'success': False, 'error': '存在不允许修改的字段'}), 400
    if 'status' in updates and updates['status'] not in STATUS_NAMES:
        return jsonify({'success': False, 'error': '无效报销状态'}), 400
    if 'submitter' in updates or 'submitter_id' in updates:
        try:
            name, owner_id = _resolve_submitter(updates)
        except ValueError as exc:
            return jsonify(success=False, error=str(exc)), 400
        updates.update(submitter=name, submitter_id=owner_id, ownership_source='manual')

    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    moved_count = 0
    for inv_id in ids:
        if not isinstance(inv_id, str): continue
        inv_id = inv_id.replace('\\', '/')
        if not _is_safe_rel(inv_id) or not os.path.isfile(_safe_join(inv_id) or ''): continue
        if inv_id not in meta: meta[inv_id] = {}
        old_status = meta[inv_id].get('status', '')
        for field, value in updates.items(): meta[inv_id][field] = value
        meta[inv_id]['updated'] = now
        new_status = updates.get('status', old_status)
        if new_status != old_status and new_status in STATUS_NAMES:
            save_meta(meta)
            if move_invoice_file(inv_id, new_status): moved_count += 1
            meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    save_meta(meta)
    return jsonify({'success': True, 'count': len(ids), 'moved': moved_count})


# ---- 查重 API ----

# ---- 上传过程用的单文件查重接口 ----
@bp.route('/api/check-duplicate')
def api_check_duplicate():
    """检查指定文件是否与已有发票重复"""
    rel_path = request.args.get('rel_path', '')
    invoice_number = request.args.get('invoice_number', '')
    tmp_path = request.args.get('tmp_path', '')

    path = None
    if tmp_path:
        path = _own_temp_path(tmp_path)
        if path is None:
            return jsonify({'success': False, 'error': '无权限访问该临时文件'}), 403
    elif rel_path:
        if not _is_safe_rel(rel_path) or not _user_can_view(rel_path):
            return jsonify({'success': False, 'error': '无权限'}), 403
        path = _safe_join(rel_path)
    elif not tmp_path and not rel_path:
        return jsonify({'success': True, 'duplicates': []})

    duplicates = []
    if path and os.path.isfile(path):
        duplicates = check_duplicates(os.path.relpath(path, BASE_DIR)) or []

    # 同时按发票号码查
    if invoice_number:
        if len(invoice_number) > 64:
            return jsonify({'success': False, 'error': '发票号码过长'}), 400
        for entry in os.listdir(BASE_DIR):
            entry_path = os.path.join(BASE_DIR, entry)
            if not os.path.isdir(entry_path) or is_skipped(entry): continue
            for root, dirs, files in os.walk(entry_path):
                dirs[:] = [d for d in dirs if not is_skipped(d)]
                for f in files:
                    if not f.lower().endswith('.pdf'): continue
                    fp = os.path.join(root, f)
                    if extract_invoice_number(fp) == invoice_number:
                        rp = os.path.relpath(fp, BASE_DIR)
                        if rp not in [d['path'] for d in duplicates]:
                            duplicates.append({'type': '发票号码匹配', 'path': rp})

    return jsonify({'success': True, 'duplicates': _visible_duplicates(duplicates)})


# ---- 后台任务调度 ----

@bp.route('/api/job/start', methods=['POST'])
@finance_required
def api_job_start():
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or not isinstance(data.get('type'), str)
            or data['type'] not in invoice_jobs.JOB_TYPES):
        return jsonify(error='未知任务类型'), 400
    job, created = invoice_jobs.enqueue(data['type'], current_user())
    start_invoice_worker()
    _worker_wakeup.set()
    return jsonify(job_id=job['id'], status=job['status'], created=created), 202


@bp.route('/api/jobs')
@finance_required
def api_jobs():
    return jsonify(jobs=invoice_jobs.list_jobs())


@bp.route('/api/job/<job_id>')
@finance_required
def api_job_detail(job_id):
    job = invoice_jobs.get_job(job_id)
    if job is None:
        return jsonify(error='任务不存在'), 404
    return jsonify(job)


@bp.route('/api/job/stream/<job_id>')
@finance_required
def api_job_stream(job_id):
    if invoice_jobs.get_job(job_id) is None:
        return jsonify(error='任务不存在'), 404
    return _job_sse(job_id)


# ---- 后台任务实现 ----

def _do_scan_dups(jid):
    """后台：全库查重"""
    entries = [e for e in os.listdir(BASE_DIR)
               if os.path.isdir(os.path.join(BASE_DIR, e)) and not is_skipped(e)]
    total_pdfs = 0
    for entry in entries:
        for root, dirs, files in os.walk(os.path.join(BASE_DIR, entry)):
            dirs[:] = [d for d in dirs if not is_skipped(d)]
            total_pdfs += sum(1 for f in files if f.lower().endswith('.pdf'))

    total_checked = 0
    index = {}
    for entry in entries:
        entry_path = os.path.join(BASE_DIR, entry)
        for root, dirs, files in os.walk(entry_path):
            dirs[:] = [d for d in dirs if not is_skipped(d)]
            for f in files:
                if not f.lower().endswith('.pdf'): continue
                fp = os.path.join(root, f)
                inv_num = extract_invoice_number(fp)
                if inv_num:
                    if inv_num not in index: index[inv_num] = []
                    index[inv_num].append(os.path.relpath(fp, BASE_DIR))
                    total_checked += 1
                    if total_checked % 5 == 0:
                        _update_job(jid, min(int(total_checked / max(total_pdfs, 1) * 90), 90),
                                    f'已扫描 {total_checked} 个文件...')

    dup_groups = {k: v for k, v in index.items() if len(v) > 1}
    all_dup_paths = []
    for paths in dup_groups.values(): all_dup_paths.extend(paths)
    return {
        'duplicates': dup_groups,
        'dup_count': sum(len(v) - 1 for v in dup_groups.values()),
        'all_dup_paths': list(set(all_dup_paths)),
        'total_checked': total_checked,
    }


def _file_month(entry, filename, filepath):
    """确定发票所属月份: 文件名日期 > PDF 内容日期 > 文件夹名 > 未归类"""
    info = parse_invoice_filename(filename)
    month = ''
    if info and info.get('date'):
        month = _normalize_month(info['date'][:7].replace('-', '.'))
    if not month:
        try:
            pdf_info = extract_invoice_info(filepath)
            if pdf_info.get('date'):
                month = _normalize_month(pdf_info['date'][:7].replace('-', '.'))
        except Exception:
            pass
    if not month:
        m = re.match(r'^(\d{4}\.\d{1,2})', entry)
        if m: month = _normalize_month(m.group(1))
    if not month: month = '未归类'
    return month


# ---- "整理文件"核心实现：把散落 PDF 归档到 月份/状态 并标准化命名（带进度回调）----
def _organize_impl(progress_cb=None):
    results = {'moved': 0, 'skipped': 0, 'errors': 0, 'folders_created': [],
               'empty_dirs_removed': 0, 'details': []}
    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}

    entries = [e for e in sorted(os.listdir(BASE_DIR))
               if os.path.isdir(ep := os.path.join(BASE_DIR, e)) and not is_skipped(e)]
    total_entries = len(entries)
    for idx, entry in enumerate(entries):
        if progress_cb: progress_cb(idx, total_entries, entry)
        entry_path = os.path.join(BASE_DIR, entry)

        if MONTH_PATTERN.match(entry):
            month = entry
            loose = [f for f in os.listdir(entry_path)
                     if os.path.isfile(os.path.join(entry_path, f)) and f.lower().endswith('.pdf')]
            for f in loose:
                old_path = os.path.join(entry_path, f)
                month2 = _file_month(entry, f, old_path)
                inv_id = f"{entry}/{f}"
                inv_meta_data = meta.get(inv_id, {})
                status = inv_meta_data.get('status', '未报销')
                if status not in STATUS_NAMES: status = '未报销'
                target_dir = os.path.join(BASE_DIR, month2, status)
                ensure_dir(target_dir)
                if f"{month2}/{status}" not in results['folders_created']:
                    results['folders_created'].append(f"{month2}/{status}")
                new_path = os.path.join(target_dir, f)
                if os.path.normcase(old_path) == os.path.normcase(new_path):
                    results['skipped'] += 1
                else:
                    _do_move(results, meta, inv_id, old_path, new_path, month2, status)
            continue

        for root, dirs, files in os.walk(entry_path):
            dirs[:] = [d for d in dirs if not is_skipped(d)]
            for f in files:
                if not f.lower().endswith('.pdf'): continue
                old_path = os.path.join(root, f)
                month = _file_month(entry, f, old_path)
                inv_id = f"{entry}/{f}"
                inv_meta_data = meta.get(inv_id, {})
                status = inv_meta_data.get('status', '')
                if not status or status not in STATUS_NAMES:
                    status = '已报销' if '已报' in entry else '未报销'
                target_dir = os.path.join(BASE_DIR, month, status)
                ensure_dir(target_dir)
                folder_key = f"{month}/{status}"
                if folder_key not in results['folders_created']:
                    results['folders_created'].append(folder_key)
                new_path = os.path.join(target_dir, f)
                if os.path.normcase(old_path) == os.path.normcase(new_path):
                    results['skipped'] += 1
                    continue
                _do_move(results, meta, inv_id, old_path, new_path, month, status)

    save_meta(meta)

    if progress_cb: progress_cb(total_entries, total_entries, '清理空目录')
    for entry in sorted(os.listdir(BASE_DIR)):
        entry_path = os.path.join(BASE_DIR, entry)
        if not os.path.isdir(entry_path) or is_skipped(entry): continue
        if MONTH_PATTERN.match(entry): continue
        removed = _remove_empty_dirs(entry_path)
        results['empty_dirs_removed'] += removed
    return results


def _do_organize(jid):
    """后台：整理文件"""
    def cb(idx, total, entry):
        _update_job(jid, int((idx / max(total, 1)) * 80), f'处理文件夹: {entry}')
    return _organize_impl(cb)


def _do_refresh(jid):
    """后台：刷新PDF金额"""
    invoices = scan_invoices(use_cache=False)
    total = len(invoices)
    cache = {}
    pdf_count = 0
    for i, inv in enumerate(invoices):
        if inv['source'] == 'PDF':
            cache[inv['id']] = {'amount': inv['amount'], 'vendor': inv['vendor'],
                                'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            pdf_count += 1
        if (i + 1) % 5 == 0:
            _update_job(jid, int((i / max(total, 1)) * 90), f'已处理 {i + 1}/{total} 张...')
    save_cache(cache)
    return {'total': total, 'pdf_extracted': pdf_count}


def _do_delete_dups(jid):
    """后台：删除重复发票（每对保留第一个，删除其余的）"""
    _update_job(jid, 5, '扫描重复发票...')
    index = _build_invoice_number_index()
    dup_groups = {k: v for k, v in index.items() if len(v) > 1}

    deleted = []
    failed = []
    meta = load_meta()
    total_groups = len(dup_groups)

    _update_job(jid, 10, f'发现 {total_groups} 组重复，准备删除...')

    for gi, (inv_num, paths) in enumerate(dup_groups.items()):
        pct = 10 + int((gi / max(total_groups, 1)) * 85)
        _update_job(jid, pct, f'处理重复组 {gi + 1}/{total_groups}...')

        # 保留第一个，删除其余
        keep = paths[0]
        for dup_path in paths[1:]:
            fp = os.path.join(BASE_DIR, dup_path)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
                # 删除元数据
                meta.pop(dup_path, None)
                deleted.append(dup_path)
            except Exception as e:
                failed.append({'path': dup_path, 'error': str(e)})

    save_meta(meta)
    _update_job(jid, 95, '清理元数据...')
    return {
        'deleted': len(deleted),
        'failed': len(failed),
        'groups': total_groups,
        'deleted_paths': deleted[:50],
        'failed_details': failed,
    }


# ---- 保留同步版本接口（快速调用兼容） ----

@bp.route('/api/scan-duplicates')
@finance_required
def api_scan_duplicates():
    index = _build_invoice_number_index()
    dup_groups = {k: v for k, v in index.items() if len(v) > 1}
    all_dup_paths = []
    for paths in dup_groups.values(): all_dup_paths.extend(paths)
    return jsonify({
        'success': True, 'duplicates': dup_groups,
        'dup_count': sum(len(v) - 1 for v in dup_groups.values()),
        'all_dup_paths': list(set(all_dup_paths)),
        'message': f"查重完成：发现 {sum(len(v) - 1 for v in dup_groups.values())} 个重复项",
    })


# ---- 上传 API ----

@bp.route('/api/upload', methods=['POST'])
# ---- 上传（单文件）→ 解析 → 返回临时信息（尚未入库）----
def api_upload():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '没有上传文件'})

    file = request.files['file']
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'success': False, 'error': '仅支持PDF文件'})

    try:
        tmp_path = _save_temp_pdf(file)
    except ValueError as exc:
        return jsonify({'success': False, 'error': str(exc)}), 400
    safe_name = os.path.basename(tmp_path).split('_', 1)[-1]

    info = extract_invoice_info(tmp_path)
    fn_info = parse_invoice_filename(safe_name)
    if fn_info and fn_info.get('from_filename'):
        if not info['amount']: info['amount'] = fn_info['amount']
        if not info['date']: info['date'] = fn_info['date']
        if not info['vendor'] or info['vendor'] == safe_name: info['vendor'] = fn_info['vendor']
        if fn_info.get('category') and not info.get('category'): info['category'] = fn_info['category']

    # 查重
    duplicates = check_duplicates(os.path.relpath(tmp_path, BASE_DIR)) or []

    return jsonify({
        'success': True,
        'tmp_path': os.path.relpath(tmp_path, BASE_DIR),
        'original_name': file.filename,
        'info': info,
        'duplicates': _visible_duplicates(duplicates),
        'has_duplicate': len(duplicates) > 0,
    })


@bp.route('/api/upload/batch', methods=['POST'])
# ---- 批量上传解析（返回解析结果供确认表格）----
def api_upload_batch():
    """批量上传多个PDF，解析信息后返回列表"""
    files = request.files.getlist('files')
    if not files:
        return jsonify({'success': False, 'error': '没有上传文件'})

    results = []

    # 预建发票号码索引（一次扫描，全局复用）
    inv_index = _build_invoice_number_index()

    for file in files:
        if not file.filename.lower().endswith('.pdf'):
            results.append({'original_name': file.filename, 'error': '非PDF文件', 'success': False})
            continue

        try:
            tmp_path = _save_temp_pdf(file)
            safe_name = os.path.basename(tmp_path).split('_', 1)[-1]
        except (ValueError, OSError) as e:
            results.append({'original_name': file.filename, 'error': f'保存失败: {e}', 'success': False})
            continue

        try:
            info = extract_invoice_info(tmp_path)
        except Exception:
            info = {'amount': None, 'date': '', 'month': '', 'vendor': '', 'invoice_number': ''}

        fn_info = parse_invoice_filename(safe_name)
        if fn_info and fn_info.get('from_filename'):
            if not info['amount']: info['amount'] = fn_info['amount']
            if not info['date']: info['date'] = fn_info['date']
            if not info['vendor'] or info['vendor'] == safe_name: info['vendor'] = fn_info['vendor']

        # 快速查重（只用已缓存的发票号码索引，不读PDF）
        duplicates = []
        inv_num = info.get('invoice_number', '')
        if inv_num and inv_num in inv_index:
            duplicates = [{'type': '发票号码相同', 'path': p} for p in inv_index[inv_num]]

        results.append({
            'success': True,
            'tmp_path': os.path.relpath(tmp_path, BASE_DIR),
            'original_name': file.filename,
            'info': info,
            'duplicates': _visible_duplicates(duplicates),
            'has_duplicate': len(duplicates) > 0,
        })

    return jsonify({'success': True, 'results': results, 'total': len(results)})


@bp.route('/api/upload/cleanup', methods=['POST'])
def api_upload_cleanup():
    """删除被放弃的临时上传文件(如用户取消入库)"""
    data = request.get_json(silent=True) or {}
    paths = data.get('tmp_paths', [])
    if not isinstance(paths, list):
        return jsonify({'success': False, 'error': '临时文件列表无效'}), 400
    removed = 0
    for p in paths:
        fp = _own_temp_path(p)
        if fp:
            try:
                os.remove(fp)
                removed += 1
            except OSError:
                pass
    return jsonify({'success': True, 'removed': removed})


@bp.route('/api/upload/confirm', methods=['POST'])
# ---- 入库确认：成员提交人/状态被服务端强制为 本人 / 未报销 ----
def api_upload_confirm():
    data = money_json()
    tmp_rel = data.get('tmp_path', '')
    tmp_path = _own_temp_path(tmp_rel)
    if not tmp_path:
        return jsonify({'success': False, 'error': '临时文件不存在或不属于当前账号'}), 400
    month = _normalize_month(data.get('month', ''))
    status = data.get('status', '未报销')
    if _is_finance():
        try:
            submitter, submitter_id = _resolve_submitter(data)
        except ValueError as exc:
            return jsonify(success=False, error=str(exc)), 400
    else:
        status = '未报销'
        submitter = current_user()['name']
        submitter_id = current_user()['id']
    category = data.get('category', '')
    notes = data.get('notes', '').strip()
    amount = _valid_amount(data.get('amount', 0))
    if amount is None:
        return jsonify({'success': False, 'error': '金额必须是范围内的非负数，按分四舍五入'}), 400
    vendor = data.get('vendor', '')
    original_name = data.get('original_name', '')
    invoice_number = data.get('invoice_number', '')
    skip_duplicate_check = data.get('skip_duplicate_check', False)

    if not tmp_path or not os.path.isfile(tmp_path):
        return jsonify({'success': False, 'error': '临时文件不存在'})
    if not month:
        return jsonify({'success': False, 'error': '缺少月份信息'})
    if month != '未归类' and not MONTH_PATTERN.match(month):
        return jsonify({'success': False, 'error': '月份格式无效'}), 400
    if status not in STATUS_NAMES:
        status = '未报销'
    try:
        corrected_date = _validated_invoice_date(data['date']) if 'date' in data else None
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400

    # 查重保护
    if not skip_duplicate_check:
        dups = check_duplicates(os.path.relpath(tmp_path, BASE_DIR)) or []
        if dups:
            return jsonify({
                'success': False, 'error': '存在重复发票',
                'duplicates': dups, 'code': 'DUPLICATE',
            })

    target_dir = os.path.join(BASE_DIR, month, status)
    ensure_dir(target_dir)

    # 使用标准化命名
    new_name = _generate_standard_name(tmp_path, confirmed_amount=amount)
    new_path = os.path.join(target_dir, new_name)
    counter = 1
    while os.path.exists(new_path):
        name, ext = os.path.splitext(new_name)
        new_path = os.path.join(target_dir, f"{name}_{counter}{ext}")
        counter += 1

    try:
        shutil.move(tmp_path, new_path)
    except Exception as e:
        return jsonify({'success': False, 'error': f'文件移动失败: {e}'})

    rel_path = os.path.relpath(new_path, BASE_DIR).replace('\\', '/')
    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    meta[rel_path] = {
        'rel_path': rel_path, 'month': month, 'filename': os.path.basename(new_path),
        'status': status, 'submitter': submitter, 'submitter_id': submitter_id, 'notes': notes,
        'category_override': category, 'reimbursed_date': '',
        'confirmed_amount_cents': to_cents(amount),
        'invoice_number': invoice_number, 'tags': [],
        'uploaded': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    if corrected_date is not None:
        meta[rel_path]['date'] = corrected_date
    save_meta(meta)

    if amount > 0:
        cache = load_cache()
        cache[rel_path] = {'amount': amount, 'vendor': vendor,
                           'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        save_cache(cache)

    return jsonify({'success': True, 'rel_path': rel_path, 'message': f'已入库: {os.path.basename(new_path)}',
                    **money_fields(to_cents(amount))})


@bp.route('/api/upload/confirm/batch', methods=['POST'])
# ---- 批量入库确认（同样强制提交人/状态规则）----
def api_upload_confirm_batch():
    """批量确认上传"""
    data = money_json()
    items = data.get('items', [])
    if not isinstance(items, list) or len(items) > 200:
        return jsonify({'success': False, 'error': '批次格式无效或超过 200 张'}), 400
    if not items:
        return jsonify({'success': False, 'error': '没有待入库的发票'})

    results = []
    is_fin = _is_finance()
    cur_name = current_user()['name']
    for item in items:
        if not isinstance(item, dict):
            results.append({'file': '', 'status': 'error', 'reason': '条目格式错误'})
            continue
        tmp_rel = item.get('tmp_path', '')
        tmp_path = _own_temp_path(tmp_rel)
        month = _normalize_month(item.get('month', ''))
        status = item.get('status', '未报销')
        if is_fin:
            try:
                submitter, submitter_id = _resolve_submitter(item)
            except ValueError as exc:
                results.append({'file': item.get('original_name', ''), 'status': 'error', 'reason': str(exc)})
                continue
        else:
            status = '未报销'
            submitter = cur_name
            submitter_id = current_user()['id']
        category = item.get('category', '')
        notes = item.get('notes', '').strip()
        amount = _valid_amount(item.get('amount', 0))
        if amount is None:
            results.append({'file': item.get('original_name', ''), 'status': 'error', 'reason': '金额无效'})
            continue
        vendor = item.get('vendor', '')
        original_name = item.get('original_name', '')
        invoice_number = item.get('invoice_number', '')

        if not tmp_path or not os.path.isfile(tmp_path):
            results.append({'file': original_name, 'status': 'error', 'reason': '临时文件不存在'})
            continue
        if not month:
            results.append({'file': original_name, 'status': 'error', 'reason': '缺少月份'})
            continue
        if month != '未归类' and not MONTH_PATTERN.match(month):
            results.append({'file': original_name, 'status': 'error', 'reason': '月份格式无效'})
            continue
        if status not in STATUS_NAMES:
            status = '未报销'
        try:
            corrected_date = _validated_invoice_date(item['date']) if 'date' in item else None
        except ValueError as exc:
            results.append({'file': original_name, 'status': 'error', 'reason': str(exc)})
            continue

        target_dir = os.path.join(BASE_DIR, month, status)
        ensure_dir(target_dir)

        new_name = _generate_standard_name(tmp_path, confirmed_amount=amount)
        new_path = os.path.join(target_dir, new_name)
        counter = 1
        while os.path.exists(new_path):
            name, ext = os.path.splitext(new_name)
            new_path = os.path.join(target_dir, f"{name}_{counter}{ext}")
            counter += 1

        try:
            shutil.move(tmp_path, new_path)
            rel_path = os.path.relpath(new_path, BASE_DIR).replace('\\', '/')
            meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
            meta[rel_path] = {
                'rel_path': rel_path, 'month': month, 'filename': os.path.basename(new_path),
                'status': status, 'submitter': submitter, 'submitter_id': submitter_id, 'notes': notes,
                'category_override': category, 'reimbursed_date': '',
                'confirmed_amount_cents': to_cents(amount),
                'invoice_number': invoice_number, 'tags': [],
                'uploaded': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            if corrected_date is not None:
                meta[rel_path]['date'] = corrected_date
            save_meta(meta)
            if amount > 0:
                cache = load_cache()
                cache[rel_path] = {'amount': amount, 'vendor': vendor, 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                save_cache(cache)
            results.append({'file': original_name, 'status': 'ok', 'path': rel_path,
                            **money_fields(to_cents(amount))})
        except Exception as e:
            results.append({'file': original_name, 'status': 'error', 'reason': str(e)})

    ok_count = sum(1 for r in results if r['status'] == 'ok')
    err_count = sum(1 for r in results if r['status'] == 'error')
    for item, result in zip(items, results):
        result['tmp_path'] = item.get('tmp_path', '') if isinstance(item, dict) else ''
    return jsonify({
        'success': True,
        'results': results,
        'ok': ok_count,
        'error': err_count,
        'message': f'入库完成：{ok_count} 成功，{err_count} 失败',
    })


# ---- 整理 ----

@bp.route('/api/organize', methods=['POST'])
@finance_required
# ---- 整理文件（管理，后台任务）----
def api_organize():
    results = organize_all_files()
    msg = (f"整理完成：移动 {results['moved']} 个文件，"
           f"跳过 {results['skipped']} 个，"
           f"创建 {len(results['folders_created'])} 个文件夹，"
           f"清理 {results['empty_dirs_removed']} 个空目录"
           + (f"，{results['errors']} 个失败" if results['errors'] else ""))
    return jsonify({'success': True, 'results': results, 'message': msg})


@bp.route('/api/organize/preview')
@finance_required
# ---- 整理预览：返回将被归档的文件列表（前端确认后执行）----
def api_organize_preview():
    to_move = []
    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    for entry in os.listdir(BASE_DIR):
        entry_path = os.path.join(BASE_DIR, entry)
        if not os.path.isdir(entry_path) or is_skipped(entry): continue
        if MONTH_PATTERN.match(entry):
            for f in os.listdir(entry_path):
                fp = os.path.join(entry_path, f)
                if os.path.isfile(fp) and f.lower().endswith('.pdf'):
                    month2 = _file_month(entry, f, fp)
                    inv_id = f"{entry}/{f}"
                    inv_meta_data = meta.get(inv_id, {})
                    status = inv_meta_data.get('status', '未报销')
                    if status not in STATUS_NAMES: status = '未报销'
                    to_move.append({'from': os.path.relpath(fp, BASE_DIR),
                                    'to': f"{month2}/{status}/{f}", 'month': month2, 'status': status})
            continue
        for root, dirs, files in os.walk(entry_path):
            dirs[:] = [d for d in dirs if not is_skipped(d)]
            for f in files:
                if not f.lower().endswith('.pdf'): continue
                old_path = os.path.join(root, f)
                month = _file_month(entry, f, old_path)
                inv_id = f"{entry}/{f}"
                inv_meta_data = meta.get(inv_id, {})
                status = inv_meta_data.get('status', '')
                if not status or status not in STATUS_NAMES:
                    status = '已报销' if '已报' in entry else '未报销'
                to_move.append({'from': os.path.relpath(old_path, BASE_DIR),
                                'to': f"{month}/{status}/{f}", 'month': month, 'status': status})
    months = sorted(set(f['month'] for f in to_move))
    return jsonify({'count': len(to_move), 'files': to_move[:200], 'months': months})


# ---- 其他 ----

@bp.route('/api/refresh', methods=['POST'])
@finance_required
# ---- 刷新 PDF 金额（管理，后台任务）----
def api_refresh():
    invoices = scan_invoices(use_cache=False)
    cache = {}
    for inv in invoices:
        if inv['source'] == 'PDF':
            cache[inv['id']] = {'amount': inv['amount'], 'vendor': inv['vendor'],
                                'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    save_cache(cache)
    pdf_count = sum(1 for i in invoices if i['source'] == 'PDF')
    return jsonify({'success': True, 'total': len(invoices), 'pdf_extracted': pdf_count,
                    'message': f"刷新完成：共 {len(invoices)} 张，{pdf_count} 张从PDF提取金额"})


@bp.route('/api/open_file')
# ---- 打开文件：返回浏览器预览 URL（成员需为提交人本人）----
def api_open_file():
    rel_path = request.args.get('rel_path', '')
    fp = _safe_join(rel_path) if rel_path.lower().endswith('.pdf') and _is_safe_rel(rel_path) else None
    if fp and os.path.isfile(fp):
        if not _user_can_view(rel_path):
            return jsonify({'success': False, 'error': '无权限'}), 403
        normalized = rel_path.replace('\\', '/')
        url = '/invoices/file/' + '/'.join(quote(seg) for seg in normalized.split('/') if seg)
        return jsonify({'success': True, 'url': url})
    return jsonify({'success': False, 'error': '文件不存在'}), 404


@bp.route('/api/open_folder')
@finance_required
# ---- 打开文件夹：服务端部署不支持（保留接口，返回提示）----
def api_open_folder():
    return jsonify({'success': False, 'error': '服务端部署不支持直接打开文件夹'})


@bp.route('/api/export', methods=['POST'])
@finance_required
# ---- 导出 CSV（管理）----
def api_export():
    invoices = scan_invoices()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['月份', '文件夹', '状态', '提交人', '序号', '类别', '金额', '金额来源',
                     '供应商', '开票日期', '报销日期', '备注', '发票号码', '文件名', '文件路径'])
    for i in invoices:
        row = [i['month'], i['folder'], i['status'], i.get('submitter', ''),
               i['seq'], i['category'], str(i['amount']), i['source'],
               i['vendor'], i['date'], i.get('reimbursed_date', ''),
               i.get('notes', ''), i.get('invoice_number', ''),
               i['filename'], i.get('rel_path', '')]
        writer.writerow([safe_csv_cell(value) for value in row])
    csv_path = os.path.join(BASE_DIR, '发票汇总.csv')
    fd, tmp_path = tempfile.mkstemp(prefix='.invoice-export-', suffix='.tmp', dir=BASE_DIR)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as stream:
            stream.write(output.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, csv_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return jsonify({'success': True, 'path': csv_path, 'count': len(invoices)})


@bp.route('/api/export/download')
@finance_required
def api_export_download():
    """真正下载 CSV，不再只把文件生成到服务器目录。"""
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['月份', '文件夹', '状态', '提交人', '序号', '类别', '金额', '金额来源',
                     '供应商', '开票日期', '报销日期', '备注', '发票号码', '文件名', '文件路径'])
    for inv in scan_invoices():
        writer.writerow([safe_csv_cell(value) for value in (
            inv.get('month', ''), inv.get('folder', ''), inv.get('status', ''),
            inv.get('submitter', ''), inv.get('seq', ''), inv.get('category', ''),
            inv.get('amount', 0), inv.get('source', ''), inv.get('vendor', ''),
            inv.get('date', ''), inv.get('reimbursed_date', ''), inv.get('notes', ''),
            inv.get('invoice_number', ''), inv.get('filename', ''), inv.get('rel_path', ''),
        )])
    buffer = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    buffer.seek(0)
    return send_file(buffer, mimetype='text/csv; charset=utf-8', as_attachment=True,
                     download_name='invoices_' + datetime.now().strftime('%Y%m%d') + '.csv')


# ---- 打包发票 zip：排除 .uploads / __pycache__ / 往届发票.zip ----
def build_invoices_zip(out_path):
    with _invoice_lock:
        return _build_invoices_zip(out_path)


def _build_invoices_zip(out_path):
    skipped_dirs = {'.uploads', '__pycache__', '_待清理'}
    skipped_files = {'往届发票.zip', '.invoice.lock'}
    target_path = os.path.realpath(out_path) if isinstance(out_path, (str, os.PathLike)) else None
    count = 0
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in skipped_dirs]
            for name in files:
                if name in skipped_files:
                    continue
                path = os.path.join(root, name)
                if target_path and os.path.realpath(path) == target_path:
                    continue
                z.write(path, os.path.relpath(path, BASE_DIR))
                count += 1
    return count


@bp.route('/api/export_zip')
@finance_required
# ---- 导出压缩包：生成到 /tmp 并作为附件下载（管理）----
def api_export_zip():
    # Each response owns its file, so a later export cannot overwrite a download.
    out = tempfile.TemporaryFile()
    try:
        build_invoices_zip(out)
        out.seek(0)
        stamp = datetime.now().strftime('%Y%m%d')
        response = send_file(out, mimetype='application/zip', as_attachment=True,
                             download_name=f'invoices_{stamp}.zip')
        response.call_on_close(out.close)
        return response
    except BaseException:
        out.close()
        raise


# ===================== 前端 HTML =====================

# ==================== 前端整页 HTML（内嵌 CSS/JS；/*__APP_CONFIG__*/ 为角色注入点）====================
def create_index_html():
    """从独立 HTML 文件读取前端，保留角色配置注入。"""
    html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "templates", "invoices.html")
    with open(html_path, encoding="utf-8") as file:
        return file.read()



# ===================== 集成适配 =====================


@bp.before_request
# ---- 模块守卫：登录即可访问（管理接口与数据级权限在各自位置控制）----
def _invoices_guard():
    user = current_user()
    if user is None:
        if '/api/' in request.path:
            return jsonify({'success': False, 'error': '未登录'}), 401
        return redirect('/login')
    start_invoice_worker()
    if request.endpoint not in {'invoices.index', 'invoices.api_job_stream', 'invoices.api_job_start',
                                'invoices.api_job_detail', 'invoices.api_jobs'}:
        _invoice_lock.acquire()
        g.invoice_lock_held = True
    return None


@bp.teardown_request
def _release_invoice_lock(exc):
    if getattr(g, 'invoice_lock_held', False):
        g.invoice_lock_held = False
        _invoice_lock.release()


@bp.route('/file/<path:rel>')
# ---- 文件下发：/invoices/file/<path>（成员仅能取自己的发票）----
def invoice_file(rel):
    if not _is_safe_rel(rel) or not rel.lower().endswith('.pdf'):
        return '文件不存在', 404
    fp = _safe_join(rel)
    if not fp or not os.path.isfile(fp):
        return '文件不存在', 404
    if not _user_can_view(rel):
        return '无权限', 403
    return send_file(fp)


# ---- 启动迁移：把 Windows 时代元数据/缓存中的反斜杠键统一为正斜杠 ----
def _normalize_windows_keys():
    meta = load_meta()
    norm = {}
    changed = False
    for key, value in meta.items():
        nk = key.replace('\\', '/')
        if nk != key:
            changed = True
        norm[nk] = value
    if changed:
        save_meta(norm)
    cache = load_cache()
    norm = {}
    changed = False
    for key, value in cache.items():
        nk = key.replace('\\', '/')
        if nk != key:
            changed = True
        norm[nk] = value
    if changed:
        save_cache(norm)


# ---- 启动文件维护也必须与其他进程共享同一把锁 ----
with _invoice_lock:
    _normalize_windows_keys()
    _migrate_cache_amounts()
    build_folder_structure()
    ensure_dir(UPLOAD_DIR)
    cleanup_uploads()
