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
      - finance_required：导出/整理/查重/批量/删除等管理接口仅 admin/manager
      - 数据级：成员在列表/统计/预览接口中按 submitter==本人 过滤
  [5] 上传入库时服务端强制 submitter=登录人、status=未报销（防伪造）
  [6] 元数据键统一用正斜杠（兼容从 Windows 迁移的老数据，启动时自动归一化）
  [7] 后台任务(SSE 实时进度)依赖进程内内存 _jobs —— 因此 gunicorn 必须固定 1 worker
  [8] 新增 /api/export_zip：一键打包全部发票下载（排除 .uploads、往届发票.zip）

前端：create_index_html() 内嵌整页 HTML/CSS/JS（见文件后半部分）；
      页面通过 /*__APP_CONFIG__*/ 注入 window.IS_FINANCE / window.ME_NAME
      来区分"成员视角"（上传+只看自己的）与"财务视角"（全量管理）。
"""
import os
import re
import json
import shutil
import hashlib
import uuid
import time
import threading
import tempfile
import zipfile
from datetime import datetime
from functools import wraps
from flask import (Blueprint, Response, jsonify, redirect, request,
                   send_file, send_from_directory, stream_with_context)
from urllib.parse import quote

from common import current_user

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


# ---- 角色判断：是否管理员/项管（即发票模块的"财务视角"）----
def _is_finance(user=None):
    user = user or current_user()
    return bool(user) and user['role'] in ('admin', 'manager')


# ---- 装饰器：仅管理员/项管可调用（导出/整理/查重/批量/维护类接口）----
def finance_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _is_finance():
            return jsonify({'success': False, 'error': '无权限'}), 403
        return view(*args, **kwargs)
    return wrapper


# ---- 数据级权限：普通成员仅能查看/预览 submitter 为自己 的发票文件 ----
def _user_can_view(rel_path):
    user = current_user()
    if _is_finance(user):
        return True
    rel = (rel_path or '').replace('\\', '/')
    meta = load_meta()
    for key, value in meta.items():
        if key.replace('\\', '/') == rel and value.get('submitter', '').strip() == user['name']:
            return True
    return False

# ========== 后台任务管理 ==========
_jobs = {}  # {job_id: {status, progress, message, result, ...}}
_jobs_lock = threading.Lock()


# ===================== 工具 =====================

# ========== 后台任务 ==========

def _create_job(job_type):
    jid = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[jid] = {'type': job_type, 'status': 'running', 'progress': 0, 'message': '初始化...', 'result': None}
    return jid


def _update_job(jid, progress, message):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]['progress'] = progress
            _jobs[jid]['message'] = message


def _finish_job(jid, result):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]['status'] = 'done'
            _jobs[jid]['progress'] = 100
            _jobs[jid]['message'] = '完成'
            _jobs[jid]['result'] = result
            _jobs[jid]['finished_at'] = time.time()


def _fail_job(jid, error):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid]['status'] = 'error'
            _jobs[jid]['message'] = str(error)
            _jobs[jid]['finished_at'] = time.time()


def _cleanup_jobs(max_age=1800):
    """清理完成超过 max_age 秒的任务, 防止内存无限增长"""
    now = time.time()
    with _jobs_lock:
        for jid in [j for j, v in _jobs.items() if v.get('finished_at') and now - v['finished_at'] > max_age]:
            _jobs.pop(jid, None)


def _job_sse(jid):
    """生成 SSE 事件流"""
    def generate():
        last_progress = -1
        while True:
            with _jobs_lock:
                job = _jobs.get(jid, {})
            status = job.get('status', 'running')
            progress = job.get('progress', 0)
            message = job.get('message', '')
            if progress != last_progress or status in ('done', 'error'):
                payload = {'progress': progress, 'message': message, 'status': status}
                if status in ('done', 'error'):
                    payload['result'] = job.get('result')
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_progress = progress
            if status in ('done', 'error'):
                _cleanup_jobs()
                break
            time.sleep(0.3)
    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _run_job_thread(jid, func, *args, **kwargs):
    """在后台线程运行 func(jid, *args, **kwargs)"""
    def _run():
        try:
            result = func(jid, *args, **kwargs)
            _finish_job(jid, result)
        except Exception as e:
            _fail_job(jid, e)
    threading.Thread(target=_run, daemon=True).start()


def _normalize_month(s):
    if not s: return s
    parts = s.split('.')
    if len(parts) == 2:
        try: return f"{parts[0]}.{int(parts[1])}"
        except ValueError: return s
    return s


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
def load_json(path, default=None):
    if default is None: default = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: return default
    return default


def save_json(path, data):
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try: os.replace(tmp, path)
        except PermissionError: shutil.move(tmp, path)
    except Exception as e:
        print(f'[save error] {path}: {e}')
        try: shutil.move(tmp, path)
        except: pass


load_meta = lambda: load_json(META_FILE)
save_meta = lambda d: save_json(META_FILE, d)
load_cache = lambda: load_json(CACHE_FILE)
save_cache = lambda d: save_json(CACHE_FILE, d)


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
    for f in os.listdir(UPLOAD_DIR):
        if f == '_待清理':
            continue
        fp = os.path.join(UPLOAD_DIR, f)
        if not os.path.isfile(fp):
            continue
        if os.path.getmtime(fp) < cutoff:
            try:
                shutil.move(fp, os.path.join(quarantine, f))
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
            try: return float(m.group(1).replace(',', ''))
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
            try: result['amount'] = float(m.group(1).replace(',', '')); break
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
                'category': m.group(2), 'amount': float(m.group(3).replace(',', '')),
                'vendor': m.group(4), 'date': m.group(6).replace('.', '-'), 'from_filename': True}
    m2 = re.search(r'([\d,.]+)元', name)
    if m2:
        return {'seq': '', 'category': '', 'amount': float(m2.group(1).replace(',', '')),
                'vendor': name, 'date': '', 'from_filename': True}
    return {'seq': '', 'category': '', 'amount': 0.0, 'vendor': name, 'date': '', 'from_filename': False}


# ---- 金额取值优先级：文件名 → 金额缓存 → 实时解析 PDF ----
def resolve_amount(inv, use_cache=True):
    cache = load_cache() if use_cache else {}
    cache = {k.replace('\\', '/'): v for k, v in cache.items()}
    key = inv['id'].replace('\\', '/')
    if inv.get('from_filename') and inv['amount'] > 0:
        return inv['amount'], '文件名'
    if key in cache and cache[key].get('amount'):
        return cache[key]['amount'], 'PDF(缓存)'
    filepath = os.path.join(BASE_DIR, inv.get('rel_path', ''))
    if os.path.isfile(filepath):
        pdf_amount = extract_amount_from_pdf(filepath)
        if pdf_amount is not None:
            cache[key] = {'amount': pdf_amount, 'vendor': inv['vendor'],
                          'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            save_cache(cache)
            return pdf_amount, 'PDF'
    return inv.get('amount', 0.0), '未知'


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
        meta[inv_id] = inv_meta
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
def _generate_standard_name(filepath):
    """根据PDF内容和文件名生成标准化名称: {category}-{amount}元-{vendor}-{inv_num}-{date}-发票.pdf"""
    info = extract_invoice_info(filepath)
    fn_info = parse_invoice_filename(os.path.basename(filepath))

    # 金额：优先文件名，其次PDF内容
    amount = 0.0
    if fn_info and fn_info.get('from_filename') and fn_info.get('amount'):
        amount = fn_info['amount']
    if not amount and info.get('amount'):
        amount = info['amount']

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
    if amount > 0: parts.append(f'{amount}元')
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
                    _add_invoice(invoices, meta, month, status, os.path.join(status_dir, f))
            for f in os.listdir(entry_path):
                fp = os.path.join(entry_path, f)
                if os.path.isfile(fp) and f.lower().endswith('.pdf'):
                    _add_invoice(invoices, meta, month, '未报销', fp)
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
                    _add_invoice(invoices, meta, month, found_status, os.path.join(root, f))

    seen = set()
    unique = []
    for inv in invoices:
        key = os.path.normcase(inv['rel_path'])
        if key not in seen:
            seen.add(key)
            unique.append(inv)
    unique.sort(key=lambda x: (x['month'], x.get('date', '')), reverse=True)
    return unique


# ---- 单文件 → 发票字典（合并元数据字段，ID 使用正斜杠相对路径）----
def _add_invoice(invoices, meta, month, status, filepath):
    filename = os.path.basename(filepath)
    rel_path = os.path.relpath(filepath, BASE_DIR)
    inv_id = rel_path.replace('\\', '/')
    info = parse_invoice_filename(filename)
    if info is None: return
    inv_meta = meta.get(inv_id, {})
    final_status = inv_meta.get('status', status)
    if final_status not in STATUS_NAMES: final_status = status

    inv = {
        'id': inv_id, 'month': inv_meta.get('month', month),
        'folder': os.path.dirname(rel_path), 'rel_path': rel_path, 'filename': filename,
        'category': inv_meta.get('category_override') or info['category'],
        'amount': info['amount'], 'vendor': info['vendor'], 'date': info['date'],
        'seq': info['seq'], 'status': final_status,
        'submitter': inv_meta.get('submitter', ''), 'notes': inv_meta.get('notes', ''),
        'from_filename': info['from_filename'], 'source': '',
        'reimbursed_date': inv_meta.get('reimbursed_date', ''),
        'tags': inv_meta.get('tags', []),
        'invoice_number': inv_meta.get('invoice_number', ''),
    }
    inv['amount'], inv['source'] = resolve_amount(inv, use_cache=True)
    invoices.append(inv)


# ===================== 统计 =====================

# ---- 统计聚合：总数/总额/状态/提交人/类别/月份 ----
def get_stats(invoices):
    total = len(invoices)
    total_amount = sum(i['amount'] for i in invoices)

    status_stats = {}
    for s in STATUS_NAMES:
        subset = [i for i in invoices if i['status'] == s]
        status_stats[s] = {'count': len(subset), 'amount': round(sum(i['amount'] for i in subset), 2)}

    submitter_stats = {}
    for i in invoices:
        s = i['submitter'] or '未指定'
        if s not in submitter_stats: submitter_stats[s] = {'count': 0, 'amount': 0.0}
        submitter_stats[s]['count'] += 1
        submitter_stats[s]['amount'] += i['amount']

    category_stats = {}
    for i in invoices:
        c = i['category'] or '未分类'
        if c not in category_stats: category_stats[c] = {'count': 0, 'amount': 0.0}
        category_stats[c]['count'] += 1
        category_stats[c]['amount'] += i['amount']

    month_stats = {}
    for i in invoices:
        m = i['month']
        if m not in month_stats: month_stats[m] = {'count': 0, 'amount': 0.0}
        month_stats[m]['count'] += 1
        month_stats[m]['amount'] += i['amount']

    return {
        'total_count': total, 'total_amount': round(total_amount, 2),
        'status_stats': status_stats, 'submitter_stats': submitter_stats,
        'category_stats': category_stats,
        'month_stats': dict(sorted(month_stats.items(), reverse=True)),
    }


# ===================== 路由 =====================

@bp.route('/')
def index():
    user = current_user()
    html = create_index_html()
    html = html.replace(
        '/*__APP_CONFIG__*/',
        'window.IS_FINANCE=%s;window.ME_NAME=%s;' % (
            'true' if _is_finance(user) else 'false',
            json.dumps(user['name'], ensure_ascii=False),
        ),
    )
    return Response(html, mimetype='text/html')


# ==================== 列表 / 统计 / 提交人 ====================
@bp.route('/api/invoices')
# ---- 列表：成员仅返回自己的（服务端过滤 submitter）----
def api_invoices():
    user = current_user()
    invoices = scan_invoices()
    if not _is_finance(user):
        invoices = [i for i in invoices if i.get('submitter') == user['name']]
    result = invoices

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
    if min_amt: result = [i for i in result if i['amount'] >= float(min_amt)]
    if max_amt: result = [i for i in result if i['amount'] <= float(max_amt)]

    inv_id = request.args.get('id', '')
    if inv_id: result = [i for i in result if i['id'] == inv_id]

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
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
        invoices = [i for i in invoices if i.get('submitter') == user['name']]
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


@bp.route('/api/invoice/update', methods=['POST'])
@finance_required
# ---- 修改发票：状态/提交人/备注/类别/报销日期/标签/发票号（管理）----
def api_invoice_update():
    data = request.get_json(force=True)
    inv_id = data.get('id', '').replace('\\', '/')
    if not inv_id: return jsonify({'success': False, 'error': '缺少 id'})
    if not _is_safe_rel(inv_id): return jsonify({'success': False, 'error': '非法 id'}), 400

    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    if inv_id not in meta: meta[inv_id] = {}

    old_status = meta[inv_id].get('status', '')
    new_status = data.get('status', old_status)

    for field in ['status', 'submitter', 'notes', 'category_override', 'reimbursed_date', 'tags', 'invoice_number']:
        if field in data: meta[inv_id][field] = data[field]
    meta[inv_id]['updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_meta(meta)

    moved = False
    if new_status != old_status and new_status in STATUS_NAMES:
        moved = move_invoice_file(inv_id, new_status)

    return jsonify({'success': True, 'moved': moved})


@bp.route('/api/invoice/batch', methods=['POST'])
@finance_required
# ---- 批量修改发票（管理）----
def api_invoice_batch():
    data = request.get_json(force=True)
    ids = data.get('ids', [])
    updates = data.get('updates', {})
    if not ids or not updates: return jsonify({'success': False, 'error': '缺少参数'})

    meta = {k.replace('\\', '/'): v for k, v in load_meta().items()}
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    moved_count = 0
    for inv_id in ids:
        if not isinstance(inv_id, str): continue
        inv_id = inv_id.replace('\\', '/')
        if not _is_safe_rel(inv_id): continue
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
    if tmp_path and _is_safe_rel(tmp_path):
        path = _safe_join(tmp_path)
    elif rel_path and _is_safe_rel(rel_path):
        path = _safe_join(rel_path)
    elif not tmp_path and not rel_path:
        return jsonify({'success': True, 'duplicates': []})

    duplicates = []
    if path and os.path.isfile(path):
        duplicates = check_duplicates(os.path.relpath(path, BASE_DIR)) or []

    # 同时按发票号码查
    if invoice_number:
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

    return jsonify({'success': True, 'duplicates': duplicates})


# ---- 后台任务调度 ----

@bp.route('/api/job/start', methods=['POST'])
@finance_required
# ---- 后台任务入口：整理 / 刷新金额 / 删除重复（配合 SSE 实时进度）----
def api_job_start():
    data = request.get_json(force=True)
    job_type = data.get('type', '')
    if job_type == 'scan_dups':
        jid = _create_job('scan_dups')
        _run_job_thread(jid, _do_scan_dups)
        return jsonify({'job_id': jid})
    elif job_type == 'organize':
        jid = _create_job('organize')
        _run_job_thread(jid, _do_organize)
        return jsonify({'job_id': jid})
    elif job_type == 'refresh':
        jid = _create_job('refresh')
        _run_job_thread(jid, _do_refresh)
        return jsonify({'job_id': jid})
    elif job_type == 'delete_dups':
        jid = _create_job('delete_dups')
        _run_job_thread(jid, _do_delete_dups)
        return jsonify({'job_id': jid})
    return jsonify({'error': '未知任务类型'}), 400


@bp.route('/api/job/stream/<job_id>')
@finance_required
# ---- SSE 进度流：/api/job/stream/<job_id>（依赖进程内 _jobs，须单 worker）----
def api_job_stream(job_id):
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

    ensure_dir(UPLOAD_DIR)
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', file.filename)
    tmp_path = os.path.join(UPLOAD_DIR, f"_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_name}")
    file.save(tmp_path)

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
        'duplicates': duplicates,
        'has_duplicate': len(duplicates) > 0,
    })


@bp.route('/api/upload/batch', methods=['POST'])
# ---- 批量上传解析（返回解析结果供确认表格）----
def api_upload_batch():
    """批量上传多个PDF，解析信息后返回列表"""
    files = request.files.getlist('files')
    if not files:
        return jsonify({'success': False, 'error': '没有上传文件'})

    ensure_dir(UPLOAD_DIR)
    results = []

    # 预建发票号码索引（一次扫描，全局复用）
    inv_index = _build_invoice_number_index()

    for file in files:
        if not file.filename.lower().endswith('.pdf'):
            results.append({'original_name': file.filename, 'error': '非PDF文件', 'success': False})
            continue

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', file.filename)
        tmp_path = os.path.join(UPLOAD_DIR, f"_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{safe_name}")
        try:
            file.save(tmp_path)
        except Exception as e:
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
            'duplicates': duplicates,
            'has_duplicate': len(duplicates) > 0,
        })

    return jsonify({'success': True, 'results': results, 'total': len(results)})


@bp.route('/api/upload/cleanup', methods=['POST'])
def api_upload_cleanup():
    """删除被放弃的临时上传文件(如用户取消入库)"""
    data = request.get_json(silent=True) or {}
    paths = data.get('tmp_paths', [])
    removed = 0
    for p in paths:
        if not isinstance(p, str) or not _is_safe_rel(p):
            continue
        fp = _safe_join(p)
        if fp and os.path.isfile(fp) and os.path.dirname(os.path.realpath(fp)) == os.path.realpath(UPLOAD_DIR):
            try:
                os.remove(fp)
                removed += 1
            except OSError:
                pass
    return jsonify({'success': True, 'removed': removed})


@bp.route('/api/upload/confirm', methods=['POST'])
# ---- 入库确认：成员提交人/状态被服务端强制为 本人 / 未报销 ----
def api_upload_confirm():
    data = request.get_json(force=True)
    tmp_rel = data.get('tmp_path', '')
    if not _is_safe_rel(tmp_rel):
        return jsonify({'success': False, 'error': '非法临时文件路径'}), 400
    tmp_path = _safe_join(tmp_rel)
    month = _normalize_month(data.get('month', ''))
    status = data.get('status', '未报销')
    submitter = data.get('submitter', '').strip()
    if not _is_finance():
        status = '未报销'
        submitter = current_user()['name']
    category = data.get('category', '')
    notes = data.get('notes', '').strip()
    amount = float(data.get('amount', 0))
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
    new_name = _generate_standard_name(tmp_path)
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
        'status': status, 'submitter': submitter, 'notes': notes,
        'category_override': category, 'reimbursed_date': '',
        'invoice_number': invoice_number, 'tags': [],
        'uploaded': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    save_meta(meta)

    if amount > 0:
        cache = load_cache()
        cache[rel_path] = {'amount': amount, 'vendor': vendor,
                           'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        save_cache(cache)

    return jsonify({'success': True, 'rel_path': rel_path, 'message': f'已入库: {os.path.basename(new_path)}'})


@bp.route('/api/upload/confirm/batch', methods=['POST'])
# ---- 批量入库确认（同样强制提交人/状态规则）----
def api_upload_confirm_batch():
    """批量确认上传"""
    data = request.get_json(force=True)
    items = data.get('items', [])
    if not items:
        return jsonify({'success': False, 'error': '没有待入库的发票'})

    results = []
    is_fin = _is_finance()
    cur_name = current_user()['name']
    for item in items:
        tmp_rel = item.get('tmp_path', '')
        tmp_path = _safe_join(tmp_rel) if _is_safe_rel(tmp_rel) else None
        month = _normalize_month(item.get('month', ''))
        status = item.get('status', '未报销')
        submitter = item.get('submitter', '').strip()
        if not is_fin:
            status = '未报销'
            submitter = cur_name
        category = item.get('category', '')
        notes = item.get('notes', '').strip()
        amount = float(item.get('amount', 0))
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

        target_dir = os.path.join(BASE_DIR, month, status)
        ensure_dir(target_dir)

        new_name = _generate_standard_name(tmp_path)
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
                'status': status, 'submitter': submitter, 'notes': notes,
                'category_override': category, 'reimbursed_date': '',
                'invoice_number': invoice_number, 'tags': [],
                'uploaded': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            save_meta(meta)
            if amount > 0:
                cache = load_cache()
                cache[rel_path] = {'amount': amount, 'vendor': vendor, 'updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                save_cache(cache)
            results.append({'file': original_name, 'status': 'ok', 'path': rel_path})
        except Exception as e:
            results.append({'file': original_name, 'status': 'error', 'reason': str(e)})

    ok_count = sum(1 for r in results if r['status'] == 'ok')
    err_count = sum(1 for r in results if r['status'] == 'error')
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

@bp.route('/api/refresh')
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
    fp = _safe_join(rel_path) if rel_path and _is_safe_rel(rel_path) else None
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


@bp.route('/api/export')
@finance_required
# ---- 导出 CSV（管理）----
def api_export():
    invoices = scan_invoices()
    lines = ['月份,文件夹,状态,提交人,序号,类别,金额,金额来源,供应商,开票日期,报销日期,备注,发票号码,文件名,文件路径']
    for i in invoices:
        row = [i['month'], i['folder'], i['status'], i.get('submitter', ''),
               i['seq'], i['category'], str(i['amount']), i['source'],
               i['vendor'], i['date'], i.get('reimbursed_date', ''),
               i.get('notes', ''), i.get('invoice_number', ''),
               i['filename'], i.get('rel_path', '')]
        lines.append(','.join(f'"{str(c).replace(chr(34), chr(39))}"' for c in row))
    csv_path = os.path.join(BASE_DIR, '发票汇总.csv')
    with open(csv_path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines))
    return jsonify({'success': True, 'path': csv_path, 'count': len(invoices)})


# ---- 打包发票 zip：排除 .uploads / __pycache__ / 往届发票.zip ----
def build_invoices_zip(out_path):
    skipped_dirs = {'.uploads', '__pycache__', '_待清理'}
    skipped_files = {'往届发票.zip'}
    if os.path.exists(out_path):
        os.remove(out_path)
    count = 0
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(BASE_DIR):
            dirs[:] = [d for d in dirs if d not in skipped_dirs]
            for name in files:
                if name in skipped_files:
                    continue
                path = os.path.join(root, name)
                z.write(path, os.path.relpath(path, BASE_DIR))
                count += 1
    return count


@bp.route('/api/export_zip')
@finance_required
# ---- 导出压缩包：生成到 /tmp 并作为附件下载（管理）----
def api_export_zip():
    out = os.path.join(tempfile.gettempdir(), 'invoices_export.zip')
    build_invoices_zip(out)
    stamp = datetime.now().strftime('%Y%m%d')
    return send_file(out, mimetype='application/zip', as_attachment=True,
                     download_name=f'invoices_{stamp}.zip')


# ===================== 前端 HTML =====================

# ==================== 前端整页 HTML（内嵌 CSS/JS；/*__APP_CONFIG__*/ 为角色注入点）====================
def create_index_html():
    return r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>巡天御风管理系统 · 发票管理</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f2f4f8;color:#1f2430;min-width:1024px}
body.member .finance-only{display:none!important}
body.member .col-check{display:none!important}
.topbar{display:flex;align-items:center;gap:16px;padding:10px 18px;background:#0f172a;color:#e2e8f0;position:sticky;top:0;z-index:50;flex-wrap:wrap}
.topbar .brand{font-weight:700;font-size:16px;letter-spacing:.5px}
.topbar .brand span{color:#7dd3fc;font-weight:400}
.topbar .nav{display:flex;gap:4px;flex:1}
.topbar .nav a{color:#cbd5e1;padding:6px 12px;border-radius:8px;text-decoration:none;font-size:14px;transition:background .15s}
.topbar .nav a:hover{background:rgba(255,255,255,.08)}
.topbar .nav a.active{background:rgba(125,211,252,.18);color:#7dd3fc}
.topbar .userbox{display:flex;align-items:center;gap:10px}
.topbar .userbox .who{color:#94a3b8;font-size:13px}
.topbar .userbox a.toplink{color:#fff;border:1px solid rgba(255,255,255,.45);border-radius:8px;padding:4px 10px;font-size:13px;text-decoration:none}
.topbar .userbox a.toplink:hover{background:rgba(255,255,255,.12)}
.page-head{background:#fff;border-bottom:1px solid #e3e7ee;padding:12px 26px;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.page-head h1{font-size:18px;font-weight:700}
.page-head .actions{display:flex;gap:8px;flex-wrap:wrap}
.page-head .actions button{background:#fff;border:1px solid #e3e7ee;color:#1f2430;padding:6px 14px;border-radius:8px;cursor:pointer;font-size:12px;white-space:nowrap;transition:.15s}
.page-head .actions button:hover{border-color:#c7cdd8;background:#f8fafc}
.page-head .actions button.btn-accent{background:#2563eb;border-color:#2563eb;color:#fff;font-weight:600}
.page-head .actions button.btn-accent:hover{background:#1d4ed8}
.page-head .actions button.btn-warn{background:#f59e0b;border-color:#f59e0b;color:#fff;font-weight:600}
.page-head .actions button.btn-warn:hover{background:#d97706}
.container{max-width:1500px;margin:0 auto;padding:18px}
/* 拖入区域 */
.dropzone{border:2px dashed #c2d9f9;border-radius:10px;padding:22px;text-align:center;background:#f8fafd;margin-bottom:16px;transition:.2s;cursor:pointer}
.dropzone.drag-over{border-color:#1a73e8;background:#e8f0fe}
.dropzone .dz-icon{font-size:36px;margin-bottom:6px}
.dropzone .dz-text{color:#888;font-size:13px}
.dropzone .dz-text strong{color:#1a73e8}
.dropzone:hover{border-color:#1a73e8;background:#eef4fd}
/* 统计卡 */
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin-bottom:16px}
.stat-card{background:#fff;border-radius:10px;padding:13px 16px;box-shadow:0 1px 3px rgba(0,0,0,0.06);border-left:4px solid transparent;transition:.15s}
.stat-card:hover{box-shadow:0 2px 8px rgba(0,0,0,0.1)}
.stat-card .label{font-size:11px;color:#999;margin-bottom:4px}
.stat-card .value{font-size:20px;font-weight:700}
.stat-card.accent-blue{border-left-color:#1a73e8}
.stat-card.accent-green{border-left-color:#0d904f}
.stat-card.accent-orange{border-left-color:#f9ab00}
.stat-card.accent-red{border-left-color:#d93025}
.stat-card.accent-purple{border-left-color:#9334e6}
/* 工具栏 */
.toolbar{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:12px;background:#fff;padding:11px 16px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06)}
.toolbar select,.toolbar input[type=text],.toolbar input[type=date]{padding:6px 9px;border:1px solid #ddd;border-radius:5px;font-size:12px;outline:none;background:#fff}
.toolbar select:focus,.toolbar input:focus{border-color:#1a73e8}
.toolbar .search{flex:1;min-width:150px}
.toolbar .sep{width:1px;height:20px;background:#ddd;margin:0 3px}
.toolbar .text-btn{background:none;border:none;color:#1a73e8;cursor:pointer;font-size:12px;padding:4px 7px;border-radius:4px;white-space:nowrap}
.toolbar .text-btn:hover{background:#e8f0fe}
/* 批量 */
.batch-bar{display:none;align-items:center;gap:8px;padding:8px 14px;background:#e8f0fe;border-radius:8px;margin-bottom:10px;font-size:12px}
.batch-bar.show{display:flex}
.batch-bar select{padding:5px 8px;border:1px solid #c2d9f9;border-radius:4px;font-size:12px;background:#fff}
.batch-bar button{padding:5px 12px;background:#1a73e8;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px}
.batch-bar button:hover{opacity:.9}
.batch-bar .cancel{background:transparent;color:#666;border:1px solid #ccc}
/* 表格 */
.table-wrap{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,0.06);overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:1150px}
th{background:#fafafa;text-align:left;padding:9px 10px;font-size:11px;color:#888;border-bottom:2px solid #eee;font-weight:600;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid #f0f0f0;font-size:12px}
tr:hover{background:#f8fafd}
tr.row-selected{background:#e8f0fe}
tr.row-dup{background:#fff3e0}
.amount-col{font-weight:600;text-align:right}
.amount-col.missing{color:#aaa}
/* 徽章 */
.badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:10px;font-weight:600;line-height:1.6}
.badge.status-reimbursed{background:#e6f4ea;color:#137333}
.badge.status-pending{background:#fef7e0;color:#b06000}
.badge.status-unreimbursed{background:#fce8e6;color:#c5221f}
.badge.tag-pdf{background:#fff3e0;color:#e65100;font-size:9px}
.badge.tag-filename{background:#e8f5e9;color:#2e7d32;font-size:9px}
.badge.tag-unknown{background:#f3e5f5;color:#7b1fa2;font-size:9px}
.badge.tag-dup{background:#fce8e6;color:#d93025;font-size:9px;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.clickable{color:#1a73e8;cursor:pointer}
.clickable:hover{text-decoration:underline}
/* 分页 */
.pagination{display:flex;justify-content:center;align-items:center;gap:5px;padding:12px;flex-wrap:wrap}
.pagination button,.pagination span{font-size:11px}
.pagination button{padding:4px 10px;border:1px solid #ddd;border-radius:5px;background:#fff;cursor:pointer}
.pagination button:hover{border-color:#1a73e8;color:#1a73e8}
.pagination button.active{background:#1a73e8;color:#fff;border-color:#1a73e8}
.pagination button:disabled{opacity:.4;cursor:default}
.pagination .info{color:#888;margin:0 6px}
/* 弹窗 */
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.3);z-index:1000;justify-content:center;align-items:center}
.modal.show{display:flex}
.modal-content{background:#fff;border-radius:12px;width:90%;max-width:650px;max-height:88vh;overflow-y:auto;padding:22px 26px;box-shadow:0 20px 60px rgba(0,0,0,0.15)}
.modal-content.wide{max-width:1050px}
.modal-content.xwide{max-width:1200px}
.modal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;position:sticky;top:0;background:#fff;z-index:2;padding-bottom:8px;border-bottom:1px solid #eee}
.modal-header h2{font-size:18px}
.modal-close{background:none;border:none;font-size:20px;cursor:pointer;color:#999;padding:2px}
.modal-close:hover{color:#333}
/* 详情网格 */
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px}
.detail-grid .field label{display:block;font-size:11px;color:#888;margin-bottom:3px}
.detail-grid .field input,.detail-grid .field select,.detail-grid .field textarea{width:100%;padding:7px 9px;border:1px solid #ddd;border-radius:5px;font-size:12px;outline:none}
.detail-grid .field textarea{resize:vertical;min-height:48px}
.detail-grid .field input:focus,.detail-grid .field select:focus,.detail-grid .field textarea:focus{border-color:#1a73e8}
.detail-grid .field.full{grid-column:1/-1}
.detail-grid .field input[readonly]{background:#f9f9f9;color:#666}
.detail-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px;padding-top:12px;border-top:1px solid #eee}
.detail-actions button{padding:7px 16px;border-radius:5px;font-size:12px;cursor:pointer}
.btn-save{background:#1a73e8;color:#fff;border:none}
.btn-save:hover{background:#1557b0}
.btn-cancel{background:#fff;color:#666;border:1px solid #ddd}
.btn-cancel:hover{background:#f5f5f5}
.btn-danger{background:transparent;color:#d93025;border:1px solid #fce8e6;margin-right:auto}
.btn-danger:hover{background:#fce8e6}
/* 查重结果 */
.dup-warning{background:#fef7e0;border:1px solid #f9ab00;border-radius:8px;padding:10px 14px;margin-bottom:14px;font-size:12px}
.dup-warning .dup-title{font-weight:600;color:#b06000;margin-bottom:4px}
.dup-warning .dup-item{color:#666;margin:2px 0;font-size:11px}
.dup-warning .dup-skip{color:#1a73e8;cursor:pointer;text-decoration:underline;margin-top:4px;display:inline-block}
/* 批量上传列表 */
.upload-list{width:100%;font-size:12px;margin-bottom:14px}
.upload-list th{background:#fafafa;padding:7px 8px;font-size:10px;position:sticky;top:0}
.upload-list td{padding:6px 8px;font-size:11px}
.upload-list input[type=text],.upload-list select{padding:4px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px;width:100%}
.upload-list .dup-row td{background:#fff3e0}
/* Toast */
.toast{position:fixed;top:20px;left:50%;transform:translateX(-50%);padding:9px 22px;border-radius:7px;font-size:13px;z-index:9999;opacity:0;transition:opacity .3s;pointer-events:none;color:#fff}
.toast.show{opacity:1}
.toast.success{background:#1e8e3e}
.toast.error{background:#d93025}
.toast.info{background:#1a73e8}
.empty{text-align:center;padding:50px 20px;color:#aaa;font-size:13px}
.inline-select{padding:3px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px;background:#fff;cursor:pointer;outline:none;color:#333}
.inline-select:focus{border-color:#1a73e8}
.btn-sm{padding:3px 8px;border:1px solid #ddd;border-radius:4px;background:#fff;cursor:pointer;font-size:10px;white-space:nowrap}
.btn-sm:hover{background:#f0f0f0}
/* 统计图表 */
.chart-row{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;margin-top:14px}
.chart-box{background:#fafbfd;border:1px solid #f0f0f0;border-radius:8px;padding:12px 14px}
.chart-box h3{font-size:13px;color:#666;margin-bottom:12px;font-weight:600}
.chart-bar{display:grid;grid-template-columns:100px 1fr 170px;gap:8px;align-items:center;margin-bottom:8px;font-size:11px}
.chart-bar .name{color:#555;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chart-bar .bar-wrap{background:#f0f0f0;border-radius:3px;height:16px;overflow:hidden}
.chart-bar .bar-fill{height:100%;background:linear-gradient(90deg,#1a73e8,#4fc3f7);border-radius:3px;min-width:2px}
.chart-bar.accent-green .bar-fill{background:#0d904f}
.chart-bar.accent-orange .bar-fill{background:#f9ab00}
.chart-bar.accent-red .bar-fill{background:#d93025}
.chart-bar .bar-value{color:#666;white-space:nowrap;font-size:10px}
@media(max-width:1024px){
.chart-row,.chart-row.three{grid-template-columns:1fr}
.detail-grid{grid-template-columns:1fr}
.container{padding:10px}
}
</style>
</head>
<body>
<script>/*__APP_CONFIG__*/</script>

<header class="topbar">
<div class="brand">巡天御风<span>管理系统</span></div>
<nav class="nav">
<a href="/">看板</a>
<a href="/funds/" class="finance-only">资金管理</a>
<a href="/invoices/" class="active">发票管理</a>
</nav>
<div class="userbox">
<span class="who" id="whoAmI"></span>
<a class="toplink" href="/logout">退出</a>
</div>
</header>
<div class="page-head">
<h1>发票管理</h1>
<div class="actions">
<button onclick="StatsModal.show()">统计</button>
<button class="finance-only" onclick="exportCSV()">导出CSV</button>
<button class="finance-only" onclick="exportZip()">导出压缩包</button>
<button class="finance-only" onclick="scanDuplicates()">查重</button>
<button class="finance-only btn-warn" onclick="organizeFiles()" id="organizeBtn">整理文件</button>
<button class="finance-only btn-accent" onclick="refreshPDF()" id="refreshBtn">刷新PDF金额</button>
</div>
</div>

<div class="container">

<div class="dropzone" id="dropZone" onclick="document.getElementById('fileInput').click()">
<div class="dz-icon">&#128229;</div>
<div class="dz-text">拖入一个或多个PDF发票到此处，或<strong>点击选择文件</strong><br>
<span style="color:#aaa;font-size:11px">支持批量上传，AI自动识别金额/日期/发票号 · 上传时自动查重</span></div>
</div>
<input type="file" id="fileInput" accept=".pdf" multiple style="display:none" onchange="handleFiles(this.files)">

<div class="stats-grid" id="statsGrid"></div>

<div class="batch-bar finance-only" id="batchBar">
<span id="batchCount">已选 0 张</span>
<select id="batchStatus"><option value="">-- 批量改状态 --</option><option value="未报销">未报销</option><option value="正在报销">正在报销</option><option value="已报销">已报销</option></select>
<select id="batchSubmitter"><option value="">-- 批量设提交人 --</option></select>
<button onclick="doBatchUpdate()">执行</button>
<button class="cancel" onclick="clearSelection()">取消选择</button>
</div>

<div class="toolbar">
<select id="filterStatus" onchange="applyFilters()"><option value="">全部状态</option><option value="未报销">未报销</option><option value="正在报销">正在报销</option><option value="已报销">已报销</option></select>
<select id="filterMonth" onchange="applyFilters()"><option value="">全部月份</option></select>
<select id="filterCategory" onchange="applyFilters()"><option value="">全部分类</option></select>
<select id="filterSubmitter" onchange="applyFilters()"><option value="">全部提交人</option></select>
<div class="sep"></div>
<input type="date" id="filterDateFrom" onchange="applyFilters()" title="开始日期" style="width:125px">
<span style="color:#888;font-size:11px">至</span>
<input type="date" id="filterDateTo" onchange="applyFilters()" title="结束日期" style="width:125px">
<div class="sep"></div>
<input type="text" class="search" id="searchInput" placeholder="搜索 供应商/金额/备注/提交人/发票号..." oninput="debounceSearch()">
<button class="text-btn finance-only" onclick="scanDuplicates()">查重</button>
<button class="text-btn finance-only" onclick="organizeFiles()">整理文件</button>
</div>

<div class="table-wrap">
<table>
<thead><tr>
<th class="col-check" style="width:32px"><input type="checkbox" id="selectAll" onclick="toggleSelectAll()" title="全选"></th>
<th>月份</th><th>日期</th><th>状态</th><th>提交人</th><th>类别</th>
<th>供应商</th><th style="text-align:right">金额</th><th>发票号</th><th>操作</th>
</tr></thead>
<tbody id="invoiceTableBody"></tbody>
</table>
<div id="pagination" class="pagination"></div>
<div id="emptyMsg" class="empty" style="display:none">暂无发票数据</div>
</div>
</div>

<!-- 统计弹窗 (略) -->
<div class="modal" id="statsModal"><div class="modal-content wide">
<div class="modal-header"><h2>发票统计</h2><button class="modal-close" onclick="StatsModal.close()">&times;</button></div>
<div class="stats-grid" id="modalStatsGrid"></div>
<div class="chart-row three"><div class="chart-box"><h3>状态分布</h3><div id="statusChart"></div></div><div class="chart-box"><h3>按月份</h3><div id="monthChart"></div></div><div class="chart-box"><h3>按类别</h3><div id="categoryChart"></div></div></div>
<div class="chart-row"><div class="chart-box"><h3>按提交人</h3><div id="submitterChart"></div></div></div>
</div></div>

<!-- 详情弹窗 -->
<div class="modal" id="detailModal"><div class="modal-content">
<div class="modal-header"><h2>发票详情</h2><button class="modal-close" onclick="DetailModal.close()">&times;</button></div>
<div class="detail-grid">
<div class="field"><label>月份</label><input id="detMonth" readonly></div>
<div class="field"><label>开票日期</label><input id="detDate" readonly></div>
<div class="field"><label>状态</label><select id="detStatus"><option value="未报销">未报销</option><option value="正在报销">正在报销</option><option value="已报销">已报销</option></select></div>
<div class="field"><label>报销日期</label><input type="date" id="detReimbursedDate"></div>
<div class="field"><label>提交人</label><input id="detSubmitter" placeholder="输入提交人姓名"></div>
<div class="field"><label>类别</label><input id="detCategory" placeholder="类别"></div>
<div class="field"><label>供应商</label><input id="detVendor" readonly></div>
<div class="field"><label>金额</label><input id="detAmount" readonly></div>
<div class="field full"><label>备注</label><textarea id="detNotes" placeholder="添加备注..." rows="2"></textarea></div>
<div class="field"><label>发票号码</label><input id="detInvNum" placeholder="发票号码"></div>
<div class="field"><label>标签</label><input id="detTags" placeholder="逗号分隔"></div>
<div class="field"><label>文件名</label><input id="detFilename" readonly></div>
<div class="field"><label>金额来源</label><input id="detSource" readonly></div>
<div class="field full"><label>文件路径</label><input id="detPath" readonly style="font-size:11px;color:#888"></div>
</div>
<div class="detail-actions">
<button class="btn-danger finance-only" onclick="DetailModal.clearMeta()">清除元数据</button>
<button class="btn-cancel" onclick="DetailModal.close()">取消</button>
<button class="btn-save finance-only" onclick="DetailModal.save()">保存</button>
</div>
</div></div>

<!-- 批量上传确认弹窗 -->
<div class="modal" id="batchUploadModal"><div class="modal-content xwide">
<div class="modal-header"><h2>批量确认发票入库 (<span id="buTotal">0</span> 张)</h2><button class="modal-close" onclick="BUModal.close()">&times;</button></div>
<div class="finance-only" style="display:flex;gap:12px;align-items:center;margin-bottom:14px">
<label style="font-size:12px;color:#888;white-space:nowrap">统一提交人:</label>
<input type="text" id="buSubmitter" placeholder="填写所有发票的来源人" style="flex:1;padding:7px 10px;border:1px solid #ddd;border-radius:5px;font-size:13px">
<label style="font-size:12px;color:#888;white-space:nowrap">统一状态:</label>
<select id="buStatus" style="padding:7px 10px;border:1px solid #ddd;border-radius:5px;font-size:12px"><option value="未报销">未报销</option><option value="正在报销">正在报销</option><option value="已报销">已报销</option></select>
</div>
<div style="max-height:55vh;overflow-y:auto">
<table class="upload-list" id="buTable"><thead><tr><th style="width:30px">#</th><th>文件名</th><th>金额</th><th>日期</th><th>月份</th><th>供应商</th><th>发票号</th><th>类别</th><th>提交人</th><th>重复</th></tr></thead><tbody id="buTbody"></tbody></table>
</div>
<div class="detail-actions" style="position:sticky;bottom:0;background:#fff;padding-top:10px">
<button class="btn-cancel" onclick="BUModal.close()">取消</button>
<button class="btn-save" onclick="BUModal.confirm()">全部确认入库</button>
</div>
</div></div>

<!-- 删除重复确认弹窗 -->
<div class="modal" id="delDupModal"><div class="modal-content">
<div class="modal-header"><h2>删除重复发票</h2><button class="modal-close" onclick="DelDupModal.close()">&times;</button></div>
<div class="dup-warning">
<div class="dup-title" style="font-size:14px">发现 <span id="delDupCount">0</span> 个重复项</div>
<div style="color:#888;margin-top:6px;font-size:12px">每组重复将保留较早的一个文件，删除其余副本<br>此操作<b>不可恢复</b>，建议先确认后再执行</div>
</div>
<div style="max-height:200px;overflow-y:auto;margin-bottom:14px;font-size:11px;color:#666" id="delDupList"></div>
<div class="detail-actions">
<button class="btn-cancel" onclick="DelDupModal.close()">保留重复文件</button>
<button class="btn-danger" onclick="DelDupModal.confirm()" style="background:#d93025;color:#fff;border:none;padding:8px 18px;border-radius:5px;cursor:pointer">确认删除重复文件</button>
</div>
</div></div>

<div class="toast" id="toast"></div>
<!-- 进度条 -->
<div id="progressBar" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;border-radius:14px;padding:28px 36px;box-shadow:0 12px 40px rgba(0,0,0,0.2);z-index:10000;text-align:center;min-width:320px">
<div style="font-size:14px;color:#333;margin-bottom:14px" id="progressText">处理中...</div>
<div style="width:100%;height:8px;background:#e0e0e0;border-radius:4px;overflow:hidden">
<div id="progressFill" style="height:100%;width:0;background:linear-gradient(90deg,#1a73e8,#4fc3f7);border-radius:4px;transition:width 0.3s"></div>
</div>
<div style="font-size:11px;color:#999;margin-top:8px" id="progressPercent">0%</div>
</div>

<script>
var App={currentPage:1,totalPages:1,selected:{},stats:null,searchTimer:null,_dupCache:null};
function showToast(msg,type){var t=document.getElementById('toast');t.textContent=msg;t.className='toast show '+(type||'info');setTimeout(function(){t.classList.remove('show')},3000)}

async function loadStats(){
var r=await fetch('/invoices/api/stats');App.stats=await r.json();var s=App.stats,ss=s.status_stats||{};
document.getElementById('statsGrid').innerHTML=[
{l:'发票总数',v:s.total_count,cls:'accent-blue'},
{l:'总金额',v:'\xA5'+(s.total_amount||0).toLocaleString(),cls:'accent-blue'},
{l:'已报销',v:(ss['已报销']||{}).count+' / \xA5'+((ss['已报销']||{}).amount||0).toLocaleString(),cls:'accent-green'},
{l:'正在报销',v:(ss['正在报销']||{}).count+' / \xA5'+((ss['正在报销']||{}).amount||0).toLocaleString(),cls:'accent-orange'},
{l:'未报销',v:(ss['未报销']||{}).count+' / \xA5'+((ss['未报销']||{}).amount||0).toLocaleString(),cls:'accent-red'},
{l:'提交人',v:Object.keys(s.submitter_stats||{}).filter(function(k){return k!=='未指定'}).length+' 人',cls:'accent-purple'},
].map(function(c){return '<div class="stat-card '+c.cls+'"><div class="label">'+c.l+'</div><div class="value">'+c.v+'</div></div>'}).join('');
['filterMonth','filterCategory'].forEach(function(id){setOptions(id,Object.keys(s[id.replace('filter','').toLowerCase()+'_stats']||{}).sort().reverse())});
['filterSubmitter','batchSubmitter'].forEach(function(id){setOptions(id,Object.keys(s.submitter_stats||{}).filter(function(k){return k!=='未指定'}))});
}
function setOptions(id,arr){var sel=document.getElementById(id);if(!sel)return;var cur=sel.value,first=sel.options[0];sel.innerHTML='';sel.appendChild(first);arr.forEach(function(v){var o=document.createElement('option');o.value=v;o.textContent=v;if(v===cur)o.selected=true;sel.appendChild(o)})}

async function loadInvoices(page,highlightDups){
page=page||1;App.currentPage=page;
var p=new URLSearchParams();
['filterStatus','filterMonth','filterCategory','filterSubmitter'].forEach(function(id){var el=document.getElementById(id);if(el&&el.value)p.set(id.replace('filter','').toLowerCase(),el.value)});
var df=document.getElementById('filterDateFrom'),dt=document.getElementById('filterDateTo');
if(df.value)p.set('date_from',df.value);
if(dt.value)p.set('date_to',dt.value);
var kw=document.getElementById('searchInput').value.trim();
if(kw)p.set('search',kw);
p.set('page',page);p.set('per_page','50');
var r=await fetch('/invoices/api/invoices?'+p.toString()),data=await r.json();
App.totalPages=Math.ceil(data.total/data.per_page)||1;
var tb=document.getElementById('invoiceTableBody'),em=document.getElementById('emptyMsg');

var dupPaths=highlightDups?Object.values(App._dupCache||{}).flat():[];

if(data.data.length===0){tb.innerHTML='';em.style.display='block'}else{em.style.display='none';
tb.innerHTML=data.data.map(function(inv){
var sc=App.selected[inv.id]?' row-selected':'';
if(dupPaths.length&&dupPaths.indexOf(inv.rel_path)>=0)sc+=' row-dup';
var ah=inv.amount>0?'\xA5'+inv.amount.toLocaleString():'<span class="amount-col missing">\xA50.00</span>';
var st=inv.source==='PDF'?'<span class="badge tag-pdf">PDF</span>':inv.source==='PDF(\u7f13\u5b58)'?'<span class="badge tag-pdf">PDF\u7f13\u5b58</span>':inv.source==='文件名'?'<span class="badge tag-filename">文件名</span>':'<span class="badge tag-unknown">待识别</span>';
var eid = jsEscape(inv.id);
var erp = encodeURIComponent(inv.rel_path||'');
var subHtml=inv.submitter?'<span class="clickable" onclick="DetailModal.open(\''+eid+'\')">'+escHtml(inv.submitter)+'</span>':'<span style="color:#ccc;cursor:pointer" onclick="DetailModal.open(\''+eid+'\')">-</span>';
var invNum=inv.invoice_number?('<span class="clickable" title="'+escHtml(inv.invoice_number)+'">'+inv.invoice_number.substring(inv.invoice_number.length-8)+'</span>'):'-';
var scol = inv.status==='已报销'?'#e6f4ea':inv.status==='正在报销'?'#fef7e0':'#fce8e6';
var unsel = inv.status==='未报销'?' selected':'';
var insel = inv.status==='正在报销'?' selected':'';
var resel = inv.status==='已报销'?' selected':'';
return '<tr class="'+sc+'" data-id="'+escHtml(inv.id)+'">'+
'<td class="col-check"><input type="checkbox" '+(App.selected[inv.id]?'checked':'')+' onclick="toggleOne(\''+eid+'\',this.checked)"></td>'+
'<td><span class="clickable" onclick="openFolder(\''+encodeURIComponent(inv.month)+'/'+encodeURIComponent(inv.status)+'\')">'+inv.month+'</span></td>'+
'<td>'+(inv.date||'-')+'</td>'+
'<td>'+(IS_FINANCE?'<select class="inline-select" onchange="quickSetStatus(\''+eid+'\',this.value)" style="background:'+scol+'"><option value="\u672a\u62a5\u9500"'+unsel+'>\u672a\u62a5\u9500</option><option value="\u6b63\u5728\u62a5\u9500"'+insel+'>\u6b63\u5728\u62a5\u9500</option><option value="\u5df2\u62a5\u9500"'+resel+'>\u5df2\u62a5\u9500</option></select>':'<span class="badge" style="background:'+scol+'">'+inv.status+'</span>')+'</td>'+
'<td>'+subHtml+'</td>'+
'<td>'+(inv.category||'-')+'</td>'+
'<td><span class="clickable" onclick="openFile(\''+erp+'\')">'+escHtml(inv.vendor)+'</span></td>'+
'<td class="amount-col">'+ah+'</td>'+
'<td style="font-size:10px">'+invNum+'</td>'+
'<td><button class="btn-sm" onclick="openFile(\''+erp+'\')">\u6253\u5f00</button> <button class="btn-sm" onclick="DetailModal.open(\''+eid+'\')">'+(IS_FINANCE?'\u7f16\u8f91':'\u67e5\u770b')+'</button></td></tr>';
}).join('');document.getElementById('selectAll').checked=false}
renderPagination(data.total,page);updateBatchBar()
}
function applyFilters(){loadInvoices(1);loadStats()}
function debounceSearch(){clearTimeout(App.searchTimer);App.searchTimer=setTimeout(function(){loadInvoices(1)},400)}
function renderPagination(total,page){var pg=document.getElementById('pagination'),tp=App.totalPages;if(tp<=1){pg.innerHTML='';return}var h='<span class="info">共 '+total+' 条 / '+tp+' 页</span>';h+='<button '+(page<=1?'disabled':'')+' onclick="loadInvoices('+(page-1)+')">上一页</button>';for(var i=1;i<=tp;i++){if(i===1||i===tp||(i>=page-2&&i<=page+2))h+='<button class="'+(i===page?'active':'')+'" onclick="loadInvoices('+i+')">'+i+'</button>';else if(i===page-3||i===page+3)h+='<span>...</span>'}h+='<button '+(page>=tp?'disabled':'')+' onclick="loadInvoices('+tp+')">末页</button>';pg.innerHTML=h}

function toggleOne(id,checked){if(checked)App.selected[id]=true;else delete App.selected[id];updateBatchBar()}
function toggleSelectAll(){var c=document.getElementById('selectAll').checked,cbs=document.querySelectorAll('#invoiceTableBody input[type=checkbox]');cbs.forEach(function(cb){var tr=cb.closest('tr');if(!tr)return;var id=tr.getAttribute('data-id');if(!id)return;if(c)App.selected[id]=true;else delete App.selected[id];cb.checked=c});updateBatchBar()}
function updateBatchBar(){var b=document.getElementById('batchBar'),n=Object.keys(App.selected).length;if(n>0){b.classList.add('show');document.getElementById('batchCount').textContent='已选 '+n+' 张'}else{b.classList.remove('show')}}
function clearSelection(){App.selected={};updateBatchBar();loadInvoices(App.currentPage)}
function escHtml(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function jsEscape(s){return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'").replace(/\n/g,'\\n').replace(/\r/g,'')}

async function doBatchUpdate(){
var ids=Object.keys(App.selected);if(!ids.length)return;
var u={},st=document.getElementById('batchStatus'),su=document.getElementById('batchSubmitter');
if(st.value)u.status=st.value;
if(su.value)u.submitter=su.value;
if(!Object.keys(u).length){showToast('请选择要修改的字段','error');return}
var r=await fetch('/invoices/api/invoice/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids:ids,updates:u})});
var d=await r.json();
if(d.success){showToast('已更新 '+d.count+' 张'+(d.moved?'，已移动 '+d.moved+' 个文件':''),'success');clearSelection();App.stats=null;loadStats();loadInvoices(App.currentPage)}}

async function quickSetStatus(id,status){
var r=await fetch('/invoices/api/invoice/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,status:status})});
var d=await r.json();
if(d.success){App.stats=null;loadStats();loadInvoices(App.currentPage);showToast(d.moved?'状态已更新，文件已移动':'状态已更新','success')}}

// ====== 进度条 ======
var Progress={show:function(msg){document.getElementById('progressText').textContent=msg||'处理中...';document.getElementById('progressFill').style.width='0%';document.getElementById('progressPercent').textContent='0%';document.getElementById('progressBar').style.display='block'},update:function(pct,msg){document.getElementById('progressFill').style.width=(pct||0)+'%';document.getElementById('progressPercent').textContent=Math.round(pct||0)+'%';if(msg)document.getElementById('progressText').textContent=msg},hide:function(){document.getElementById('progressBar').style.display='none'}};

// ====== XMLHttpRequest 工具 ======
function xhrUpload(url,formData,onProgress){
return new Promise(function(resolve,reject){
var xhr=new XMLHttpRequest();
xhr.open('POST',url);
xhr.upload.onprogress=function(e){if(e.lengthComputable&&onProgress)onProgress(Math.round(e.loaded/e.total*100))};
xhr.onload=function(){try{resolve(JSON.parse(xhr.responseText))}catch(e){reject(new Error('解析响应失败'))}};
xhr.onerror=function(){reject(new Error('网络错误'))};
xhr.ontimeout=function(){reject(new Error('请求超时'))};
xhr.timeout=180000;
xhr.send(formData);
});}

// ====== 拖入 ======
var dropZone=document.getElementById('dropZone');
dropZone.addEventListener('dragover',function(e){e.preventDefault();dropZone.classList.add('drag-over')});
dropZone.addEventListener('dragleave',function(){dropZone.classList.remove('drag-over')});
dropZone.addEventListener('drop',function(e){e.preventDefault();dropZone.classList.remove('drag-over');handleFiles(e.dataTransfer.files)});
document.getElementById('fileInput').addEventListener('change',function(){handleFiles(this.files);this.value=''});

async function handleFiles(files){
if(!files||!files.length)return;
var pdfs=[];for(var i=0;i<files.length;i++){if(files[i].name.toLowerCase().endsWith('.pdf'))pdfs.push(files[i])}
if(!pdfs.length){showToast('请选择PDF文件','error');return}

if(pdfs.length===1){
var fd=new FormData();fd.append('file',pdfs[0]);
Progress.show('上传并解析: '+pdfs[0].name);
try{
var d=await xhrUpload('/invoices/api/upload',fd,function(pct){Progress.update(pct,'上传中 '+pct+'%')});
Progress.update(90,'AI解析中...');
Progress.hide();
if(d.success){quickConfirm(d)}else{showToast(d.error,'error')}
}catch(e){Progress.hide();showToast('上传失败: '+e.message,'error')}
}else{
Progress.show('准备上传 '+pdfs.length+' 个PDF');
var fd2=new FormData();
for(var j=0;j<pdfs.length;j++)fd2.append('files',pdfs[j]);
try{
var d2=await xhrUpload('/invoices/api/upload/batch',fd2,function(pct){
Progress.update(Math.min(pct*0.6,60),'上传中 '+pct+'% ('+pdfs.length+'个文件)');
});
Progress.update(80,'AI解析发票信息...');
setTimeout(function(){Progress.update(90,'完成解析')},200);
Progress.hide();
if(d2.success){BUModal.open(d2.results)}else{showToast('上传失败: '+(d2.error||'未知错误'),'error')}
}catch(e){Progress.hide();showToast('上传失败: '+e.message,'error')}
}
}

function cleanupTmp(paths){if(!paths||!paths.length)return;fetch('/invoices/api/upload/cleanup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tmp_paths:paths})})}

async function quickConfirm(d){
var sub=IS_FINANCE?prompt('请输入提交人（来源人）：'):ME_NAME;
if(!sub||!sub.trim()){showToast('需要填写提交人才能入库','error');cleanupTmp([d.tmp_path]);return}
var body={tmp_path:d.tmp_path,original_name:d.original_name,month:d.info.month||'未归类',status:'未报销',submitter:sub.trim(),category:d.info.category||'',notes:'',amount:d.info.amount||0,vendor:d.info.vendor||'',date:d.info.date||'',invoice_number:d.info.invoice_number||'',skip_duplicate_check:!d.has_duplicate||confirm('检测到可能重复，仍要入库吗？')};
Progress.show('正在入库...');
try{var r=await fetch('/invoices/api/upload/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});var dd=await r.json();Progress.hide();if(dd.success){showToast(dd.message,'success');App.stats=null;loadStats();loadInvoices(1)}else if(dd.code==='DUPLICATE'){showToast('存在重复发票，已拦截','error');cleanupTmp([d.tmp_path])}else{showToast(dd.error,'error');cleanupTmp([d.tmp_path])}}
catch(e){Progress.hide();showToast('入库失败: '+e.message,'error')}
}

// ====== 批量上传弹窗 ======
var BUModal={
_data:[],
open:function(results){
BUModal._data=results;
document.getElementById('buTotal').textContent=results.length;
document.getElementById('buSubmitter').value='';
var tbody=document.getElementById('buTbody');
tbody.innerHTML=results.map(function(r,idx){
if(!r.success)return '<tr><td>'+(idx+1)+'</td><td colspan="9" style="color:#d93025">'+r.original_name+': '+r.error+'</td></tr>';
var d=r.info||{};
var dupClass=r.has_duplicate?' dup-row':'';
var dupInfo=r.has_duplicate?'<span class="badge tag-dup" title="'+r.duplicates.map(function(dd){return dd.path}).join('\n')+'">重复!</span>':'-';
return '<tr class="'+dupClass+'">'+
'<td>'+(idx+1)+'</td>'+
'<td title="'+escHtml(r.original_name)+'">'+escHtml(r.original_name.substring(0,25))+'</td>'+
'<td><input type="number" step="0.01" value="'+(d.amount||'')+'" style="width:70px" class="bu-amount-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.date||'')+'" style="width:90px" class="bu-date-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.month||'')+'" style="width:55px" class="bu-month-'+idx+'"></td>'+
'<td><input type="text" value="'+escHtml(d.vendor||'')+'" style="width:120px" class="bu-vendor-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.invoice_number||'')+'" style="width:100px" class="bu-inum-'+idx+'"></td>'+
'<td><input type="text" value="'+(d.category||'')+'" style="width:60px" class="bu-cat-'+idx+'"></td>'+
'<td><input type="text" value="'+(IS_FINANCE?'':escHtml(ME_NAME))+'" placeholder="来源人" style="width:70px" class="bu-sub-'+idx+'"'+(IS_FINANCE?'':' readonly')+'></td>'+
'<td>'+dupInfo+'</td></tr>';
}).join('');
document.getElementById('batchUploadModal').classList.add('show');
},
close:function(){
document.getElementById('batchUploadModal').classList.remove('show');
var paths=BUModal._data.filter(function(r){return r.success}).map(function(r){return r.tmp_path});
BUModal._data=[];
cleanupTmp(paths);
},
confirm:async function(){
var dupN=BUModal._data.filter(function(r){return r.success&&r.has_duplicate}).length;
if(dupN>0&&!confirm('有 '+dupN+' 张疑似重复发票, 仍要入库吗?'))return;
var globalSub=IS_FINANCE?document.getElementById('buSubmitter').value.trim():ME_NAME;
var globalStatus=IS_FINANCE?document.getElementById('buStatus').value:'未报销';
var items=[];
for(var i=0;i<BUModal._data.length;i++){
var r=BUModal._data[i];
if(!r.success)continue;
var sub=document.querySelector('.bu-sub-'+i);
items.push({tmp_path:r.tmp_path,original_name:r.original_name,status:globalStatus,submitter:(sub&&sub.value.trim())||globalSub,category:document.querySelector('.bu-cat-'+i).value,notes:'',amount:parseFloat(document.querySelector('.bu-amount-'+i).value)||0,vendor:document.querySelector('.bu-vendor-'+i).value,date:document.querySelector('.bu-date-'+i).value,month:document.querySelector('.bu-month-'+i).value||'未归类',invoice_number:document.querySelector('.bu-inum-'+i).value});
}
if(!globalSub&&!items.every(function(it){return it.submitter})){showToast('请填写提交人','error');return}
Progress.show('正在入库 '+items.length+' 张发票...');
try{
var r2=await fetch('/invoices/api/upload/confirm/batch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:items})});
var d2=await r2.json();
Progress.hide();
if(d2.success){showToast(d2.message,'success');BUModal.close();App.stats=null;loadStats();loadInvoices(1)}
else{showToast('入库失败','error')}
}catch(e){Progress.hide();showToast('入库失败: '+e.message,'error')}
}
};
document.getElementById('batchUploadModal').addEventListener('click',function(e){if(e.target===this)BUModal.close()});

// ====== 实时进度 SSE ======
function startJob(type, onResult){
Progress.show('准备中...');
fetch('/invoices/api/job/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:type})})
.then(function(r){return r.json()})
.then(function(d){
if(d.error){Progress.hide();showToast(d.error,'error');return}
var src=new EventSource('/invoices/api/job/stream/'+d.job_id);
src.onmessage=function(e){
var data=JSON.parse(e.data);
Progress.update(data.progress, data.message);
if(data.status==='done'){
src.close();
Progress.hide();
if(onResult)onResult(data);
}else if(data.status==='error'){
src.close();
Progress.hide();
showToast('操作失败: '+data.message,'error');
}
};
src.onerror=function(){src.close();Progress.hide();showToast('连接中断','error')};
})
.catch(function(e){Progress.hide();showToast('启动失败: '+e.message,'error')});
}

// ====== 查重 ======
function scanDuplicates(){
startJob('scan_dups', function(data){
var res=data.result||{};
var dups=res.duplicates||{};
var dupCount=res.dup_count||0;
var allPaths=res.all_dup_paths||[];
App._dupCache=allPaths.length?{_all:allPaths}:null;
if(dupCount===0){
showToast('未发现重复发票','success');
loadInvoices(App.currentPage,false);
}else{
var msg='发现 '+dupCount+' 个重复项（'+Object.keys(dups).length+' 组）';
if(allPaths.length>0)msg+='，'+allPaths.length+' 个文件已高亮';
showToast(msg,'error');
loadInvoices(App.currentPage,true);
// 显示删除确认弹窗
DelDupModal.show(dups, allPaths);
}
});
}

var DelDupModal={
_data:null,
show:function(dups, allPaths){
DelDupModal._data={dups:dups,allPaths:allPaths};
document.getElementById('delDupCount').textContent=Object.keys(dups).length+' 组';
var list=[];
Object.entries(dups).forEach(function(e){
var num=e[0];
var paths=e[1];
list.push('<div style="margin-bottom:6px;padding:4px 8px;background:#fff8e1;border-radius:4px"><b>发票号:</b> '+escHtml(num)+' ('+paths.length+'个文件)</div>');
paths.forEach(function(p,pi){
if(pi===0)list.push('<div style="margin-left:16px;color:#0d904f">  \u2714 保留: '+escHtml(p)+'</div>');
else list.push('<div style="margin-left:16px;color:#d93025">  \u2716 删除: '+escHtml(p)+'</div>');
});
});
document.getElementById('delDupList').innerHTML=list.join('');
document.getElementById('delDupModal').classList.add('show');
},
close:function(){
document.getElementById('delDupModal').classList.remove('show');
DelDupModal._data=null;
},
confirm:async function(){
if(!confirm('确定要删除重复文件吗？此操作不可恢复！'))return;
DelDupModal.close();
startJob('delete_dups', function(){
showToast('重复文件已删除','success');
App._dupCache=null;
App.stats=null;
loadStats();
loadInvoices(1,false);
});
}
};
document.getElementById('delDupModal').addEventListener('click',function(e){if(e.target===this)DelDupModal.close()});

// ====== 详情弹窗 (保持不变) ======
var DetailModal={
_id:'',
open:async function(id){DetailModal._id=id;var r=await fetch('/invoices/api/invoices?id='+encodeURIComponent(id)),d=await r.json(),inv=d.data.length?d.data[0]:null;if(!inv){showToast('未找到发票','error');return}
document.getElementById('detMonth').value=inv.month;
document.getElementById('detDate').value=inv.date||'';
document.getElementById('detStatus').value=inv.status||'未报销';
document.getElementById('detReimbursedDate').value=inv.reimbursed_date||'';
document.getElementById('detSubmitter').value=inv.submitter||'';
document.getElementById('detCategory').value=inv.category||'';
document.getElementById('detVendor').value=inv.vendor;
document.getElementById('detAmount').value='\xA5'+inv.amount.toLocaleString();
document.getElementById('detNotes').value=inv.notes||'';
document.getElementById('detInvNum').value=inv.invoice_number||'';
document.getElementById('detTags').value=(inv.tags||[]).join(', ');
document.getElementById('detFilename').value=inv.filename;
document.getElementById('detSource').value=inv.source||'未知';
document.getElementById('detPath').value=inv.rel_path||'';
document.getElementById('detailModal').classList.add('show')},
close:function(){document.getElementById('detailModal').classList.remove('show');DetailModal._id=''},
save:async function(){
var u={status:document.getElementById('detStatus').value,submitter:document.getElementById('detSubmitter').value.trim(),notes:document.getElementById('detNotes').value.trim(),category_override:document.getElementById('detCategory').value.trim(),reimbursed_date:document.getElementById('detReimbursedDate').value,invoice_number:document.getElementById('detInvNum').value.trim(),tags:document.getElementById('detTags').value.split(',').map(function(s){return s.trim()}).filter(Boolean)};
var r=await fetch('/invoices/api/invoice/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({id:DetailModal._id},u))});
var d=await r.json();
if(d.success){showToast('保存成功'+(d.moved?'，文件已移动':''),'success');DetailModal.close();App.stats=null;loadStats();loadInvoices(App.currentPage)}},
clearMeta:async function(){if(!confirm('确定要清除该发票的元数据？'))return;var r=await fetch('/invoices/api/invoice/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:DetailModal._id,status:'',submitter:'',notes:'',category_override:'',reimbursed_date:'',tags:[],invoice_number:''})});if((await r.json()).success){showToast('已清除','success');DetailModal.close();App.stats=null;loadStats();loadInvoices(App.currentPage)}}
};
document.getElementById('detailModal').addEventListener('click',function(e){if(e.target===this)DetailModal.close()});

// ====== 统计弹窗 (精简) ======
var StatsModal={show:async function(){
if(!App.stats){var r=await fetch('/invoices/api/stats');App.stats=await r.json()}var s=App.stats,ss=s.status_stats||{};
document.getElementById('modalStatsGrid').innerHTML=[{l:'发票总数',v:s.total_count,cls:'accent-blue'},{l:'总金额',v:'\xA5'+(s.total_amount||0).toLocaleString(),cls:'accent-blue'},{l:'已报销',v:(ss['已报销']||{}).count+'张 / \xA5'+((ss['已报销']||{}).amount||0).toLocaleString(),cls:'accent-green'},{l:'正在报销',v:(ss['正在报销']||{}).count+'张 / \xA5'+((ss['正在报销']||{}).amount||0).toLocaleString(),cls:'accent-orange'},{l:'未报销',v:(ss['未报销']||{}).count+'张 / \xA5'+((ss['未报销']||{}).amount||0).toLocaleString(),cls:'accent-red'}].map(function(o){return '<div class="stat-card '+o.cls+'"><div class="label">'+o.l+'</div><div class="value">'+o.v+'</div></div>'}).join('');
var months=Object.entries(s.month_stats||{}).sort(),maxM=Math.max.apply(null,months.map(function(e){return e[1].amount}))||1;document.getElementById('monthChart').innerHTML=months.map(function(e){return '<div class="chart-bar"><div class="name">'+e[0]+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+(e[1].amount/maxM*100).toFixed(1)+'%"></div></div><div class="bar-value">\xA5'+e[1].amount.toLocaleString()+' ('+e[1].count+'张)</div></div>'}).join('');
document.getElementById('statusChart').innerHTML=['未报销','正在报销','已报销'].map(function(st){var d=ss[st]||{count:0,amount:0},cls=st==='已报销'?'accent-green':st==='正在报销'?'accent-orange':'accent-red',pct=s.total_count?(d.count/s.total_count*100).toFixed(1):0;return '<div class="chart-bar '+cls+'"><div class="name">'+st+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+Math.max(pct,2)+'%"></div></div><div class="bar-value">\xA5'+d.amount.toLocaleString()+' ('+d.count+'张)</div></div>'}).join('');
var cats=Object.entries(s.category_stats||{}).sort(function(a,b){return b[1].amount-a[1].amount}),maxC=Math.max.apply(null,cats.map(function(e){return e[1].amount}))||1;document.getElementById('categoryChart').innerHTML=cats.map(function(e){return '<div class="chart-bar"><div class="name">'+e[0]+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+(e[1].amount/maxC*100).toFixed(1)+'%"></div></div><div class="bar-value">\xA5'+e[1].amount.toLocaleString()+' ('+e[1].count+'张)</div></div>'}).join('');
var subs=Object.entries(s.submitter_stats||{}).sort(function(a,b){return b[1].amount-a[1].amount}),maxS=Math.max.apply(null,subs.map(function(e){return e[1].amount}))||1;document.getElementById('submitterChart').innerHTML=subs.length===0?'<div style="color:#aaa;text-align:center;padding:20px">暂无提交人数据</div>':subs.map(function(e){return '<div class="chart-bar"><div class="name">'+e[0]+'</div><div class="bar-wrap"><div class="bar-fill" style="width:'+(e[1].amount/maxS*100).toFixed(1)+'%"></div></div><div class="bar-value">\xA5'+e[1].amount.toLocaleString()+' ('+e[1].count+'张)</div></div>'}).join('');
document.getElementById('statsModal').classList.add('show')},close:function(){document.getElementById('statsModal').classList.remove('show')}};
document.getElementById('statsModal').addEventListener('click',function(e){if(e.target===this)StatsModal.close()});

// ====== 文件操作 ======
async function openFile(relPath){var r=await fetch('/invoices/api/open_file?rel_path='+encodeURIComponent(relPath)),d=await r.json();if(d.success&&d.url){var a=document.createElement('a');a.href=d.url;a.target='_blank';document.body.appendChild(a);a.click();a.remove()}else{showToast((d&&d.error)||'打开失败','error')}}
function openFolder(folder){showToast('服务端部署不支持直接打开文件夹','error')}
async function exportCSV(){try{var r=await fetch('/invoices/api/export'),d=await r.json();showToast('导出成功！共 '+d.count+' 条 -> 发票汇总.csv','success')}catch(e){showToast('导出失败','error')}}
function exportZip(){showToast('正在打包全部发票，请稍候...','info');window.location.href='/invoices/api/export_zip'}

async function organizeFiles(){
var btn=document.getElementById('organizeBtn');
try{
var pv=await fetch('/invoices/api/organize/preview'),pd=await pv.json();
if(pd.count===0){showToast('没有需要整理的文件','info');return}
if(!confirm('整理预览: 共 '+pd.count+' 个文件将被归档到对应的 月份/状态 文件夹并标准化命名, 是否继续?'))return;
}catch(e){}
btn.disabled=true;btn.textContent='整理中...';
startJob('organize', function(){
btn.disabled=false;btn.textContent='整理文件';
App.stats=null;loadStats();loadInvoices(1);
showToast('整理完成','success');
});
}

async function refreshPDF(){
var b1=document.getElementById('refreshBtn');b1.disabled=true;b1.textContent='识别中...';
startJob('refresh', function(){
b1.disabled=false;b1.textContent='刷新PDF金额';
App.stats=null;loadStats();loadInvoices(App.currentPage);
showToast('PDF金额刷新完成','success');
});
}

window.IS_FINANCE=window.IS_FINANCE||false;window.ME_NAME=window.ME_NAME||'';
document.getElementById('whoAmI').textContent=window.ME_NAME;
if(!window.IS_FINANCE){document.body.classList.add('member');document.querySelectorAll('#detailModal input,#detailModal select,#detailModal textarea').forEach(function(el){el.disabled=true});}
loadStats().then(function(){loadInvoices()});
</script>
</body>
</html>'''


# ===================== 集成适配 =====================


@bp.before_request
# ---- 模块守卫：登录即可访问（管理接口与数据级权限在各自位置控制）----
def _invoices_guard():
    user = current_user()
    if user is None:
        if '/api/' in request.path:
            return jsonify({'success': False, 'error': '未登录'}), 401
        return redirect('/login')
    return None


@bp.route('/file/<path:rel>')
# ---- 文件下发：/invoices/file/<path>（成员仅能取自己的发票）----
def invoice_file(rel):
    if not _is_safe_rel(rel):
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


# ---- 模块导入即执行：键归一化 → 目录检查 → 过期临时文件清理 ----
_normalize_windows_keys()
build_folder_structure()
ensure_dir(UPLOAD_DIR)
cleanup_uploads()
