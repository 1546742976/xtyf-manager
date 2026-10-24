"""Quantity inventory in the application's existing database; no client-side stock."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, render_template, request

from common import ROLE_NAMES, current_user, get_db, login_required
from modules.security import has_permission

bp = Blueprint("assets", __name__)
MAX_QUANTITY = 1_000_000_000
MAX_ASSET_REQUEST_BYTES = 16 * 1024



def migrate_assets(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS asset_campuses (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS asset_items (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, specification TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('returnable','consumable')),
            active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS asset_categories (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1
        );
        INSERT OR IGNORE INTO asset_categories(name)
            SELECT DISTINCT category FROM asset_items WHERE category<>'';
        DROP INDEX IF EXISTS asset_item_identity;
        CREATE INDEX asset_item_identity ON asset_items(name,specification);
        CREATE TABLE IF NOT EXISTS asset_stock (
            item_id INTEGER NOT NULL REFERENCES asset_items(id),
            campus_id INTEGER NOT NULL REFERENCES asset_campuses(id),
            available INTEGER NOT NULL DEFAULT 0 CHECK(available BETWEEN 0 AND 1000000000),
            version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(item_id,campus_id)
        );
        CREATE TABLE IF NOT EXISTS asset_loans (
            id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES asset_items(id),
            campus_id INTEGER NOT NULL REFERENCES asset_campuses(id),
            member_id INTEGER NOT NULL REFERENCES members(id), member_name TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity>0),
            outstanding INTEGER NOT NULL CHECK(outstanding>=0 AND outstanding<=quantity),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS asset_loan_stock ON asset_loans(item_id,campus_id,outstanding);
        CREATE INDEX IF NOT EXISTS asset_loan_member ON asset_loans(member_id,outstanding);
        CREATE TABLE IF NOT EXISTS asset_events (
            id INTEGER PRIMARY KEY, item_id INTEGER REFERENCES asset_items(id),
            campus_id INTEGER REFERENCES asset_campuses(id),
            actor_id INTEGER NOT NULL REFERENCES members(id), actor_name TEXT NOT NULL,
            member_id INTEGER REFERENCES members(id), member_name TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT NOT NULL,
            consumed_delta INTEGER NOT NULL DEFAULT 0,
            corrects_id INTEGER UNIQUE REFERENCES asset_events(id)
        );
        CREATE INDEX IF NOT EXISTS asset_event_item ON asset_events(item_id,id);
        CREATE TABLE IF NOT EXISTS asset_requests (
            actor_id INTEGER NOT NULL REFERENCES members(id), request_id TEXT NOT NULL,
            payload TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(actor_id,request_id)
        );
        CREATE TRIGGER IF NOT EXISTS asset_event_no_update BEFORE UPDATE ON asset_events
            BEGIN SELECT RAISE(ABORT,'asset history is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS asset_event_no_delete BEFORE DELETE ON asset_events
            BEGIN SELECT RAISE(ABORT,'asset history is immutable'); END;
    """)
    from modules.asset_scan import migrate as migrate_scan
    migrate_scan(db)
    from modules.asset_units import migrate as migrate_units
    migrate_units(db)
    if "supply_status" not in {r[1] for r in db.execute("PRAGMA table_info(asset_stock)")}:
        db.execute("ALTER TABLE asset_stock ADD COLUMN supply_status TEXT NOT NULL DEFAULT 'low' "
                   "CHECK(supply_status IN ('sufficient','low'))")
    db.commit()


class Invalid(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def manager(user=None):
    user = user or current_user()
    return bool(user and (user["role"] in ("admin", "manager") or has_permission("assets.manage", user)))


def require_manager():
    if not manager():
        raise Invalid("仅资产管理员可执行此操作", 403)


def integer(data, key, minimum=1):
    value = data.get(key)
    if type(value) is not int or not minimum <= value <= MAX_QUANTITY:
        raise Invalid(f"{key} 必须是 {minimum} 至 {MAX_QUANTITY} 的整数")
    return value


def text(data, key, required=True, maximum=120):
    value = data.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > maximum or (required and not value.strip()):
        raise Invalid(f"{key} 内容无效")
    return value.strip()


def row(db, table, identity):
    value = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    if value is None:
        raise Invalid("记录不存在", 404)
    return dict(value)


def stamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def mutation(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        db = get_db()
        try:
            if request.content_length is not None and request.content_length > MAX_ASSET_REQUEST_BYTES:
                raise Invalid("资产请求体超过 16 KiB 限制", 413)
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                raise Invalid("请求必须是 JSON 对象")
            catalog_fields = {"request_id", "name", "specification", "category", "unit",
                              "kind", "id", "version", "active", "supplier_code", "inventory", "reason", "consumable_mode", "returnable_mode", "force_new"}
            operation_fields = {"request_id", "action", "reason", "occurred_at", "quantity",
                                "loan_id", "item_id", "campus_id", "target_campus_id",
                                "version", "event_id"}
            allowed = getattr(view, 'asset_fields', catalog_fields if request.endpoint == "assets.save_catalog" else operation_fields)
            if request.endpoint == "assets.supply_status":
                allowed = {"request_id", "item_id", "campus_id", "status", "version", "item_version"}
            if request.endpoint == "assets.assign_categories":
                allowed = {"request_id", "items", "category_id", "category_version"}
            if set(data) - allowed:
                raise Invalid("资产请求包含不支持的字段")
            key = text(data, "request_id", maximum=100)
            user = current_user()
            payload = json.dumps([request.path, data], sort_keys=True, ensure_ascii=False)
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT payload,result FROM asset_requests WHERE actor_id=? AND request_id=?",
                (user["id"], key),
            ).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise Invalid("重复请求标识对应了不同内容，请重新操作", 409)
                db.rollback()
                return jsonify(json.loads(previous["result"]))
            result = dict(ok=True, **view(db, data, *args, **kwargs))
            db.execute("INSERT INTO asset_requests VALUES(?,?,?,?)",
                       (user["id"], key, payload, json.dumps(result)))
            db.commit()
            return jsonify(result)
        except Invalid as error:
            db.rollback()
            return jsonify(ok=False, error=error.message), error.status
        except sqlite3.IntegrityError:
            db.rollback()
            return jsonify(ok=False, error="名称规格重复、数量越界或该记录已纠正，请刷新核对"), 409
        except sqlite3.OperationalError:
            db.rollback()
            return jsonify(ok=False, error="数据库暂时无法保存，请保留内容后重试"), 503
        except Exception:
            db.rollback()
            raise
    return wrapped


def event(db, action, reason, *, item_id=None, campus_id=None, quantity=0,
          member_id=None, member_name="", details=None, consumed_delta=0, corrects_id=None):
    user = current_user()
    return db.execute(
        "INSERT INTO asset_events(item_id,campus_id,actor_id,actor_name,member_id,member_name,"
        "action,quantity,reason,created_at,details,consumed_delta,corrects_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (item_id, campus_id, user["id"], user["name"], member_id, member_name, action,
         quantity, reason, stamp(), json.dumps(details or {}, ensure_ascii=False), consumed_delta, corrects_id),
    ).lastrowid


@bp.get("/assets")
@bp.get("/assets/detail")
@login_required
def page():
    return render_template("assets.html", me=current_user(), page="assets", role_names=ROLE_NAMES)


@bp.get("/api/assets/catalog")
@login_required
def catalog():
    db = get_db()
    return jsonify(ok=True, can_manage=manager(), intake_enabled=True, server_time=stamp(),
                   member_id=current_user()["id"],
                   categories=[dict(r) for r in db.execute("SELECT * FROM asset_categories ORDER BY name,id")],
                   campuses=[dict(r) for r in db.execute("SELECT * FROM asset_campuses ORDER BY name,id")])


@bp.post("/api/assets/catalog/<kind>")
@mutation
def save_catalog(db, data, kind):
    result = save_catalog_record(db, data, kind)
    if "inventory" in data:
        if kind != "items":
            raise Invalid("只有物品可以调整库存")
        inventory = data["inventory"]
        if not isinstance(inventory, dict) or set(inventory) - {"campus_id", "new_campus", "quantity", "version", "reason"}:
            raise Invalid("校区库存参数无效")
        campus_id = inventory_campus(db, inventory)
        item = row(db, "asset_items", result["id"])
        if item["tracking"] in ("unit", "status"):
            raise Invalid("逐件或状态物品不通过数量调整维护", 409)
        operate_record(db, {**inventory, "campus_id": campus_id, "item_id": result["id"], "action": "adjust"})
    return result


def inventory_campus(db, data):
    """An optional new campus is created inside the caller's inventory transaction."""
    if data.get("new_campus"):
        require_manager()
        if data.get("campus_id"):
            raise Invalid("新建校区与现有校区不能同时指定")
        return save_catalog_record(db, {"name": text(data, "new_campus")}, "campuses")["id"]
    if not data.get("campus_id"):
        raise Invalid("请选择校区或填写新校区")
    return integer(data, "campus_id")


def save_catalog_record(db, data, kind):
    require_manager()
    if kind not in ("items", "campuses", "categories"):
        raise Invalid("目录类型不存在", 404)
    table = "asset_" + kind
    fields = {"name": text(data, "name")}
    if kind == "items":
        fields.update(specification=text(data, "specification", False), category=text(data, "category", False),
                      unit=text(data, "unit", maximum=12), kind=text(data, "kind"))
        if fields["kind"] not in ("returnable", "consumable"):
            raise Invalid("物品类型无效")
        if fields["category"]:
            category = db.execute("SELECT * FROM asset_categories WHERE name=?", (fields["category"],)).fetchone()
            if category and not category["active"]:
                raise Invalid("该类别已停用，请选择启用类别", 409)
            if not category:
                db.execute("INSERT INTO asset_categories(name) VALUES(?)", (fields["category"],))
    identity = data.get("id")
    force_new = data.get("force_new", False)
    if type(force_new) is not bool:
        raise Invalid("另建档案确认参数无效")
    if force_new:
        text(data, "reason", maximum=500)
    if kind == 'items' and identity is None:
        fields['tracking'] = 'unit' if fields['kind'] == 'returnable' else 'quantity'
    before = None
    previous_stock = None
    if kind == "items" and "consumable_mode" in data:
        mode = text(data, "consumable_mode")
        if mode not in ("quantity", "status") or (fields["kind"] != "consumable" and mode != "quantity"):
            raise Invalid("仅耗材可选择充足／不足管理")
        if fields["kind"] == "consumable":
            fields["tracking"] = mode
    if kind == "items" and "returnable_mode" in data:
        mode = text(data, "returnable_mode")
        if fields["kind"] != "returnable" or mode not in ("unit", "quantity"):
            raise Invalid("可借用品请选择逐件或按数量管理")
        fields["tracking"] = mode
    if kind == "items":
        existing_identity = row(db, table, integer(data, "id")) if identity is not None else None
        unchanged_identity = existing_identity and all(existing_identity[k] == fields[k] for k in ("name", "specification"))
        duplicate = db.execute(
            "SELECT id FROM asset_items WHERE name=? AND specification=? AND id<>? ORDER BY id LIMIT 1",
            (fields["name"], fields["specification"], identity or 0),
        ).fetchone()
        if duplicate and not unchanged_identity and not force_new:
            raise Invalid(f"已有同名同规格档案 M:{duplicate['id']}；请核对后明确另建档案并填写原因", 409)
    if identity is not None:
        identity = integer(data, "id")
        before = row(db, table, identity)
        if integer(data, "version") != before["version"]:
            raise Invalid("目录已更新，请刷新后重试", 409)
        active = data.get("active", True)
        if type(active) is not bool:
            raise Invalid("启用状态无效")
        fields["active"] = int(active)
        column = "item_id" if kind == "items" else "campus_id"
        if kind == "categories":
            if not active and db.execute(
                "SELECT 1 FROM asset_items WHERE category=? AND active=1", (before["name"],)
            ).fetchone():
                raise Invalid("类别下仍有启用物品，请先更换物品类别或停用物品", 409)
            if before["name"] != fields["name"]:
                # A rename also invalidates stale item editors, without rewriting history.
                db.execute("UPDATE asset_items SET category=?,version=version+1 WHERE category=?",
                           (fields["name"], before["name"]))
        elif not active and (
            db.execute(f"SELECT 1 FROM asset_stock s JOIN asset_items i ON i.id=s.item_id WHERE s.{column}=? AND available>0 AND i.tracking NOT IN ('unit','status')", (identity,)).fetchone()
            or db.execute(f"SELECT 1 FROM asset_loans s JOIN asset_items i ON i.id=s.item_id WHERE s.{column}=? AND outstanding>0 AND i.tracking<>'unit'", (identity,)).fetchone()
            or db.execute(f"SELECT 1 FROM asset_units WHERE {column}=? AND lifecycle='active'", (identity,)).fetchone()
        ):
            raise Invalid("仍有库存或未还借单，不能停用", 409)
        if kind == "items" and any(before[k] != fields[k] for k in ("unit", "kind")):
            has_history = db.execute("SELECT 1 FROM asset_events WHERE item_id=? AND action<>'catalog'", (identity,)).fetchone()
            if has_history and (before["kind"] != fields["kind"] or current_user()["role"] != "admin" or not data.get("reason")):
                raise Invalid("已有历史记录，不能改变单位或借还类型", 409)
            if before["kind"] != fields["kind"]:
                fields.setdefault("tracking", "unit" if fields["kind"] == "returnable" else "quantity")
        if kind == "items" and fields.get("tracking", before["tracking"]) != before["tracking"]:
            target = fields["tracking"]
            if before["tracking"] == "migrating":
                raise Invalid("此物料正在核对实物，请先完成或取消核对", 409)
            if before["tracking"] == "unit":
                raise Invalid("已采用逐件管理，不能通过编辑资料取消单件身份", 409)
            if before["kind"] == "returnable" and target == "unit":
                raise Invalid("请从物料详情的“切换为逐件管理”核对现有库存与借单", 409)
            if before["kind"] == "returnable" and target == "quantity":
                if (db.execute("SELECT 1 FROM asset_units WHERE item_id=?", (identity,)).fetchone()
                        or db.execute("SELECT 1 FROM asset_migration_entries WHERE item_id=?", (identity,)).fetchone()):
                    raise Invalid("存在单件身份或未完成核对，不能直接改为按数量管理", 409)
                text(data, "reason", maximum=500)
            if target == "status" and db.execute(
                "SELECT 1 FROM asset_identifiers WHERE item_id=? UNION ALL "
                "SELECT 1 FROM asset_code_aliases WHERE namespace='lcsc-cn' AND item_id=? LIMIT 1",
                (identity, identity),
            ).fetchone():
                raise Invalid("嘉立创物料继续按数量管理", 409)
            if target == "status" or before["tracking"] == "status":
                text(data, "reason", maximum=500)
                previous_stock = [dict(r) for r in db.execute("SELECT * FROM asset_stock WHERE item_id=?", (identity,))]
                if before["tracking"] == "status":
                    for stock in previous_stock:
                        event(db, "adjust", "恢复数量管理，清零历史余额后重新盘点", item_id=identity,
                              campus_id=stock["campus_id"], quantity=0,
                              details={"deltas": [[stock["campus_id"], -stock["available"]]], "mode_reset": True})
                # Counts kept while in status mode are historical, never a live balance on return.
                db.execute("UPDATE asset_stock SET supply_status='low',version=version+1" +
                           (",available=0" if before["tracking"] == "status" else "") + " WHERE item_id=?", (identity,))
        db.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in fields) +
                   ",version=version+1 WHERE id=?", (*fields.values(), identity))
    else:
        identity = db.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                              tuple(fields.values())).lastrowid
    if kind == 'items' and data.get('supplier_code'):
        if row(db, "asset_items", identity)["tracking"] == "status":
            raise Invalid("嘉立创物料继续按数量管理", 409)
        from modules.asset_scan import exact_matches, parse_label
        parsed = parse_label(data['supplier_code'])
        if parsed['type'] != 'supplier':
            raise Invalid('请填写供应商 C 编号')
        matches = exact_matches(db, parsed)
        if parsed['raw'] != parsed['code']:
            matches += exact_matches(db, {**parsed, 'raw': parsed['code']})
        if any(m['target_type'] != 'item' or m['target_id'] != identity for m in matches):
            raise Invalid('供应商编号已关联其他物料或实物，请通过扫码核对后明确另建', 409)
        db.execute('INSERT OR IGNORE INTO asset_identifiers(item_id,supplier,code) VALUES(?,?,?)',
                   (identity, parsed['supplier'], parsed['code']))
    event(db, "catalog", text(data, "reason", maximum=500) if data.get("reason") else "目录维护", item_id=identity if kind == "items" else None,
          campus_id=identity if kind == "campuses" else None,
          details={"kind": kind, "id": identity, "before": before, "after": fields, "force_new": force_new,
                   **({"previous_stock": previous_stock} if previous_stock is not None else {})})
    return {"id": identity}


@bp.post("/api/assets/categories/assign")
@mutation
def assign_categories(db, data):
    require_manager()
    selections = data.get("items")
    if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
        raise Invalid("请选择 1 至 100 种物料")
    if "category_id" not in data:
        raise Invalid("请选择目标类别或明确移至未分类")
    target = data.get("category_id")
    category = None
    if target is not None:
        category = row(db, "asset_categories", integer(data, "category_id"))
        if not category["active"] or category["version"] != integer(data, "category_version"):
            raise Invalid("目标类别已更新或停用，请刷新后重试", 409)
    elif data.get("category_version") is not None:
        raise Invalid("未分类不应指定类别版本")
    chosen, seen = [], set()
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"id", "version"}:
            raise Invalid("所选物料参数无效")
        identity = integer(selection, "id")
        if identity in seen:
            raise Invalid("同一物料不能重复选择")
        seen.add(identity)
        item = row(db, "asset_items", identity)
        if item["version"] != integer(selection, "version"):
            raise Invalid("所选物料已更新，请刷新后重试", 409)
        chosen.append(item)
    name = category["name"] if category else ""
    changed = []
    for item in chosen:
        if item["category"] == name:
            continue
        db.execute("UPDATE asset_items SET category=?,version=version+1 WHERE id=?", (name, item["id"]))
        event(db, "catalog", "批量整理物料类别", item_id=item["id"],
              details={"kind": "items", "id": item["id"], "before": {"category": item["category"], "version": item["version"]},
                       "after": {"category": name, "version": item["version"] + 1}, "category_id": target})
        changed.append(item["id"])
    return {"changed": changed, "selected": len(chosen), "category_id": target}


def stock_change(db, item_id, campus_id, delta):
    db.execute("INSERT OR IGNORE INTO asset_stock(item_id,campus_id) VALUES(?,?)", (item_id, campus_id))
    current = db.execute("SELECT available FROM asset_stock WHERE item_id=? AND campus_id=?",
                         (item_id, campus_id)).fetchone()[0]
    if not 0 <= current + delta <= MAX_QUANTITY:
        raise Invalid("在库数量不足或结果超出数量上限，请刷新核对", 409)
    db.execute("UPDATE asset_stock SET available=available+?,version=version+1 WHERE item_id=? AND campus_id=?",
               (delta, item_id, campus_id))


@bp.post("/api/assets/supply-status")
@mutation
def supply_status(db, data):
    item_id, campus_id = (integer(data, k) for k in ("item_id", "campus_id"))
    item, campus = row(db, "asset_items", item_id), row(db, "asset_campuses", campus_id)
    if not item["active"] or not campus["active"] or item["tracking"] != "status":
        raise Invalid("请选择启用的状态耗材与校区", 409)
    if integer(data, "item_version") != item["version"]:
        raise Invalid("物料资料已变化，请刷新后核对", 409)
    value = text(data, "status")
    if value not in ("sufficient", "low"):
        raise Invalid("耗材状态只能是充足或不足")
    stock = db.execute("SELECT supply_status,version FROM asset_stock WHERE item_id=? AND campus_id=?",
                       (item_id, campus_id)).fetchone()
    if integer(data, "version", 0) != (stock["version"] if stock else 0):
        raise Invalid("耗材状态已变化，请刷新后核对", 409)
    before = stock["supply_status"] if stock else "low"
    db.execute("INSERT OR IGNORE INTO asset_stock(item_id,campus_id) VALUES(?,?)", (item_id, campus_id))
    db.execute("UPDATE asset_stock SET supply_status=?,version=version+1 WHERE item_id=? AND campus_id=?",
               (value, item_id, campus_id))
    return {"event_id": event(db, "supply_status", "补充后标记充足" if value == "sufficient" else "发现余量不足",
                              item_id=item_id, campus_id=campus_id,
                              details={"before": before, "after": value})}


@bp.post("/api/assets/operations")
@mutation
def operate(db, data):
    return operate_record(db, data)


def operate_record(db, data):
    action = text(data, "action")
    reason = text(data, "reason", True, 500)
    occurred_at = data.get("occurred_at")
    if occurred_at is not None:
        try:
            occurred = datetime.strptime(occurred_at, "%Y-%m-%dT%H:%M")
        except (TypeError, ValueError):
            raise Invalid("登记时间必须为北京时间，格式为 YYYY-MM-DDTHH:MM") from None
        if occurred > datetime.strptime(stamp(), "%Y-%m-%d %H:%M:%S"):
            raise Invalid("登记时间不能晚于当前北京时间")
        occurred_at = occurred.strftime("%Y-%m-%d %H:%M:%S")
    user = current_user()
    if action == "correct":
        require_manager()
        original = row(db, "asset_events", integer(data, "event_id"))
        if original["action"] not in ("receive", "adjust", "transfer", "consume", "borrow", "return"):
            raise Invalid("此记录不能撤销；请登记新的业务操作")
        if db.execute("SELECT 1 FROM asset_events WHERE corrects_id=?", (original["id"],)).fetchone():
            raise Invalid("该记录已经纠正", 409)
        details = json.loads(original["details"])
        if details.get("mode_reset"):
            raise Invalid("管理方式切换不恢复旧数量，请重新盘点", 409)
        item_id, campus_id = original["item_id"], original["campus_id"]
        if row(db,'asset_items',item_id)['tracking'] in ('unit','migrating','status'):
            raise Invalid('此物料不使用数量流水纠错，请核对当前管理方式',409)
        deltas = [(c, -d) for c, d in details["deltas"]]
        if not row(db, "asset_items", item_id)["active"]:
            raise Invalid("纠错涉及已停用物品，请先启用后核对", 409)
        for affected_campus in {campus for campus, _ in deltas}:
            if not row(db, "asset_campuses", affected_campus)["active"]:
                raise Invalid("纠错涉及已停用校区，请先启用后核对", 409)
            # An absolute stock count supersedes all earlier stock movements:
            # undoing them afterward would silently invalidate the count.
            if db.execute(
                "SELECT 1 FROM asset_events WHERE item_id=? AND campus_id=? "
                "AND action='adjust' AND id>? LIMIT 1",
                (item_id, affected_campus, original["id"]),
            ).fetchone():
                raise Invalid("后续已有库存盘点，不能直接撤销此前记录；请重新盘点", 409)
        loan_id, loan_delta = details.get("loan_id"), -details.get("loan_delta", 0)
        if loan_id:
            loan = row(db, "asset_loans", loan_id)
            if not 0 <= loan["outstanding"] + loan_delta <= loan["quantity"]:
                raise Invalid("借单已有后续操作，请先核对后续记录", 409)
            db.execute("UPDATE asset_loans SET outstanding=outstanding+? WHERE id=?", (loan_delta, loan_id))
        for campus, delta in deltas:
            stock_change(db, item_id, campus, delta)
        return {"event_id": event(db, action, reason, item_id=item_id, campus_id=campus_id,
                                  quantity=original["quantity"], member_id=original["member_id"],
                                  member_name=original["member_name"], corrects_id=original["id"],
                                  consumed_delta=-original["consumed_delta"],
                                  details={"deltas": deltas, "loan_id": loan_id, "loan_delta": loan_delta})}
    quantity = integer(data, "quantity", 0 if action == "adjust" else 1)
    loan_id, loan_delta = None, 0
    member_id, member_name = user["id"], user["name"]
    if action == "return":
        loan_id = integer(data, "loan_id")
        loan = row(db, "asset_loans", loan_id)
        if loan["member_id"] != user["id"]:
            raise Invalid("只能归还自己的借单", 403)
        if quantity > loan["outstanding"]:
            raise Invalid("归还数量超过未还数量", 409)
        item_id, campus_id = loan["item_id"], loan["campus_id"]
        loan_delta, deltas = -quantity, [(campus_id, quantity)]
        db.execute("UPDATE asset_loans SET outstanding=outstanding-? WHERE id=?", (quantity, loan_id))
    else:
        item_id, campus_id = integer(data, "item_id"), integer(data, "campus_id")
        item, campus = row(db, "asset_items", item_id), row(db, "asset_campuses", campus_id)
        if not item["active"] or not campus["active"]:
            raise Invalid("物品或校区已停用", 409)
        if action in ("receive", "adjust", "transfer"):
            if action == "transfer":
                require_manager()
            member_id, member_name = None, ""
        if action == "receive":
            deltas = [(campus_id, quantity)]
        elif action == "adjust":
            stock = db.execute("SELECT available,version FROM asset_stock WHERE item_id=? AND campus_id=?",
                               (item_id, campus_id)).fetchone()
            version = stock["version"] if stock else 0
            if integer(data, "version", 0) != version:
                raise Invalid("库存已发生变化，请刷新后重新盘点", 409)
            deltas = [(campus_id, quantity - (stock["available"] if stock else 0))]
        elif action == "transfer":
            target = integer(data, "target_campus_id")
            if target == campus_id or not row(db, "asset_campuses", target)["active"]:
                raise Invalid("请选择其他启用校区")
            deltas = [(campus_id, -quantity), (target, quantity)]
        elif action in ("borrow", "consume"):
            expected = "returnable" if action == "borrow" else "consumable"
            if item["kind"] != expected:
                raise Invalid("操作与物品类型不匹配")
            deltas = [(campus_id, -quantity)]
            if action == "borrow":
                loan_delta = quantity
                loan_id = db.execute(
                    "INSERT INTO asset_loans(item_id,campus_id,member_id,member_name,quantity,outstanding,created_at)"
                    " VALUES(?,?,?,?,?,?,?)", (item_id, campus_id, user["id"], user["name"], quantity, quantity, stamp()),
                ).lastrowid
        else:
            raise Invalid("操作类型无效")
    if row(db,'asset_items',item_id)['tracking'] in ('unit','migrating','status'):
        raise Invalid('此物料采用单件、状态管理或正在迁移，不能登记数量',409)
    for campus, delta in deltas:
        stock_change(db, item_id, campus, delta)
    return {"event_id": event(db, action, reason, item_id=item_id, campus_id=campus_id, quantity=quantity,
                              member_id=member_id, member_name=member_name,
                              consumed_delta=quantity if action == "consume" else 0,
                              details={"deltas": deltas, "loan_id": loan_id, "loan_delta": loan_delta,
                                       "occurred_at": occurred_at or stamp()}),
            "loan_id": loan_id}


def paging():
    try:
        return max(1, min(int(request.args.get("page", 1)), 1_000_000))
    except ValueError:
        return 1


def paginated(total, default=20):
    """Clamp after filtering, retaining each endpoint's legacy page size."""
    supplied = request.args.get("per_page")
    try:
        per_page = int(supplied) if supplied is not None else default
    except (TypeError, ValueError):
        per_page = default
    if per_page not in (20, 50, 100):
        per_page = default
    return min(paging(), max(1, (total + per_page - 1) // per_page)), per_page


def totals(db):
    # Fixed query count, independent of the number of items/campuses.
    own_snapshot = not db.in_transaction
    if own_snapshot:
        db.execute("BEGIN")
    try:
        balances = {(r["item_id"], r["campus_id"]): dict(r) for r in db.execute("SELECT s.*,i.tracking FROM asset_stock s JOIN asset_items i ON i.id=s.item_id WHERE i.tracking<>'unit'")}
        for r in db.execute("SELECT item_id,campus_id,SUM(outstanding) borrowed FROM asset_loans JOIN asset_items i ON i.id=item_id WHERE i.tracking<>'unit' GROUP BY item_id,campus_id"):
            balances.setdefault((r["item_id"], r["campus_id"]), {"available": 0, "version": 0})["borrowed"] = r["borrowed"]
        for r in db.execute("SELECT item_id,campus_id,SUM(consumed_delta) consumed FROM asset_events "
                            "WHERE item_id IS NOT NULL AND campus_id IS NOT NULL GROUP BY item_id,campus_id"):
            balances.setdefault((r["item_id"], r["campus_id"]), {"available": 0, "version": 0})["consumed"] = r["consumed"]
        for r in db.execute("SELECT item_id,campus_id,COUNT(*) in_register,"
                            "SUM(keeper_id IS NULL) available,SUM(keeper_id IS NOT NULL) borrowed,"
                            "SUM(keeper_id IS NULL AND health='normal' AND object_id IS NULL) lendable,"
                            "SUM(health<>'normal') unavailable FROM asset_units WHERE lifecycle='active' GROUP BY item_id,campus_id"):
            balances[(r['item_id'],r['campus_id'])]=dict(r)
        for stock in balances.values():
            if stock.get("tracking") == "status":
                stock.update(available=0, borrowed=0, consumed=0)
        return balances
    finally:
        if own_snapshot:
            db.rollback()


@bp.get("/api/assets/items")
@login_required
def items():
    db = get_db()
    balances = totals(db)
    campus = request.args.get("campus_id", "")
    query, category = request.args.get("q", "").casefold(), request.args.get("category", "")
    grouped = {}
    for (identity, location), stock in balances.items():
        if not campus or str(location) == campus:
            summary = grouped.setdefault(identity, {"available": 0, "borrowed": 0, "consumed": 0, "lendable":0,"unavailable":0})
            for key in summary:
                summary[key] += stock.get(key, 0)
    values = []
    campuses = [dict(r) for r in db.execute("SELECT * FROM asset_campuses WHERE active=1 ORDER BY name,id")]
    for r in db.execute("SELECT * FROM asset_items ORDER BY name,specification,id"):
        item = dict(r)
        if query and query not in (item["name"] + " " + item["specification"]).casefold():
            continue
        if category and category != item["category"]:
            continue
        if request.args.get("uncategorized") == "1" and item["category"]:
            continue
        if campus and item["id"] not in grouped and not (item["tracking"] == "status" and any(str(c["id"]) == campus for c in campuses)):
            continue
        for k in ("available", "borrowed", "consumed", "lendable", "unavailable"):
            item[k] = grouped.get(item["id"], {}).get(k, 0)
        item["total"] = item["available"] + item["borrowed"]
        if item["tracking"] == "status":
            item["supplies"] = [dict(id=c["id"], name=c["name"], active=c["active"],
                                     supply_status=balances.get((item["id"], c["id"]), {}).get("supply_status", "low"),
                                     stock_version=balances.get((item["id"], c["id"]), {}).get("version", 0))
                                for c in campuses if not campus or str(c["id"]) == campus]
        values.append(item)
    # Classify after campus/search/category filtering but before page slicing:
    # counts and pagination must never be computed from only one page of cards.
    def needs_attention(item):
        if not item["active"]:
            return False
        if item["tracking"] == "status":
            return any(c["supply_status"] == "low" for c in item["supplies"])
        return item["kind"] == "consumable" and item["available"] == 0

    summary = {"all": len(values), "equipment": 0, "quantity": 0, "supply": 0, "attention": 0}
    for item in values:
        group = ("equipment" if item["kind"] == "returnable" else
                 "supply" if item["tracking"] == "status" else "quantity")
        summary[group] += 1
        if needs_attention(item):
            summary["attention"] += 1
    view = request.args.get("view", "all")
    if view == "equipment":
        values = [item for item in values if item["kind"] == "returnable"]
    elif view == "quantity":
        values = [item for item in values if item["kind"] == "consumable" and item["tracking"] != "status"]
    elif view == "supply":
        values = [item for item in values if item["kind"] == "consumable" and item["tracking"] == "status"]
    elif view == "attention":
        values = [item for item in values if needs_attention(item)]
    page_number, per_page = paginated(len(values))
    return jsonify(ok=True, items=values[(page_number-1)*per_page:page_number*per_page], total=len(values), page=page_number, per_page=per_page,
                   summary=summary,
                   categories=[r[0] for r in db.execute("SELECT DISTINCT category FROM asset_items WHERE category<>'' ORDER BY category")])


@bp.get("/api/assets/items/<int:item_id>")
@login_required
def detail(item_id):
    db = get_db()
    try:
        item = row(db, "asset_items", item_id)
    except Invalid as error:
        return jsonify(ok=False, error=error.message), error.status
    balances = totals(db)
    campuses = []
    for r in db.execute("SELECT * FROM asset_campuses ORDER BY name,id"):
        campus = dict(r)
        campus.update({"available": 0, "borrowed": 0, "consumed": 0, "stock_version": 0})
        stock = balances.get((item_id, campus["id"]), {})
        campus.update({k: stock.get(k, 0) for k in ("available", "borrowed", "consumed")})
        campus["stock_version"] = stock.get("version", 0)
        if item["tracking"] == "status":
            campus["supply_status"] = stock.get("supply_status", "low")
        campuses.append(campus)
    item["supplier_codes"] = [r[0] for r in db.execute(
        "SELECT code FROM asset_identifiers WHERE item_id=? UNION "
        "SELECT code FROM asset_code_aliases WHERE namespace='lcsc-cn' AND item_id=? ORDER BY code",
        (item_id, item_id),
    )]
    return jsonify(ok=True, item=item, campuses=campuses, can_manage=manager())


@bp.get("/api/assets/loans")
@login_required
def loans():
    where, args = ["l.outstanding>0", "l.item_id IN (SELECT id FROM asset_items WHERE tracking<>'unit')"], []
    if request.args.get("mine") == "1":
        where.append("l.member_id=?")
        args.append(current_user()["id"])
    if request.args.get("item_id"):
        where.append("l.item_id=?")
        args.append(request.args["item_id"])
    clause = " AND ".join(where)
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM asset_loans l WHERE " + clause, args).fetchone()[0]
    page_number, per_page = paginated(count)
    rows = db.execute(
        "SELECT l.*,i.name,i.specification,i.unit,c.name campus_name FROM asset_loans l "
        "JOIN asset_items i ON i.id=l.item_id JOIN asset_campuses c ON c.id=l.campus_id WHERE " +
        clause + " ORDER BY l.id DESC LIMIT ? OFFSET ?", (*args, per_page, (page_number-1)*per_page),
    )
    return jsonify(ok=True, loans=[dict(r) for r in rows], total=count, page=page_number, per_page=per_page, member_id=current_user()["id"])


@bp.get("/api/assets/events")
@login_required
def events():
    db = get_db()
    where, args = [], []
    for key in ("item_id", "campus_id"):
        if request.args.get(key):
            # A transfer belongs to both campuses.
            if key == "campus_id":
                where.append("(e.campus_id=? OR EXISTS (SELECT 1 FROM json_each(e.details,'$.deltas') d "
                             "WHERE json_extract(d.value,'$[0]')=CAST(? AS INTEGER)) "
                             "OR json_extract(e.details,'$.before.campus_id')=CAST(? AS INTEGER) "
                             "OR json_extract(e.details,'$.after.campus_id')=CAST(? AS INTEGER))")
                args.extend([request.args[key]] * 4)
            else:
                where.append("e.item_id=?")
                args.append(request.args[key])
    clause = " WHERE " + " AND ".join(where) if where else ""
    count = db.execute("SELECT COUNT(*) FROM asset_events e" + clause, args).fetchone()[0]
    page_number, per_page = paginated(count)
    values = [dict(r) for r in db.execute(
        "SELECT e.*,c.name campus_name,i.name item_name,"
        "(SELECT id FROM asset_events x WHERE x.corrects_id=e.id) correction_id FROM asset_events e "
        "LEFT JOIN asset_campuses c ON c.id=e.campus_id LEFT JOIN asset_items i ON i.id=e.item_id" +
        clause + " ORDER BY e.id DESC LIMIT ? OFFSET ?", (*args, per_page, (page_number-1)*per_page))]
    for value in values:
        value["details"] = json.loads(value["details"])
    from modules.asset_scan import attach_receipts
    attach_receipts(db, values)
    return jsonify(ok=True, events=values, total=count, page=page_number, per_page=per_page)
