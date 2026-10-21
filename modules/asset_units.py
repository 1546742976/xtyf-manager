"""Physical identities, custody and append-only events in the shared database."""
import json
from collections import Counter

from flask import Blueprint, request
from common import current_user, get_db
from modules.assets import Invalid, event, integer, manager, paging, require_manager, row, stamp, text
from modules.asset_scan import read, write

bp = Blueprint('asset_units', __name__)


def migrate(db):
    columns = {r[1] for r in db.execute('PRAGMA table_info(asset_items)')}
    if 'tracking' not in columns:
        # Existing stock must never acquire guessed identities.
        db.execute("ALTER TABLE asset_items ADD COLUMN tracking TEXT NOT NULL DEFAULT 'legacy'")
    db.executescript('''
        CREATE TABLE IF NOT EXISTS asset_objects (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, project_id INTEGER REFERENCES projects(id),
            active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS asset_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL REFERENCES asset_items(id),
            campus_id INTEGER NOT NULL REFERENCES asset_campuses(id), serial TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', keeper_id INTEGER REFERENCES members(id),
            object_id INTEGER REFERENCES asset_objects(id), position TEXT NOT NULL DEFAULT '',
            health TEXT NOT NULL DEFAULT 'normal' CHECK(health IN ('normal','inspection','fault','repair')),
            lifecycle TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle IN ('active','lost','retired','destroyed')),
            version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS asset_unit_item_campus ON asset_units(item_id,campus_id,lifecycle);
        CREATE INDEX IF NOT EXISTS asset_unit_keeper ON asset_units(keeper_id,lifecycle);
        CREATE TABLE IF NOT EXISTS asset_unit_loans (
            id INTEGER PRIMARY KEY, unit_id INTEGER NOT NULL REFERENCES asset_units(id),
            member_id INTEGER NOT NULL REFERENCES members(id), member_name TEXT NOT NULL,
            opened_at TEXT NOT NULL, closed_at TEXT, closed_reason TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS asset_unit_open_loan ON asset_unit_loans(unit_id) WHERE closed_at IS NULL;
        CREATE TABLE IF NOT EXISTS asset_handoffs (
            id INTEGER PRIMARY KEY, unit_id INTEGER NOT NULL REFERENCES asset_units(id),
            sender_id INTEGER NOT NULL REFERENCES members(id), receiver_id INTEGER NOT NULL REFERENCES members(id),
            unit_version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS asset_migration_entries (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES asset_items(id),
            campus_id INTEGER NOT NULL REFERENCES asset_campuses(id), loan_id INTEGER REFERENCES asset_loans(id),
            serial TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS asset_stocktakes (
            id INTEGER PRIMARY KEY, campus_id INTEGER NOT NULL REFERENCES asset_campuses(id),
            scope TEXT NOT NULL CHECK(scope IN ('warehouse','all')), status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL, closed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS asset_stocktake_items (
            take_id INTEGER NOT NULL REFERENCES asset_stocktakes(id), item_id INTEGER NOT NULL REFERENCES asset_items(id),
            PRIMARY KEY(take_id,item_id)
        );
        CREATE TABLE IF NOT EXISTS asset_stocktake_units (
            take_id INTEGER NOT NULL REFERENCES asset_stocktakes(id), unit_id INTEGER NOT NULL REFERENCES asset_units(id),
            snapshot TEXT NOT NULL, seen_at TEXT, seen_by INTEGER REFERENCES members(id),
            PRIMARY KEY(take_id,unit_id)
        );
    ''')


def active_campus(db, identity):
    if not row(db, 'asset_campuses', identity)['active']:
        raise Invalid('校区已停用', 409)


def member(db, identity):
    value = row(db, 'members', identity)
    if not value['is_active'] or value['deleted_at']:
        raise Invalid('请选择启用成员', 409)
    return value


def ensure_unfrozen(db, item_id, campus_id):
    if db.execute("SELECT 1 FROM asset_stocktakes t JOIN asset_stocktake_items i ON i.take_id=t.id "
                  "WHERE t.status='open' AND t.campus_id=? AND i.item_id=?", (campus_id,item_id)).fetchone():
        raise Invalid('此校区型号正在盘点，暂不能流转', 409)


def create_units(db, item, campus_id, quantity, reason, serials=None):
    require_manager()
    if item['tracking'] != 'unit' or not item['active']:
        raise Invalid('请先完成型号的实物核对迁移', 409)
    if quantity > 100:
        raise Invalid('单次最多登记 100 件，请分批核对')
    active_campus(db, campus_id)
    ensure_unfrozen(db, item['id'], campus_id)
    serials = serials or [''] * quantity
    if not isinstance(serials, list) or len(serials) != quantity or any(not isinstance(s,str) or len(s)>120 for s in serials):
        raise Invalid('序列号数量与实收数量不一致')
    ids = [db.execute('INSERT INTO asset_units(item_id,campus_id,serial,created_at) VALUES(?,?,?,?)',
                      (item['id'],campus_id,s,stamp())).lastrowid for s in serials]
    return {'unit_ids': ids, 'event_id': event(db,'unit_receive',reason,item_id=item['id'],campus_id=campus_id,
                                            quantity=quantity,details={'unit_ids': ids, 'serials':serials})}


def open_loan(db, unit_id, keeper):
    db.execute('INSERT INTO asset_unit_loans(unit_id,member_id,member_name,opened_at) VALUES(?,?,?,?)',
               (unit_id,keeper['id'],keeper['name'],stamp()))


def close_loan(db, unit_id, cause):
    db.execute('UPDATE asset_unit_loans SET closed_at=?,closed_reason=? WHERE unit_id=? AND closed_at IS NULL',
               (stamp(),cause,unit_id))


@bp.get('/api/assets/units')
@read
def units():
    db=get_db(); where=[]; args=[]
    for key in ('item_id','campus_id','keeper_id','object_id'):
        if request.args.get(key):
            where.append('u.'+key+'=?');args.append(request.args[key])
    if request.args.get('mine')=='1':
        where.append("(u.keeper_id=? OR EXISTS(SELECT 1 FROM asset_handoffs h WHERE h.unit_id=u.id AND h.status='pending' AND h.receiver_id=?))");args.extend([current_user()['id']]*2)
    if request.args.get('lifecycle'):
        where.append('u.lifecycle=?');args.append(request.args['lifecycle'])
    clause=' WHERE '+' AND '.join(where) if where else ''
    count=db.execute('SELECT COUNT(*) FROM asset_units u'+clause,args).fetchone()[0]
    values=[dict(r) for r in db.execute(
        'SELECT u.*,m.name keeper_name,c.name campus_name,i.name item_name,o.name object_name,'
        "(SELECT h.receiver_id FROM asset_handoffs h WHERE h.unit_id=u.id AND h.status='pending' LIMIT 1) receiver_id "
        'FROM asset_units u JOIN asset_items i ON i.id=u.item_id JOIN asset_campuses c ON c.id=u.campus_id '
        'LEFT JOIN members m ON m.id=u.keeper_id LEFT JOIN asset_objects o ON o.id=u.object_id'+clause+
        ' ORDER BY u.id LIMIT 20 OFFSET ?', (*args,(paging()-1)*20))]
    return {'units':values,'total':count,'page':paging(),'can_manage':manager(),'member_id':current_user()['id']}


@bp.get('/api/assets/units/<int:identity>/history')
@read
def history(identity):
    db=get_db(); row(db,'asset_units',identity)
    records=[dict(r) for r in db.execute(
        "SELECT * FROM asset_events WHERE json_extract(details,'$.unit_id')=? OR "
        "EXISTS(SELECT 1 FROM json_each(details,'$.unit_ids') WHERE value=?) ORDER BY id DESC LIMIT 100", (identity,identity))]
    for record in records:
        record['details']=json.loads(record['details'])
    return {'events':records,'loans':[dict(r) for r in db.execute('SELECT * FROM asset_unit_loans WHERE unit_id=? ORDER BY id DESC',(identity,))]}


@bp.get('/api/assets/units/catalog')
@read
def catalog():
    db=get_db()
    return {'members':[dict(r) for r in db.execute('SELECT id,name FROM members WHERE is_active=1 AND deleted_at IS NULL ORDER BY name,id')],
            'objects':[dict(r) for r in db.execute('SELECT * FROM asset_objects ORDER BY name,id')],
            'projects':[dict(r) for r in db.execute('SELECT id,name FROM projects WHERE deleted_at IS NULL ORDER BY id')],
            'takes':[dict(r) for r in db.execute("SELECT * FROM asset_stocktakes WHERE status='open' ORDER BY id DESC")]}


@bp.post('/api/assets/units/receive')
@write('item_id','campus_id','quantity','reason','serials')
def receive(db,data):
    return create_units(db,row(db,'asset_items',integer(data,'item_id')),integer(data,'campus_id'),
                        integer(data,'quantity'),text(data,'reason',maximum=500),data.get('serials'))


@bp.post('/api/assets/units/operate')
@write('action','units','reason','member_id','campus_id','object_id','position','location','detached','health','destroyed')
def operate(db,data):
    action=text(data,'action'); reason=text(data,'reason',maximum=500)
    allowed={'borrow','return','handoff','accept','cancel_handoff','move','install','detach','fault','repair','healthy','retire','lost','recover','correct'}
    if action not in allowed:
        raise Invalid('不支持的单件操作')
    selections=data.get('units')
    if not isinstance(selections,list) or not 1<=len(selections)<=100:
        raise Invalid('请选择 1 至 100 件设备')
    ids=[]; snapshots=[]
    for selection in selections:
        if not isinstance(selection,dict) or set(selection)!={'id','version'}:
            raise Invalid('单件选择缺少版本')
        identity=integer(selection,'id'); unit=row(db,'asset_units',identity)
        if identity in ids or unit['version']!=integer(selection,'version'):
            raise Invalid('重复选择或设备已发生变化，请重新核对',409)
        item=row(db,'asset_items',unit['item_id'])
        if item['tracking']!='unit' or not item['active']:
            raise Invalid('型号未启用单件管理',409)
        active_campus(db,unit['campus_id']);ensure_unfrozen(db,unit['item_id'],unit['campus_id'])
        ids.append(identity);snapshots.append(unit)
    user=current_user(); admin=manager(); events=[]
    for before in snapshots:
        unit=dict(before); identity=unit['id']
        if action=='recover':
            require_manager()
            if unit['lifecycle'] not in ('lost','retired'):
                raise Invalid('仅未销毁的遗失或误报废设备可恢复',409)
            unit.update(lifecycle='active',health='inspection',keeper_id=None,object_id=None,position='')
        else:
            if unit['lifecycle']!='active':
                raise Invalid('设备已退出在册',409)
            if action not in ('borrow','accept') and not admin and unit['keeper_id']!=user['id']:
                raise Invalid('只能维护本人保管的设备',403)
            if action=='borrow':
                if unit['keeper_id'] or unit['object_id'] or unit['health']!='normal':
                    raise Invalid('设备不在可借状态',409)
                keeper=member(db,integer(data,'member_id') if data.get('member_id') else user['id'])
                if keeper['id']!=user['id'] and not admin:
                    raise Invalid('不能为他人登记借用',403)
                unit['keeper_id']=keeper['id'];open_loan(db,identity,keeper)
            elif action in ('return','retire','lost'):
                if action in ('retire','lost'):require_manager()
                if action=='return' and not unit['keeper_id']:
                    raise Invalid('设备没有待还借用',409)
                if unit['object_id'] and data.get('detached') is not True:
                    raise Invalid('请明确确认设备已经拆卸',409)
                unit.update(object_id=None,position='',keeper_id=None)
                close_loan(db,identity,action)
                if action=='return':
                    unit['health']='inspection' if data.get('health') in ('fault','inspection') or unit['health']!='normal' else 'normal'
                    unit['campus_id']=integer(data,'campus_id')
                else:
                    unit['lifecycle']='destroyed' if action=='retire' and data.get('destroyed') is True else ('retired' if action=='retire' else 'lost')
            elif action=='handoff':
                if not unit['keeper_id']:raise Invalid('设备尚未借出',409)
                receiver=member(db,integer(data,'member_id'))
                if receiver['id']==unit['keeper_id']:raise Invalid('接收人与当前保管人相同')
                db.execute("UPDATE asset_handoffs SET status='cancelled' WHERE unit_id=? AND status='pending'",(identity,))
                db.execute('INSERT INTO asset_handoffs(unit_id,sender_id,receiver_id,unit_version,created_at) VALUES(?,?,?,?,?)',
                           (identity,unit['keeper_id'],receiver['id'],unit['version']+1,stamp()))
            elif action=='accept':
                handoff=db.execute("SELECT * FROM asset_handoffs WHERE unit_id=? AND status='pending'",(identity,)).fetchone()
                if not handoff or handoff['receiver_id']!=user['id'] or handoff['unit_version']!=unit['version']:
                    raise Invalid('移交已失效或当前成员不是接收人',409)
                close_loan(db,identity,'handoff');open_loan(db,identity,member(db,user['id']))
                unit['keeper_id']=user['id']
                db.execute("UPDATE asset_handoffs SET status='accepted' WHERE id=?",(handoff['id'],))
            elif action=='move':
                if unit['object_id'] and not admin:
                    raise Invalid('已装车设备请由管理人员登记整车移动或先拆卸',409)
                unit['campus_id']=integer(data,'campus_id')
            elif action=='install':
                if not unit['keeper_id'] or unit['object_id'] or unit['health']!='normal':
                    raise Invalid('请先借用正常设备，或先拆卸原安装关系',409)
                obj=row(db,'asset_objects',integer(data,'object_id'))
                if not obj['active']:raise Invalid('使用对象已停用',409)
                unit.update(object_id=obj['id'],position=text(data,'position',False))
            elif action=='detach':
                if not unit['object_id']:raise Invalid('设备没有安装关系',409)
                unit.update(object_id=None,position='',location=text(data,'location'))
            elif action=='fault':
                unit['health']='inspection'
            elif action in ('repair','healthy'):
                require_manager()
                if action=='repair' and unit['object_id']:
                    if data.get('detached') is not True:raise Invalid('送修前请明确确认已拆卸',409)
                    unit.update(object_id=None,position='')
                unit['health']='repair' if action=='repair' else 'normal'
            elif action=='correct':
                require_manager()
                keeper=member(db,integer(data,'member_id')) if data.get('member_id') else None
                if unit['object_id'] and keeper is None:raise Invalid('已装车设备需保留责任人或先拆卸',409)
                close_loan(db,identity,'correction')
                if keeper:open_loan(db,identity,keeper)
                unit['keeper_id']=keeper['id'] if keeper else None
        if 'location' in data:unit['location']=text(data,'location',False)
        active_campus(db,unit['campus_id']);ensure_unfrozen(db,unit['item_id'],unit['campus_id'])
        if action not in ('handoff','accept'):
            db.execute("UPDATE asset_handoffs SET status='cancelled' WHERE unit_id=? AND status='pending'",(identity,))
        fields=('campus_id','location','keeper_id','object_id','position','health','lifecycle')
        db.execute('UPDATE asset_units SET '+','.join(k+'=?' for k in fields)+',version=version+1 WHERE id=?',
                   (*(unit[k] for k in fields),identity))
        unit['version']+=1
        keeper=row(db,'members',unit['keeper_id']) if unit['keeper_id'] else None
        events.append(event(db,'unit_'+action,reason,item_id=unit['item_id'],campus_id=unit['campus_id'],quantity=1,
                            member_id=unit['keeper_id'],member_name=keeper['name'] if keeper else '',
                            details={'unit_id':identity,'before':before,'after':unit}))
    return {'event_ids':events,'unit_ids':ids}


@bp.post('/api/assets/units/migrate')
@write('item_id','action','reason','campus_id','loan_id','quantity','serials','version')
def migrate_item(db,data):
    require_manager();item=row(db,'asset_items',integer(data,'item_id'))
    reason=text(data,'reason',maximum=500);action=text(data,'action')
    if item['kind']!='returnable' or item['tracking']=='unit':raise Invalid('此型号不需要迁移',409)
    if action=='start':
        if item['tracking']!='legacy' or item['version']!=integer(data,'version'):raise Invalid('型号状态已变化',409)
        db.execute("UPDATE asset_items SET tracking='migrating',version=version+1 WHERE id=?",(item['id'],))
    elif item['tracking']!='migrating':raise Invalid('请先冻结此型号并开始核对',409)
    elif action=='cancel':
        db.execute('DELETE FROM asset_migration_entries WHERE item_id=?',(item['id'],))
        db.execute("UPDATE asset_items SET tracking='legacy',version=version+1 WHERE id=?",(item['id'],))
    elif action=='add':
        campus=integer(data,'campus_id');active_campus(db,campus)
        quantity=integer(data,'quantity');loan_id=data.get('loan_id') or None
        if quantity>100:raise Invalid('单次核对最多 100 件')
        if loan_id:
            loan=row(db,'asset_loans',integer(data,'loan_id'))
            if loan['item_id']!=item['id'] or loan['campus_id']!=campus:raise Invalid('借单与型号校区不匹配')
        serials=data.get('serials') or ['']*quantity
        if not isinstance(serials,list) or len(serials)!=quantity or any(not isinstance(s,str) or len(s)>120 for s in serials):raise Invalid('序列号内容无效')
        db.executemany('INSERT INTO asset_migration_entries(item_id,campus_id,loan_id,serial) VALUES(?,?,?,?)',
                       [(item['id'],campus,loan_id,s) for s in serials])
    elif action=='finish':
        entries=[dict(r) for r in db.execute('SELECT * FROM asset_migration_entries WHERE item_id=?',(item['id'],))]
        stock=Counter({(r['campus_id'],None):r['available'] for r in db.execute('SELECT * FROM asset_stock WHERE item_id=?',(item['id'],)) if r['available']})
        loans={r['id']:dict(r) for r in db.execute('SELECT * FROM asset_loans WHERE item_id=? AND outstanding>0',(item['id'],))}
        stock.update({(r['campus_id'],r['id']):r['outstanding'] for r in loans.values()})
        if Counter((r['campus_id'],r['loan_id']) for r in entries)!=stock:raise Invalid('核对数量与各校区库存、每笔未还借单不一致',409)
        unit_ids=[]
        for entry in entries:
            keeper=member(db,loans[entry['loan_id']]['member_id']) if entry['loan_id'] else None
            identity=db.execute('INSERT INTO asset_units(item_id,campus_id,serial,keeper_id,created_at) VALUES(?,?,?,?,?)',
                                (item['id'],entry['campus_id'],entry['serial'],keeper['id'] if keeper else None,stamp())).lastrowid
            if keeper:open_loan(db,identity,keeper)
            unit_ids.append(identity)
        db.execute("UPDATE asset_items SET tracking='unit',version=version+1 WHERE id=?",(item['id'],))
        # Original balances, outstanding loans and all old events remain a historical snapshot.
        return {'unit_ids':unit_ids,'event_id':event(db,'unit_migration',reason,item_id=item['id'],quantity=len(unit_ids),details={'unit_ids':unit_ids,'mapping':entries})}
    else:raise Invalid('迁移操作无效')
    return {'event_id':event(db,'migration_'+action,reason,item_id=item['id'],details=data)}


@bp.get('/api/assets/units/migration/<int:item_id>')
@read
def migration_status(item_id):
    require_manager();db=get_db()
    return {'item':row(db,'asset_items',item_id),
            'stock':[dict(r) for r in db.execute('SELECT * FROM asset_stock WHERE item_id=?',(item_id,))],
            'loans':[dict(r) for r in db.execute('SELECT * FROM asset_loans WHERE item_id=? AND outstanding>0',(item_id,))],
            'entries':[dict(r) for r in db.execute('SELECT * FROM asset_migration_entries WHERE item_id=?',(item_id,))]}
