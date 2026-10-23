"""Physical identities, custody and append-only events in the shared database."""

import json
from collections import Counter

from flask import Blueprint, request
from common import current_user, get_db
from modules.assets import Invalid, event, integer, manager, paginated, require_manager, row, stamp, text
from modules.asset_scan import read, write

bp = Blueprint("asset_units", __name__)


def migrate(db):
    columns = {r[1] for r in db.execute("PRAGMA table_info(asset_items)")}
    if "tracking" not in columns:
        # Existing stock must never acquire guessed identities.
        db.execute("ALTER TABLE asset_items ADD COLUMN tracking TEXT NOT NULL DEFAULT 'legacy'")
    db.executescript("""
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
    """)


def active_campus(db, identity):
    if not row(db, "asset_campuses", identity)["active"]:
        raise Invalid("校区已停用", 409)


def member(db, identity):
    value = row(db, "members", identity)
    if not value["is_active"] or value["deleted_at"]:
        raise Invalid("请选择启用成员", 409)
    return value


def ensure_unfrozen(db, item_id, campus_id):
    if db.execute(
        "SELECT 1 FROM asset_stocktakes t JOIN asset_stocktake_items i ON i.take_id=t.id "
        "WHERE t.status='open' AND t.campus_id=? AND i.item_id=?",
        (campus_id, item_id),
    ).fetchone():
        raise Invalid("此校区型号正在盘点，暂不能流转", 409)


def create_units(db, item, campus_id, quantity, reason, serials=None):
    require_manager()
    if item["tracking"] != "unit" or not item["active"]:
        raise Invalid("请先完成型号的实物核对迁移", 409)
    if quantity > 100:
        raise Invalid("单次最多登记 100 件，请分批核对")
    active_campus(db, campus_id)
    ensure_unfrozen(db, item["id"], campus_id)
    serials = serials or [""] * quantity
    if (
        not isinstance(serials, list)
        or len(serials) != quantity
        or any(not isinstance(s, str) or len(s) > 120 for s in serials)
    ):
        raise Invalid("序列号数量与实收数量不一致")
    ids = [
        db.execute(
            "INSERT INTO asset_units(item_id,campus_id,serial,created_at) VALUES(?,?,?,?)",
            (item["id"], campus_id, s, stamp()),
        ).lastrowid
        for s in serials
    ]
    return {
        "unit_ids": ids,
        "event_id": event(
            db,
            "unit_receive",
            reason,
            item_id=item["id"],
            campus_id=campus_id,
            quantity=quantity,
            details={"unit_ids": ids, "serials": serials},
        ),
    }


def open_loan(db, unit_id, keeper):
    db.execute(
        "INSERT INTO asset_unit_loans(unit_id,member_id,member_name,opened_at) VALUES(?,?,?,?)",
        (unit_id, keeper["id"], keeper["name"], stamp()),
    )


def close_loan(db, unit_id, cause):
    db.execute(
        "UPDATE asset_unit_loans SET closed_at=?,closed_reason=? WHERE unit_id=? AND closed_at IS NULL",
        (stamp(), cause, unit_id),
    )


@bp.get("/api/assets/units")
@read
def units():
    db = get_db()
    where = []
    args = []
    for key in ("item_id", "campus_id", "keeper_id", "object_id"):
        if request.args.get(key):
            where.append("u." + key + "=?")
            args.append(request.args[key])
    if request.args.get("mine") == "1":
        where.append(
            "(u.keeper_id=? OR EXISTS(SELECT 1 FROM asset_handoffs h WHERE h.unit_id=u.id AND h.status='pending' AND h.receiver_id=?))"
        )
        args.extend([current_user()["id"]] * 2)
    if request.args.get("lifecycle"):
        where.append("u.lifecycle=?")
        args.append(request.args["lifecycle"])
    clause = " WHERE " + " AND ".join(where) if where else ""
    count = db.execute("SELECT COUNT(*) FROM asset_units u" + clause, args).fetchone()[0]
    page_number, per_page = paginated(count)
    values = [
        dict(r)
        for r in db.execute(
            "SELECT u.*,m.name keeper_name,c.name campus_name,i.name item_name,o.name object_name,"
            "(SELECT h.receiver_id FROM asset_handoffs h WHERE h.unit_id=u.id AND h.status='pending' LIMIT 1) receiver_id "
            "FROM asset_units u JOIN asset_items i ON i.id=u.item_id JOIN asset_campuses c ON c.id=u.campus_id "
            "LEFT JOIN members m ON m.id=u.keeper_id LEFT JOIN asset_objects o ON o.id=u.object_id"
            + clause
            + " ORDER BY u.id LIMIT ? OFFSET ?",
            (*args, per_page, (page_number - 1) * per_page),
        )
    ]
    return {
        "units": values,
        "total": count,
        "page": page_number,
        "per_page": per_page,
        "can_manage": manager(),
        "member_id": current_user()["id"],
    }


@bp.get("/api/assets/units/<int:identity>")
@read
def detail(identity):
    db = get_db()
    unit = row(db, "asset_units", identity)
    item = row(db, "asset_items", unit["item_id"])
    unit.update(item_name=item["name"], campus_name=row(db, "asset_campuses", unit["campus_id"])["name"])
    if unit["keeper_id"]:
        unit["keeper_name"] = row(db, "members", unit["keeper_id"])["name"]
    handoff = db.execute("SELECT receiver_id FROM asset_handoffs WHERE unit_id=? AND status='pending' LIMIT 1", (identity,)).fetchone()
    unit["receiver_id"] = handoff["receiver_id"] if handoff else None
    return {"unit": unit, "item": item, "can_manage": manager(), "member_id": current_user()["id"]}


@bp.get("/api/assets/units/<int:identity>/history")
@read
def history(identity):
    db = get_db()
    row(db, "asset_units", identity)
    clause = (
        "json_extract(details,'$.unit_id')=? OR EXISTS(SELECT 1 FROM json_each(details,'$.unit_ids') WHERE value=?)"
    )
    total = db.execute("SELECT COUNT(*) FROM asset_events WHERE " + clause, (identity, identity)).fetchone()[0]
    page_number, per_page = paginated(total, default=100)
    records = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM asset_events WHERE " + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (identity, identity, per_page, (page_number - 1) * per_page),
        )
    ]
    for record in records:
        record["details"] = json.loads(record["details"])
    from modules.asset_scan import attach_receipts

    attach_receipts(db, records)
    return {
        "events": records,
        "total": total,
        "page": page_number,
        "per_page": per_page,
        "loans": [
            dict(r) for r in db.execute("SELECT * FROM asset_unit_loans WHERE unit_id=? ORDER BY id DESC", (identity,))
        ],
    }


@bp.get("/api/assets/units/catalog")
@read
def catalog():
    db = get_db()
    return {
        "members": [
            dict(r)
            for r in db.execute("SELECT id,name FROM members WHERE is_active=1 AND deleted_at IS NULL ORDER BY name,id")
        ],
        "objects": [dict(r) for r in db.execute("SELECT * FROM asset_objects ORDER BY name,id")],
        "projects": [dict(r) for r in db.execute("SELECT id,name FROM projects WHERE deleted_at IS NULL ORDER BY id")],
        "takes": [dict(r) for r in db.execute("SELECT * FROM asset_stocktakes ORDER BY id DESC LIMIT 50")],
    }


@bp.post("/api/assets/units/receive")
@write("item_id", "campus_id", "quantity", "reason", "serials")
def receive(db, data):
    return create_units(
        db,
        row(db, "asset_items", integer(data, "item_id")),
        integer(data, "campus_id"),
        integer(data, "quantity"),
        text(data, "reason", maximum=500),
        data.get("serials"),
    )


@bp.post("/api/assets/units/operate")
@write(
    "action",
    "units",
    "reason",
    "member_id",
    "campus_id",
    "object_id",
    "position",
    "location",
    "detached",
    "health",
    "destroyed",
)
def operate(db, data):
    return operate_units(db, data)


def operate_units(db, data):
    action = text(data, "action")
    reason = text(data, "reason", maximum=500)
    allowed = {
        "borrow",
        "return",
        "handoff",
        "accept",
        "cancel_handoff",
        "move",
        "install",
        "detach",
        "fault",
        "repair",
        "healthy",
        "retire",
        "lost",
        "recover",
        "correct",
    }
    if action not in allowed:
        raise Invalid("不支持的单件操作")
    selections = data.get("units")
    if not isinstance(selections, list) or not 1 <= len(selections) <= 100:
        raise Invalid("请选择 1 至 100 件设备")
    ids = []
    snapshots = []
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"id", "version"}:
            raise Invalid("单件选择缺少版本")
        identity = integer(selection, "id")
        unit = row(db, "asset_units", identity)
        if identity in ids or unit["version"] != integer(selection, "version"):
            raise Invalid("重复选择或设备已发生变化，请重新核对", 409)
        item = row(db, "asset_items", unit["item_id"])
        if item["tracking"] != "unit" or not item["active"]:
            raise Invalid("型号未启用单件管理", 409)
        active_campus(db, unit["campus_id"])
        ensure_unfrozen(db, unit["item_id"], unit["campus_id"])
        ids.append(identity)
        snapshots.append(unit)
    user = current_user()
    admin = manager()
    events = []
    for before in snapshots:
        unit = dict(before)
        identity = unit["id"]
        if action == "recover":
            require_manager()
            if unit["lifecycle"] not in ("lost", "retired"):
                raise Invalid("仅未销毁的遗失或误报废设备可恢复", 409)
            unit.update(lifecycle="active", health="inspection", keeper_id=None, object_id=None, position="")
        else:
            if unit["lifecycle"] != "active":
                raise Invalid("设备已退出在册", 409)
            if action not in ("borrow", "accept") and not admin and unit["keeper_id"] != user["id"]:
                raise Invalid("只能维护本人保管的设备", 403)
            if action == "borrow":
                if unit["keeper_id"] or unit["object_id"] or unit["health"] != "normal":
                    raise Invalid("设备不在可借状态", 409)
                keeper = member(db, integer(data, "member_id") if data.get("member_id") else user["id"])
                if keeper["id"] != user["id"] and not admin:
                    raise Invalid("不能为他人登记借用", 403)
                unit["keeper_id"] = keeper["id"]
                open_loan(db, identity, keeper)
            elif action in ("return", "retire", "lost"):
                if action in ("retire", "lost"):
                    require_manager()
                if action == "return" and not unit["keeper_id"]:
                    raise Invalid("设备没有待还借用", 409)
                if unit["object_id"] and data.get("detached") is not True:
                    raise Invalid("请明确确认设备已经拆卸", 409)
                unit.update(object_id=None, position="", keeper_id=None)
                close_loan(db, identity, action)
                if action == "return":
                    unit["health"] = (
                        "inspection"
                        if data.get("health") in ("fault", "inspection") or unit["health"] != "normal"
                        else "normal"
                    )
                    unit["campus_id"] = integer(data, "campus_id")
                else:
                    unit["lifecycle"] = (
                        "destroyed"
                        if action == "retire" and data.get("destroyed") is True
                        else ("retired" if action == "retire" else "lost")
                    )
            elif action == "handoff":
                if not unit["keeper_id"]:
                    raise Invalid("设备尚未借出", 409)
                receiver = member(db, integer(data, "member_id"))
                if receiver["id"] == unit["keeper_id"]:
                    raise Invalid("接收人与当前保管人相同")
                db.execute(
                    "UPDATE asset_handoffs SET status='cancelled' WHERE unit_id=? AND status='pending'", (identity,)
                )
                db.execute(
                    "INSERT INTO asset_handoffs(unit_id,sender_id,receiver_id,unit_version,created_at) VALUES(?,?,?,?,?)",
                    (identity, unit["keeper_id"], receiver["id"], unit["version"] + 1, stamp()),
                )
            elif action == "accept":
                handoff = db.execute(
                    "SELECT * FROM asset_handoffs WHERE unit_id=? AND status='pending'", (identity,)
                ).fetchone()
                if not handoff or handoff["receiver_id"] != user["id"] or handoff["unit_version"] != unit["version"]:
                    raise Invalid("移交已失效或当前成员不是接收人", 409)
                close_loan(db, identity, "handoff")
                open_loan(db, identity, member(db, user["id"]))
                unit["keeper_id"] = user["id"]
                db.execute("UPDATE asset_handoffs SET status='accepted' WHERE id=?", (handoff["id"],))
            elif action == "move":
                if unit["object_id"] and (not admin or data.get("object_id") != unit["object_id"]):
                    raise Invalid("已装车设备请由管理人员登记整车移动或先拆卸", 409)
                if unit["object_id"]:
                    mounted = {
                        u[0]
                        for u in db.execute(
                            "SELECT id FROM asset_units WHERE object_id=? AND lifecycle='active'", (unit["object_id"],)
                        )
                    }
                    if mounted != set(ids):
                        raise Invalid("移动整车必须包含全部已安装设备", 409)
                unit["campus_id"] = integer(data, "campus_id")
            elif action == "install":
                if not unit["keeper_id"] or unit["object_id"] or unit["health"] != "normal":
                    raise Invalid("请先借用正常设备，或先拆卸原安装关系", 409)
                obj = row(db, "asset_objects", integer(data, "object_id"))
                if not obj["active"]:
                    raise Invalid("使用对象已停用", 409)
                unit.update(object_id=obj["id"], position=text(data, "position", False))
            elif action == "detach":
                if not unit["object_id"]:
                    raise Invalid("设备没有安装关系", 409)
                unit.update(object_id=None, position="", location=text(data, "location"))
            elif action == "fault":
                unit["health"] = "inspection"
            elif action in ("repair", "healthy"):
                require_manager()
                if action == "repair" and unit["object_id"]:
                    if data.get("detached") is not True:
                        raise Invalid("送修前请明确确认已拆卸", 409)
                    unit.update(object_id=None, position="")
                unit["health"] = "repair" if action == "repair" else "normal"
            elif action == "correct":
                require_manager()
                keeper = member(db, integer(data, "member_id")) if data.get("member_id") else None
                if unit["object_id"] and keeper is None:
                    raise Invalid("已装车设备需保留责任人或先拆卸", 409)
                close_loan(db, identity, "correction")
                if keeper:
                    open_loan(db, identity, keeper)
                unit["keeper_id"] = keeper["id"] if keeper else None
        if "location" in data:
            unit["location"] = text(data, "location", False)
        active_campus(db, unit["campus_id"])
        ensure_unfrozen(db, unit["item_id"], unit["campus_id"])
        if action not in ("handoff", "accept"):
            db.execute("UPDATE asset_handoffs SET status='cancelled' WHERE unit_id=? AND status='pending'", (identity,))
        fields = ("campus_id", "location", "keeper_id", "object_id", "position", "health", "lifecycle")
        db.execute(
            "UPDATE asset_units SET " + ",".join(k + "=?" for k in fields) + ",version=version+1 WHERE id=?",
            (*(unit[k] for k in fields), identity),
        )
        unit["version"] += 1
        keeper = row(db, "members", unit["keeper_id"]) if unit["keeper_id"] else None
        events.append(
            event(
                db,
                "unit_" + action,
                reason,
                item_id=unit["item_id"],
                campus_id=unit["campus_id"],
                quantity=1,
                member_id=unit["keeper_id"],
                member_name=keeper["name"] if keeper else "",
                details={"unit_id": identity, "before": before, "after": unit},
            )
        )
    return {"event_ids": events, "unit_ids": ids}


@bp.post("/api/assets/units/objects")
@write("id", "version", "name", "project_id", "active", "reason")
def save_object(db, data):
    require_manager()
    name = text(data, "name")
    reason = text(data, "reason", maximum=500)
    project = integer(data, "project_id") if data.get("project_id") else None
    if project and row(db, "projects", project)["deleted_at"]:
        raise Invalid("造车组已删除", 409)
    active = data.get("active", True)
    if type(active) is not bool:
        raise Invalid("启用状态无效")
    before = None
    if data.get("id"):
        before = row(db, "asset_objects", integer(data, "id"))
        if before["version"] != integer(data, "version"):
            raise Invalid("使用对象已更新", 409)
        if not active and db.execute("SELECT 1 FROM asset_units WHERE object_id=?", (before["id"],)).fetchone():
            raise Invalid("仍有已安装设备，不能停用", 409)
        identity = before["id"]
        db.execute(
            "UPDATE asset_objects SET name=?,project_id=?,active=?,version=version+1 WHERE id=?",
            (name, project, int(active), identity),
        )
    else:
        identity = db.execute(
            "INSERT INTO asset_objects(name,project_id,active) VALUES(?,?,?)", (name, project, int(active))
        ).lastrowid
    return {
        "id": identity,
        "event_id": event(
            db,
            "object_edit",
            reason,
            details={"object_id": identity, "before": before, "after": row(db, "asset_objects", identity)},
        ),
    }


@bp.post("/api/assets/units/object-move")
@write("object_id", "version", "units", "campus_id", "location", "reason")
def move_object(db, data):
    require_manager()
    obj = row(db, "asset_objects", integer(data, "object_id"))
    if obj["version"] != integer(data, "version"):
        raise Invalid("使用对象已变化，请重新核对", 409)
    expected = {
        u["id"]: u["version"]
        for u in db.execute("SELECT id,version FROM asset_units WHERE object_id=? AND lifecycle='active'", (obj["id"],))
    }
    selected = data.get("units")
    if not isinstance(selected, list) or any(not isinstance(s, dict) for s in selected):
        raise Invalid("请核对整车单件列表")
    if expected != {integer(s, "id"): integer(s, "version") for s in selected}:
        raise Invalid("整车设备清单或状态已变化，请重新核对", 409)
    result = operate_units(db, dict(data, action="move"))
    db.execute("UPDATE asset_objects SET version=version+1 WHERE id=?", (obj["id"],))
    return result


@bp.post("/api/assets/units/stocktakes")
@write("campus_id", "item_ids", "scope", "reason")
def start_take(db, data):
    require_manager()
    campus = integer(data, "campus_id")
    active_campus(db, campus)
    reason = text(data, "reason", maximum=500)
    scope = text(data, "scope")
    if scope not in ("warehouse", "all"):
        raise Invalid("盘点范围无效")
    items = data.get("item_ids")
    if (
        not isinstance(items, list)
        or not 1 <= len(items) <= 20
        or any(type(i) is not int for i in items)
        or len(set(items)) != len(items)
    ):
        raise Invalid("请选择 1 至 20 个不同型号")
    for identity in items:
        integer({"id": identity}, "id")
        item = row(db, "asset_items", identity)
        if item["tracking"] != "unit" or not item["active"]:
            raise Invalid("只能盘点已启用的单件型号", 409)
        ensure_unfrozen(db, identity, campus)
    marks = ",".join("?" for _ in items)
    units = [
        dict(u)
        for u in db.execute(
            f"SELECT * FROM asset_units WHERE item_id IN ({marks}) AND campus_id=? AND lifecycle='active'"
            + (" AND keeper_id IS NULL" if scope == "warehouse" else ""),
            (*items, campus),
        )
    ]
    if len(units) > 1000:
        raise Invalid("盘点范围超过 1000 件，请缩小型号范围")
    identity = db.execute(
        "INSERT INTO asset_stocktakes(campus_id,scope,created_at) VALUES(?,?,?)", (campus, scope, stamp())
    ).lastrowid
    db.executemany("INSERT INTO asset_stocktake_items VALUES(?,?)", [(identity, i) for i in items])
    db.executemany(
        "INSERT INTO asset_stocktake_units(take_id,unit_id,snapshot) VALUES(?,?,?)",
        [(identity, u["id"], json.dumps(u, ensure_ascii=False)) for u in units],
    )
    return {
        "id": identity,
        "event_id": event(
            db,
            "stocktake_start",
            reason,
            campus_id=campus,
            details={"take_id": identity, "scope": scope, "item_ids": items, "unit_ids": [u["id"] for u in units]},
        ),
    }


@bp.get("/api/assets/units/stocktakes/<int:identity>")
@read
def take_detail(identity):
    require_manager()
    db = get_db()
    take = row(db, "asset_stocktakes", identity)
    units = [
        dict(r) for r in db.execute("SELECT * FROM asset_stocktake_units WHERE take_id=? ORDER BY unit_id", (identity,))
    ]
    for u in units:
        u["snapshot"] = json.loads(u["snapshot"])
    # Event id is the revision token, covering observations and closure.
    revision = db.execute(
        "SELECT COALESCE(MAX(id),0) FROM asset_events WHERE json_extract(details,'$.take_id')=?", (identity,)
    ).fetchone()[0]
    extras = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM asset_events WHERE action='stocktake_unexpected' AND json_extract(details,'$.take_id')=? ORDER BY id",
            (identity,),
        )
    ]
    return {"take": take, "units": units, "revision": revision, "unexpected": extras}


@bp.post("/api/assets/units/stocktakes/record")
@write("take_id", "unit_id", "action", "revision", "reason")
def record_take(db, data):
    return record_observation(db, data)


@bp.post("/api/assets/units/stocktakes/scan-batch")
@write("take_id", "unit_ids", "reason")
def record_many(db, data):
    values = data.get("unit_ids")
    if (
        not isinstance(values, list)
        or not 1 <= len(values) <= 100
        or any(type(i) is not int for i in values)
        or len(set(values)) != len(values)
    ):
        raise Invalid("请选择 1 至 100 个不同单件")
    return {"events": [record_observation(db, dict(data, unit_id=i, action="scan")) for i in values]}


def record_observation(db, data):
    require_manager()
    take = row(db, "asset_stocktakes", integer(data, "take_id"))
    if take["status"] != "open":
        raise Invalid("盘点已经结束", 409)
    action = text(data, "action")
    reason = text(data, "reason", maximum=500)
    details = {"take_id": take["id"]}
    if action == "scan":
        unit = row(db, "asset_units", integer(data, "unit_id"))
        details["unit_id"] = unit["id"]
        expected = db.execute(
            "SELECT * FROM asset_stocktake_units WHERE take_id=? AND unit_id=?", (take["id"], unit["id"])
        ).fetchone()
        if expected:
            if expected["seen_at"]:
                raise Invalid("此单件已扫描核对", 409)
            db.execute(
                "UPDATE asset_stocktake_units SET seen_at=?,seen_by=? WHERE take_id=? AND unit_id=?",
                (stamp(), current_user()["id"], take["id"], unit["id"]),
            )
        else:
            action = "unexpected"
            details["observed"] = unit
    elif action in ("close", "cancel"):
        revision = db.execute(
            "SELECT COALESCE(MAX(id),0) FROM asset_events WHERE json_extract(details,'$.take_id')=?", (take["id"],)
        ).fetchone()[0]
        if revision != integer(data, "revision"):
            raise Invalid("盘点记录已有新扫描，请重新核对差异", 409)
        missing = [
            r[0]
            for r in db.execute(
                "SELECT unit_id FROM asset_stocktake_units WHERE take_id=? AND seen_at IS NULL", (take["id"],)
            )
        ]
        details["unverified_unit_ids"] = missing
        db.execute(
            "UPDATE asset_stocktakes SET status=?,closed_at=? WHERE id=?",
            ("closed" if action == "close" else "cancelled", stamp(), take["id"]),
        )
        # Absence never changes identity, custody or inventory. Correct after physical review.
    else:
        raise Invalid("盘点操作无效")
    return {"event_id": event(db, "stocktake_" + action, reason, campus_id=take["campus_id"], details=details)}


@bp.post("/api/assets/units/migrate")
@write("item_id", "action", "reason", "campus_id", "loan_id", "quantity", "serials", "version")
def migrate_item(db, data):
    require_manager()
    item = row(db, "asset_items", integer(data, "item_id"))
    reason = text(data, "reason", maximum=500)
    action = text(data, "action")
    if item["kind"] != "returnable" or item["tracking"] not in ("legacy", "quantity", "migrating"):
        raise Invalid("此型号不需要迁移", 409)
    if item["version"] != integer(data, "version"):
        raise Invalid("核对内容已变化，请刷新后重试", 409)
    details = dict(data)
    if action == "start":
        if item["tracking"] not in ("legacy", "quantity"):
            raise Invalid("型号状态已变化", 409)
        if db.execute("SELECT 1 FROM asset_units WHERE item_id=?", (item["id"],)).fetchone():
            raise Invalid("此型号已有单件身份，请先核对管理方式", 409)
        details["tracking_before"] = item["tracking"]
        db.execute("UPDATE asset_items SET tracking='migrating',version=version+1 WHERE id=?", (item["id"],))
    elif item["tracking"] != "migrating":
        raise Invalid("请先冻结此型号并开始核对", 409)
    elif action == "cancel":
        started = db.execute("SELECT details FROM asset_events WHERE item_id=? AND action='migration_start' ORDER BY id DESC LIMIT 1",
                             (item["id"],)).fetchone()
        original_mode = json.loads(started["details"]).get("tracking_before", "legacy") if started else "legacy"
        if original_mode not in ("legacy", "quantity"):
            original_mode = "legacy"
        db.execute("DELETE FROM asset_migration_entries WHERE item_id=?", (item["id"],))
        db.execute("UPDATE asset_items SET tracking=?,version=version+1 WHERE id=?", (original_mode, item["id"]))
    elif action == "add":
        campus = integer(data, "campus_id")
        active_campus(db, campus)
        quantity = integer(data, "quantity")
        loan_id = data.get("loan_id") or None
        if quantity > 100:
            raise Invalid("单次核对最多 100 件")
        if loan_id:
            loan = row(db, "asset_loans", integer(data, "loan_id"))
            if loan["item_id"] != item["id"] or loan["campus_id"] != campus:
                raise Invalid("借单与型号校区不匹配")
        serials = data.get("serials") or [""] * quantity
        if (
            not isinstance(serials, list)
            or len(serials) != quantity
            or any(not isinstance(s, str) or len(s) > 120 for s in serials)
        ):
            raise Invalid("序列号内容无效")
        db.executemany(
            "INSERT INTO asset_migration_entries(item_id,campus_id,loan_id,serial) VALUES(?,?,?,?)",
            [(item["id"], campus, loan_id, s) for s in serials],
        )
        db.execute("UPDATE asset_items SET version=version+1 WHERE id=?", (item["id"],))
    elif action == "finish":
        entries = [dict(r) for r in db.execute("SELECT * FROM asset_migration_entries WHERE item_id=?", (item["id"],))]
        stock = Counter(
            {
                (r["campus_id"], None): r["available"]
                for r in db.execute("SELECT * FROM asset_stock WHERE item_id=?", (item["id"],))
                if r["available"]
            }
        )
        loans = {
            r["id"]: dict(r)
            for r in db.execute("SELECT * FROM asset_loans WHERE item_id=? AND outstanding>0", (item["id"],))
        }
        stock.update({(r["campus_id"], r["id"]): r["outstanding"] for r in loans.values()})
        if Counter((r["campus_id"], r["loan_id"]) for r in entries) != stock:
            raise Invalid("核对数量与各校区库存、每笔未还借单不一致", 409)
        unit_ids = []
        for entry in entries:
            # Historical custody is not a new borrow. Preserve the old loan's
            # member ID and name even when that account is disabled or soft-deleted.
            old_loan = loans[entry["loan_id"]] if entry["loan_id"] else None
            keeper = {"id": old_loan["member_id"], "name": old_loan["member_name"]} if old_loan else None
            identity = db.execute(
                "INSERT INTO asset_units(item_id,campus_id,serial,keeper_id,created_at) VALUES(?,?,?,?,?)",
                (item["id"], entry["campus_id"], entry["serial"], keeper["id"] if keeper else None, stamp()),
            ).lastrowid
            if keeper:
                open_loan(db, identity, keeper)
            unit_ids.append(identity)
        db.execute("UPDATE asset_items SET tracking='unit',version=version+1 WHERE id=?", (item["id"],))
        # Original balances, outstanding loans and all old events remain a historical snapshot.
        return {
            "unit_ids": unit_ids,
            "event_id": event(
                db,
                "unit_migration",
                reason,
                item_id=item["id"],
                quantity=len(unit_ids),
                details={"unit_ids": unit_ids, "mapping": entries},
            ),
        }
    else:
        raise Invalid("迁移操作无效")
    return {"event_id": event(db, "migration_" + action, reason, item_id=item["id"], details=details)}


@bp.get("/api/assets/units/migration/<int:item_id>")
@read
def migration_status(item_id):
    require_manager()
    db = get_db()
    return {
        "item": row(db, "asset_items", item_id),
        "stock": [dict(r) for r in db.execute("SELECT * FROM asset_stock WHERE item_id=?", (item_id,))],
        "loans": [
            dict(r) for r in db.execute("SELECT * FROM asset_loans WHERE item_id=? AND outstanding>0", (item_id,))
        ],
        "entries": [dict(r) for r in db.execute("SELECT * FROM asset_migration_entries WHERE item_id=?", (item_id,))],
    }
