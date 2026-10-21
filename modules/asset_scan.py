"""Supplier labels are untrusted input, never executable JSON or stock authority."""
import hashlib
import json
import re
from functools import wraps

from flask import Blueprint, jsonify, request
from common import current_user, get_db, login_required
from modules.assets import Invalid, event, integer, manager, mutation, require_manager, row, stamp, text

bp = Blueprint('asset_scan', __name__)


def migrate(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS asset_identifiers (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES asset_items(id),
            supplier TEXT NOT NULL, code TEXT NOT NULL, UNIQUE(supplier,code)
        );
        CREATE TABLE IF NOT EXISTS asset_receipts (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES asset_items(id),
            event_id INTEGER NOT NULL REFERENCES asset_events(id), fingerprint TEXT NOT NULL,
            raw TEXT NOT NULL, parsed TEXT NOT NULL, label_quantity INTEGER,
            actual_quantity INTEGER NOT NULL, duplicate_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS asset_receipt_label ON asset_receipts(fingerprint,id);
        CREATE TRIGGER IF NOT EXISTS asset_receipt_no_update BEFORE UPDATE ON asset_receipts
            BEGIN SELECT RAISE(ABORT,'receipt history is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS asset_receipt_no_delete BEFORE DELETE ON asset_receipts
            BEGIN SELECT RAISE(ABORT,'receipt history is immutable'); END;
    ''')


def write(*fields):
    def decorate(view):
        view.asset_fields = set(fields) | {'request_id'}
        return mutation(view)
    return decorate


def read(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        try:
            return jsonify(ok=True, **view(*args, **kwargs))
        except Invalid as error:
            return jsonify(ok=False, error=error.message), error.status
    return wrapped


def parse_label(raw):
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        raise Invalid('标签内容为空或超过 4096 字符')
    raw = raw.strip()
    if re.fullmatch(r'[UM]:[1-9][0-9]{0,17}', raw):
        return {'type': 'unit' if raw[0] == 'U' else 'item', 'id': int(raw[2:]), 'raw': raw}
    if re.fullmatch(r'C[1-9][0-9]{0,17}', raw, re.I):
        return {'type': 'supplier', 'supplier': 'lcsc-cn', 'code': raw.upper(), 'raw': raw}
    if not (raw.startswith('{') and raw.endswith('}')):
        raise Invalid('无法识别标签；可输入供应商 C 编号或内部标签')
    fields = {}
    for pair in raw[1:-1].split(','):
        key, separator, value = pair.partition(':')
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', key) or key in fields:
            raise Invalid('标签字段重复或格式无效')
        fields[key] = value
    if not re.fullmatch(r'C[1-9][0-9]{0,17}', fields.get('pc', ''), re.I):
        raise Invalid('包装标签缺少有效商品编号 pc')
    quantity = fields.get('qty', '')
    valid = bool(re.fullmatch(r'[1-9][0-9]{0,8}', quantity))
    return {'type': 'package', 'supplier': 'lcsc-cn', 'code': fields['pc'].upper(),
            'model': fields.get('pm', ''), 'order': fields.get('on', ''),
            'label_quantity': int(quantity) if valid else None,
            'quantity_error': None if valid else '标签数量无效，请明确填写实收数量',
            'fields': fields, 'raw': raw}


def fingerprint(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def receipt_history(db, raw):
    return [dict(r) for r in db.execute(
        'SELECT r.id,r.item_id,r.actual_quantity,r.label_quantity,r.duplicate_reason,'
        'e.created_at,e.actor_name,e.reason,e.campus_id FROM asset_receipts r '
        'JOIN asset_events e ON e.id=r.event_id WHERE r.fingerprint=? ORDER BY r.id DESC LIMIT 20',
        (fingerprint(raw),))]


@bp.post('/api/assets/scan/resolve')
@read
def resolve():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {'raw'}:
        raise Invalid('请提交标签原文')
    parsed = parse_label(data['raw'])
    db, item, unit = get_db(), None, None
    if parsed['type'] == 'unit':
        unit = row(db, 'asset_units', parsed['id'])
        item = row(db, 'asset_items', unit['item_id'])
    elif parsed['type'] == 'item':
        item = row(db, 'asset_items', parsed['id'])
    else:
        identifier = db.execute('SELECT item_id FROM asset_identifiers WHERE supplier=? AND code=?',
                                (parsed['supplier'], parsed['code'])).fetchone()
        if identifier:
            item = row(db, 'asset_items', identifier[0])
    history = receipt_history(db, parsed['raw']) if parsed['type'] == 'package' else []
    return {'parsed': parsed, 'item': item, 'unit': unit, 'history': history,
            'receipt_version': history[0]['id'] if history else 0, 'can_manage': manager(),
            'supplier_lookup': {'configured': False, 'message': '国内商品查询尚未配置，包装码解析可正常使用'}}


@bp.post('/api/assets/identifiers')
@write('item_id', 'code', 'reason')
def identify(db, data):
    require_manager()
    item = row(db, 'asset_items', integer(data, 'item_id'))
    code = text(data, 'code').upper()
    if not item['active'] or not re.fullmatch(r'C[1-9][0-9]{0,17}', code):
        raise Invalid('请选择启用物料并填写有效 C 编号')
    reason = text(data, 'reason', maximum=500)
    existing = db.execute("SELECT item_id FROM asset_identifiers WHERE supplier='lcsc-cn' AND code=?", (code,)).fetchone()
    if existing and existing[0] != item['id']:
        raise Invalid('该供应商编号已经属于其他型号，请先核对', 409)
    db.execute("INSERT OR IGNORE INTO asset_identifiers(item_id,supplier,code) VALUES(?,'lcsc-cn',?)", (item['id'], code))
    return {'event_id': event(db, 'identifier', reason, item_id=item['id'], details={'code': code})}


@bp.post('/api/assets/scan/receive')
@write('raw', 'item_id', 'campus_id', 'quantity', 'reason', 'receipt_version', 'duplicate_reason')
def receive(db, data):
    from modules.assets import stock_change
    parsed = parse_label(data.get('raw'))
    if parsed['type'] != 'package':
        raise Invalid('此入口只接受采购包装标签')
    item_id, campus_id, quantity = (integer(data, k) for k in ('item_id', 'campus_id', 'quantity'))
    item, campus = row(db, 'asset_items', item_id), row(db, 'asset_campuses', campus_id)
    if not item['active'] or not campus['active']:
        raise Invalid('物品或校区已停用', 409)
    mapped = db.execute('SELECT item_id FROM asset_identifiers WHERE supplier=? AND code=?',
                        (parsed['supplier'], parsed['code'])).fetchone()
    if not mapped or mapped[0] != item_id:
        raise Invalid('商品编号尚未关联此型号，请由管理人员核对建档', 409)
    reason = text(data, 'reason', maximum=500)
    history = receipt_history(db, parsed['raw'])
    if integer(data, 'receipt_version', 0) != (history[0]['id'] if history else 0):
        raise Invalid('此标签出现新的登记记录，请重新扫描核对', 409)
    duplicate_reason = text(data, 'duplicate_reason', bool(history), 500)
    if item.get('tracking') == 'unit':
        from modules.asset_units import create_units
        result = create_units(db, item, campus_id, quantity, reason)
        event_id = result['event_id']
    elif item.get('tracking') == 'migrating':
        raise Invalid('此型号正在核对迁移', 409)
    elif item['kind'] == 'returnable':
        raise Invalid('非耗材请先完成单件迁移', 409)
    else:
        stock_change(db, item_id, campus_id, quantity)
        event_id = event(db, 'receive', reason, item_id=item_id, campus_id=campus_id,
                         quantity=quantity, details={'deltas': [[campus_id, quantity]], 'source': 'lcsc-cn'})
        result = {'event_id': event_id}
    receipt_id = db.execute(
        'INSERT INTO asset_receipts(item_id,event_id,fingerprint,raw,parsed,label_quantity,actual_quantity,duplicate_reason) '
        'VALUES(?,?,?,?,?,?,?,?)', (item_id, event_id, fingerprint(parsed['raw']), parsed['raw'],
                                  json.dumps(parsed, ensure_ascii=False), parsed['label_quantity'], quantity, duplicate_reason)).lastrowid
    return {**result, 'receipt_id': receipt_id, 'actor_id': current_user()['id'], 'created_at': stamp()}
