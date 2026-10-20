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
        CREATE UNIQUE INDEX IF NOT EXISTS asset_item_identity ON asset_items(name,specification);
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
    db.commit()


class Invalid(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def manager(user=None):
    user = user or current_user()
    return bool(user and (user["role"] == "admin" or has_permission("assets.manage", user)))


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
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                raise Invalid("请求必须是 JSON 对象")
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
    return jsonify(ok=True, can_manage=manager(),
                   campuses=[dict(r) for r in get_db().execute("SELECT * FROM asset_campuses ORDER BY name,id")])


@bp.post("/api/assets/catalog/<kind>")
@mutation
def save_catalog(db, data, kind):
    require_manager()
    if kind not in ("items", "campuses"):
        raise Invalid("目录类型不存在", 404)
    table = "asset_" + kind
    fields = {"name": text(data, "name")}
    if kind == "items":
        fields.update(specification=text(data, "specification", False), category=text(data, "category", False),
                      unit=text(data, "unit", maximum=12), kind=text(data, "kind"))
        if fields["kind"] not in ("returnable", "consumable"):
            raise Invalid("物品类型无效")
    identity = data.get("id")
    before = None
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
        if not active and (
            db.execute(f"SELECT 1 FROM asset_stock WHERE {column}=? AND available>0", (identity,)).fetchone()
            or db.execute(f"SELECT 1 FROM asset_loans WHERE {column}=? AND outstanding>0", (identity,)).fetchone()
        ):
            raise Invalid("仍有库存或未还借单，不能停用", 409)
        if kind == "items" and any(before[k] != fields[k] for k in ("unit", "kind")):
            if db.execute("SELECT 1 FROM asset_events WHERE item_id=?", (identity,)).fetchone():
                raise Invalid("已有历史记录，不能改变单位或借还类型", 409)
        db.execute(f"UPDATE {table} SET " + ",".join(f"{k}=?" for k in fields) +
                   ",version=version+1 WHERE id=?", (*fields.values(), identity))
    else:
        identity = db.execute(f"INSERT INTO {table}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                              tuple(fields.values())).lastrowid
    event(db, "catalog", "目录维护", item_id=identity if kind == "items" else None,
          campus_id=identity if kind == "campuses" else None, details={"before": before, "after": fields})
    return {"id": identity}


def stock_change(db, item_id, campus_id, delta):
    db.execute("INSERT OR IGNORE INTO asset_stock(item_id,campus_id) VALUES(?,?)", (item_id, campus_id))
    current = db.execute("SELECT available FROM asset_stock WHERE item_id=? AND campus_id=?",
                         (item_id, campus_id)).fetchone()[0]
    if not 0 <= current + delta <= MAX_QUANTITY:
        raise Invalid("在库数量不足或结果超出数量上限，请刷新核对", 409)
    db.execute("UPDATE asset_stock SET available=available+?,version=version+1 WHERE item_id=? AND campus_id=?",
               (delta, item_id, campus_id))


@bp.post("/api/assets/operations")
@mutation
def operate(db, data):
    action = text(data, "action")
    reason = text(data, "reason", action in ("receive", "adjust", "transfer", "correct"), 500)
    user = current_user()
    if action == "correct":
        require_manager()
        original = row(db, "asset_events", integer(data, "event_id"))
        if original["action"] not in ("receive", "adjust", "transfer", "consume", "borrow", "return"):
            raise Invalid("此记录不能撤销；请登记新的业务操作")
        if db.execute("SELECT 1 FROM asset_events WHERE corrects_id=?", (original["id"],)).fetchone():
            raise Invalid("该记录已经纠正", 409)
        details = json.loads(original["details"])
        item_id, campus_id = original["item_id"], original["campus_id"]
        deltas = [(c, -d) for c, d in details["deltas"]]
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
    for campus, delta in deltas:
        stock_change(db, item_id, campus, delta)
    return {"event_id": event(db, action, reason, item_id=item_id, campus_id=campus_id, quantity=quantity,
                              member_id=member_id, member_name=member_name,
                              consumed_delta=quantity if action == "consume" else 0,
                              details={"deltas": deltas, "loan_id": loan_id, "loan_delta": loan_delta}),
            "loan_id": loan_id}


def paging():
    try:
        return max(1, min(int(request.args.get("page", 1)), 1_000_000))
    except ValueError:
        return 1


def totals(db):
    # Three aggregate queries, independent of the number of items/campuses.
    balances = {(r["item_id"], r["campus_id"]): dict(r) for r in db.execute("SELECT * FROM asset_stock")}
    for r in db.execute("SELECT item_id,campus_id,SUM(outstanding) borrowed FROM asset_loans GROUP BY item_id,campus_id"):
        balances.setdefault((r["item_id"], r["campus_id"]), {"available": 0, "version": 0})["borrowed"] = r["borrowed"]
    for r in db.execute("SELECT item_id,campus_id,SUM(consumed_delta) consumed FROM asset_events "
                        "WHERE item_id IS NOT NULL AND campus_id IS NOT NULL GROUP BY item_id,campus_id"):
        balances.setdefault((r["item_id"], r["campus_id"]), {"available": 0, "version": 0})["consumed"] = r["consumed"]
    return balances


@bp.get("/api/assets/items")
@login_required
def items():
    db = get_db()
    balances = totals(db)
    campus = request.args.get("campus_id", "")
    query, category = request.args.get("q", "").casefold(), request.args.get("category", "")
    values = []
    for r in db.execute("SELECT * FROM asset_items ORDER BY name,specification,id"):
        item = dict(r)
        if query and query not in (item["name"] + " " + item["specification"]).casefold():
            continue
        if category and category != item["category"]:
            continue
        stocks = [s for (i, c), s in balances.items() if i == item["id"] and (not campus or str(c) == campus)]
        if campus and not stocks:
            continue
        for k in ("available", "borrowed", "consumed"):
            item[k] = sum(s.get(k, 0) for s in stocks)
        item["total"] = item["available"] + item["borrowed"]
        values.append(item)
    page_number = paging()
    return jsonify(ok=True, items=values[(page_number-1)*20:page_number*20], total=len(values), page=page_number,
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
        campuses.append(campus)
    return jsonify(ok=True, item=item, campuses=campuses, can_manage=manager())


@bp.get("/api/assets/loans")
@login_required
def loans():
    where, args = ["l.outstanding>0"], []
    if request.args.get("mine") == "1":
        where.append("l.member_id=?")
        args.append(current_user()["id"])
    if request.args.get("item_id"):
        where.append("l.item_id=?")
        args.append(request.args["item_id"])
    clause = " AND ".join(where)
    db, page_number = get_db(), paging()
    count = db.execute("SELECT COUNT(*) FROM asset_loans l WHERE " + clause, args).fetchone()[0]
    rows = db.execute(
        "SELECT l.*,i.name,i.specification,i.unit,c.name campus_name FROM asset_loans l "
        "JOIN asset_items i ON i.id=l.item_id JOIN asset_campuses c ON c.id=l.campus_id WHERE " +
        clause + " ORDER BY l.id DESC LIMIT 20 OFFSET ?", (*args, (page_number-1)*20),
    )
    return jsonify(ok=True, loans=[dict(r) for r in rows], total=count, page=page_number, member_id=current_user()["id"])


@bp.get("/api/assets/events")
@login_required
def events():
    db, page_number = get_db(), paging()
    where, args = [], []
    for key in ("item_id", "campus_id"):
        if request.args.get(key):
            # A transfer belongs to both campuses.
            if key == "campus_id":
                where.append("(e.campus_id=? OR EXISTS (SELECT 1 FROM json_each(e.details,'$.deltas') d "
                             "WHERE json_extract(d.value,'$[0]')=CAST(? AS INTEGER)))")
                args.extend([request.args[key], request.args[key]])
            else:
                where.append("e.item_id=?")
                args.append(request.args[key])
    clause = " WHERE " + " AND ".join(where) if where else ""
    count = db.execute("SELECT COUNT(*) FROM asset_events e" + clause, args).fetchone()[0]
    values = [dict(r) for r in db.execute(
        "SELECT e.*,c.name campus_name,i.name item_name,"
        "(SELECT id FROM asset_events x WHERE x.corrects_id=e.id) correction_id FROM asset_events e "
        "LEFT JOIN asset_campuses c ON c.id=e.campus_id LEFT JOIN asset_items i ON i.id=e.item_id" +
        clause + " ORDER BY e.id DESC LIMIT 20 OFFSET ?", (*args, (page_number-1)*20))]
    for value in values:
        value["details"] = json.loads(value["details"])
    return jsonify(ok=True, events=values, total=count, page=page_number)
