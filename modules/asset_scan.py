"""Scan untrusted labels, then commit catalog, identities and receipts together."""

import hashlib
import json
import re
from functools import wraps

from flask import Blueprint, jsonify, request
from common import current_user, get_db, login_required
from modules.assets import Invalid, event, integer, manager, mutation, require_manager, row, stamp, text
from modules.asset_supplier import lookup_lcsc

bp = Blueprint("asset_scan", __name__)


def migrate(db):
    db.executescript("""
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
        CREATE TABLE IF NOT EXISTS asset_code_aliases (
            id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, code TEXT NOT NULL,
            raw TEXT NOT NULL, item_id INTEGER REFERENCES asset_items(id),
            unit_id INTEGER REFERENCES asset_units(id),
            actor_id INTEGER NOT NULL REFERENCES members(id), created_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            CHECK ((item_id IS NOT NULL AND unit_id IS NULL) OR
                   (item_id IS NULL AND unit_id IS NOT NULL))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS asset_code_item ON asset_code_aliases(namespace,code,item_id)
            WHERE item_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS asset_code_unit ON asset_code_aliases(namespace,code,unit_id)
            WHERE unit_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS asset_code_lookup ON asset_code_aliases(namespace,code);
    """)


def write(*fields):
    def decorate(view):
        view.asset_fields = set(fields) | {"request_id"}
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
        raise Invalid("标签内容为空或超过 4096 字符")
    raw = raw.strip()
    if re.fullmatch(r"[UM]:[1-9][0-9]{0,17}", raw):
        return {"type": "unit" if raw[0] == "U" else "item", "id": int(raw[2:]), "raw": raw}
    if re.fullmatch(r"C[1-9][0-9]{0,17}", raw, re.I):
        return {"type": "supplier", "supplier": "lcsc-cn", "code": raw.upper(), "raw": raw}
    external = {"type": "external", "raw": raw, "code": raw}
    if not raw.startswith("{"):
        return external
    if not raw.endswith("}"):
        return {**external, "parse_error": "包装标签格式无效，可按原码补充资料"}
    fields = {}
    for pair in raw[1:-1].split(","):
        key, separator, value = pair.partition(":")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key) or key in fields:
            return {**external, "parse_error": "标签字段重复或格式无效，可按原码补充资料"}
        fields[key] = value
    if not re.fullmatch(r"C[1-9][0-9]{0,17}", fields.get("pc", ""), re.I):
        return {**external, "parse_error": "包装标签缺少有效商品编号 pc，可按原码补充资料"}
    quantity = fields.get("qty", "")
    valid = bool(re.fullmatch(r"[1-9][0-9]{0,8}", quantity))
    return {
        "type": "package", "supplier": "lcsc-cn", "code": fields["pc"].upper(),
        "model": fields.get("pm", ""), "order": fields.get("on", ""),
        "label_quantity": int(quantity) if valid else None,
        "quantity_error": None if valid else "标签数量无效，请明确填写实收数量",
        "fields": fields, "raw": raw,
    }


def fingerprint(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def receipt_history(db, raw):
    return [dict(r) for r in db.execute(
        "SELECT r.id,r.item_id,r.actual_quantity,r.label_quantity,r.duplicate_reason,"
        "e.created_at,e.actor_name,e.reason,e.campus_id FROM asset_receipts r "
        "JOIN asset_events e ON e.id=r.event_id WHERE r.fingerprint=? ORDER BY r.id DESC LIMIT 20",
        (fingerprint(raw),),
    )]


def attach_receipts(db, events):
    """One batched query exposes source evidence without rewriting immutable events."""
    if not events:
        return
    marks = ",".join("?" for _ in events)
    receipts = {}
    for r in db.execute(f"SELECT * FROM asset_receipts WHERE event_id IN ({marks})", [e["id"] for e in events]):
        value = dict(r)
        value["parsed"] = json.loads(value["parsed"])
        receipts[value["event_id"]] = value
    for e in events:
        e["receipt"] = receipts.get(e["id"])


def exact_matches(db, parsed):
    """Internal IDs, old supplier bindings and external claims are all candidates."""
    targets = set()
    if parsed["type"] in ("item", "unit"):
        table = "asset_items" if parsed["type"] == "item" else "asset_units"
        if db.execute(f"SELECT 1 FROM {table} WHERE id=?", (parsed["id"],)).fetchone():
            targets.add((parsed["type"], parsed["id"]))
    clauses, args = ["(namespace='raw' AND code=?)"], [parsed["raw"]]
    if parsed["type"] in ("supplier", "package"):
        for identifier in db.execute(
            "SELECT item_id FROM asset_identifiers WHERE supplier=? AND code=?",
            (parsed["supplier"], parsed["code"]),
        ):
            targets.add(("item", identifier[0]))
        clauses.append("(namespace='lcsc-cn' AND code=?)")
        args.append(parsed["code"])
    for alias in db.execute("SELECT item_id,unit_id FROM asset_code_aliases WHERE " + " OR ".join(clauses), args):
        targets.add(("unit", alias["unit_id"]) if alias["unit_id"] else ("item", alias["item_id"]))
    matches = []
    for kind, identity in sorted(targets):
        unit = row(db, "asset_units", identity) if kind == "unit" else None
        item = row(db, "asset_items", unit["item_id"] if unit else identity)
        if unit:
            unit["item_name"] = item["name"]
            unit["campus_name"] = row(db, "asset_campuses", unit["campus_id"])["name"]
        matches.append({"target_type": kind, "target_id": identity, "item": item, "unit": unit})
    return matches


def match_version(matches):
    # Include catalog/unit revisions so an already edited target must be reviewed again.
    return fingerprint(json.dumps([
        [m["target_type"], m["target_id"], m["item"]["version"], (m["unit"] or {}).get("version")]
        for m in matches
    ], separators=(",", ":")))


def default_draft(parsed, supplier_lookup):
    if parsed["type"] not in ("supplier", "package"):
        return None
    product = (supplier_lookup or {}).get("product") or {}
    # Catalog limits also apply to untrusted remote metadata and label text.
    def first_text(*values):
        return next((value.strip()[:120] for value in values if isinstance(value, str) and value.strip()), "")
    model = first_text(product.get("model"), parsed.get("model"))
    name = first_text(product.get("name"), parsed.get("model"), parsed["code"])
    return {"name": name, "specification": model, "unit": "个", "kind": "consumable", "category": ""}


@bp.post("/api/assets/scan/resolve")
@read
def resolve():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"raw"}:
        raise Invalid("请提交标签原文")
    parsed = parse_label(data["raw"])
    db = get_db()
    matches = exact_matches(db, parsed)
    supplier_lookup, candidates = None, []
    if parsed["type"] in ("supplier", "package"):
        supplier_lookup = lookup_lcsc(parsed["code"])
        if not matches:
            product = supplier_lookup.get("product") or {}
            models = {model.strip().casefold() for model in (product.get("model"), parsed.get("model"))
                      if isinstance(model, str) and model.strip()}
            if models:
                candidates = [dict(candidate) for candidate in db.execute(
                    "SELECT * FROM asset_items WHERE active=1 ORDER BY name,id"
                ) if candidate["specification"].strip().casefold() in models][:20]
    draft = default_draft(parsed, supplier_lookup) if not matches else None
    history = receipt_history(db, parsed["raw"])
    return {
        "parsed": parsed, "item": matches[0]["item"] if len(matches) == 1 else None,
        "unit": matches[0]["unit"] if len(matches) == 1 else None,
        "status": "ambiguous" if len(matches) > 1 else "matched" if matches else
                  "draft" if draft else "parse_error" if parsed.get("parse_error") else "unknown",
        "matches": matches, "match_version": match_version(matches), "draft": draft,
        "draft_id": "lcsc-cn:" + parsed["code"] if draft else None,
        "history": history, "receipt_version": history[0]["id"] if history else 0,
        "can_manage": manager(), "supplier_lookup": supplier_lookup, "candidates": candidates,
    }


def bind_code(db, parsed, item_id, unit_id, reason):
    require_manager()
    namespace = "lcsc-cn" if not unit_id and parsed["type"] in ("supplier", "package") else "raw"
    code = parsed["code"] if namespace == "lcsc-cn" else parsed["raw"]
    db.execute(
        "INSERT OR IGNORE INTO asset_code_aliases(namespace,code,raw,item_id,unit_id,actor_id,created_at,reason) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (namespace, code, parsed["raw"], None if unit_id else item_id, unit_id,
         current_user()["id"], stamp(), reason),
    )
    # Preserve old clients' supplier lookup when a C number has its first owner.
    if namespace == "lcsc-cn":
        db.execute("INSERT OR IGNORE INTO asset_identifiers(item_id,supplier,code) VALUES(?,'lcsc-cn',?)",
                   (item_id, code))


@bp.post("/api/assets/identifiers")
@write("item_id", "code", "reason")
def identify(db, data):
    require_manager()
    item = row(db, "asset_items", integer(data, "item_id"))
    if item["tracking"] == "status":
        raise Invalid("嘉立创物料继续按数量管理，请选择数量物料", 409)
    code = text(data, "code").upper()
    if not item["active"] or not re.fullmatch(r"C[1-9][0-9]{0,17}", code):
        raise Invalid("请选择启用物料并填写有效 C 编号")
    reason = text(data, "reason", maximum=500)
    parsed = parse_label(code)
    if any(m["target_type"] != "item" or m["target_id"] != item["id"] for m in exact_matches(db, parsed)):
        raise Invalid("该编号已经属于其他物品，请通过扫码核对后明确另建", 409)
    bind_code(db, parsed, item["id"], None, reason)
    return {"event_id": event(db, "identifier", reason, item_id=item["id"], details={"code": code})}


RECEIPT_FIELDS = {
    "raw", "item_id", "campus_id", "new_campus", "quantity", "reason", "receipt_version", "duplicate_reason",
    "draft_id", "draft", "intent", "code_role", "match_version", "target_type", "target_id",
    "force_new", "conflict_reason", "entry_id",
}
DRAFT_FIELDS = {"name", "specification", "unit", "kind", "category", "returnable_mode", "consumable_mode"}


@bp.post("/api/assets/scan/receive")
@write(*RECEIPT_FIELDS)
def receive(db, data):
    return receive_one(db, data)


@bp.post("/api/assets/scan/create-receive")
@write(*(RECEIPT_FIELDS | DRAFT_FIELDS))
def create_receive(db, data):
    require_manager()
    entry = {key: value for key, value in data.items() if key in RECEIPT_FIELDS}
    entry.update(draft_id="single", draft={key: data[key] for key in DRAFT_FIELDS if key in data}, intent="new_item")
    return receive_many(db, [entry], allow_legacy_create=True)[0]


def prepare_receipt(db, entry, allow_legacy_create=False):
    if not isinstance(entry, dict) or set(entry) - (RECEIPT_FIELDS | {"request_id"}):
        raise Invalid("扫码登记字段无效")
    parsed = parse_label(entry.get("raw"))
    matches = exact_matches(db, parsed)
    modern = "match_version" in entry
    if modern and entry["match_version"] != match_version(matches):
        raise Invalid("此码的匹配关系或物品资料已经变化，请重新扫描核对", 409)
    intent = entry.get("intent", "receive")
    if intent not in ("receive", "new_item", "new_unit", "bind"):
        raise Invalid("扫码入库意图无效")
    if "force_new" in entry and type(entry["force_new"]) is not bool:
        raise Invalid("另建档案确认无效")
    history = receipt_history(db, parsed["raw"])
    if integer(entry, "receipt_version", 0) != (history[0]["id"] if history else 0):
        raise Invalid("此标签出现新的登记记录，请重新扫描核对", 409)
    duplicate_reason = text(entry, "duplicate_reason", bool(history), 500)
    conflict_reason = text(entry, "conflict_reason", False, 500)
    selected = None
    if "target_id" in entry or "target_type" in entry:
        target = entry.get("target_type"), integer(entry, "target_id")
        if target[0] not in ("item", "unit"):
            raise Invalid("匹配对象类型无效")
        selected = next((m for m in matches if (m["target_type"], m["target_id"]) == target), None)
        if not selected:
            if not (modern and target[0] == "item" and intent in ("new_unit", "bind")):
                raise Invalid("选择的对象已不再匹配此码，请重新核对", 409)
            row(db, "asset_items", target[1])
            if entry.get("item_id") not in (None, target[1]):
                raise Invalid("所选型号与目标编号不一致")
            entry["item_id"] = target[1]
    elif len(matches) == 1:
        selected = matches[0]
    elif len(matches) > 1 and intent not in ("new_item", "new_unit"):
        raise Invalid("此码对应多个物品，请明确选择匹配对象", 409)
    if len(matches) > 1 and not modern:
        raise Invalid("此码存在多个匹配，请更新页面后选择目标", 409)
    if entry.get("item_id") is not None:
        item_id = integer(entry, "item_id")
        if selected and selected["item"]["id"] != item_id:
            # Choosing another existing model is a new binding, never a silent override.
            if intent not in ("bind", "new_unit"):
                raise Invalid("所选型号与二维码的匹配对象不同", 409)
            selected = None
    else:
        item_id = selected["item"]["id"] if selected else None
    has_draft = bool(entry.get("draft_id") or entry.get("draft"))
    if not modern and (has_draft or intent != "receive") and not (allow_legacy_create and not matches):
        raise Invalid("建档或新增编号前请重新识别，核对最新匹配关系", 409)
    if intent == "new_item" and not has_draft:
        raise Invalid("另建档案需要填写资料")
    if has_draft and item_id is not None and intent != "new_item":
        raise Invalid("请选择已有型号或待建档资料，不能同时指定")
    if has_draft:
        item_id = None
        selected = None
        require_manager()
    if intent in ("new_item", "new_unit", "bind"):
        require_manager()
        if matches and not conflict_reason:
            raise Invalid("此码已有关联，请填写另建或重新关联的核对说明")
    if selected and selected["unit"] and intent not in ("new_item", "new_unit"):
        raise Invalid("此码对应的实物已经入库；另一件重码实物需明确新增并填写说明", 409)
    if not selected and not has_draft:
        if item_id is None:
            raise Invalid("请补充建档资料或选择已有型号", 409)
        if not modern:
            raise Invalid("商品编号尚未关联此型号，请由管理人员核对建档", 409)
        require_manager()
        if matches and not conflict_reason:
            raise Invalid("此码已有其他关联，请填写冲突核对说明")
    return {"data": entry, "parsed": parsed, "matches": matches, "selected": selected,
            "item_id": item_id, "history": history, "duplicate_reason": duplicate_reason,
            "conflict_reason": conflict_reason, "intent": intent, "has_draft": has_draft}


def apply_receipt(db, prepared, draft_items):
    from modules.assets import inventory_campus, stock_change
    from modules.asset_units import create_units

    data, parsed = prepared["data"], prepared["parsed"]
    item_id = draft_items[data["draft_id"]] if prepared["has_draft"] else prepared["item_id"]
    item = row(db, "asset_items", item_id)
    campus_id, quantity = inventory_campus(db, data), integer(data, "quantity")
    campus = row(db, "asset_campuses", campus_id)
    if not item["active"] or not campus["active"]:
        raise Invalid("物品或校区已停用", 409)
    if item["tracking"] in ("status", "migrating"):
        raise Invalid("此型号采用状态管理或正在迁移，不能按数量扫码入库", 409)
    if item["kind"] == "returnable" and item["tracking"] not in ("unit", "quantity"):
        raise Invalid("非耗材请先完成单件迁移", 409)
    role = data.get("code_role", "item" if parsed["type"] in ("supplier", "package", "item") else
                    "serial" if item["tracking"] == "unit" else "item")
    if role not in ("item", "serial"):
        raise Invalid("原码用途无效")
    if role == "serial" and (item["tracking"] != "unit" or quantity != 1):
        raise Invalid("序列号码必须对应逐件管理的一件实物")
    if prepared["intent"] == "new_unit" and item["tracking"] != "unit":
        raise Invalid("新增实物需要选择逐件管理的型号")
    if role == "serial" and prepared["matches"] and prepared["intent"] not in ("new_item", "new_unit"):
        raise Invalid("此码已有关联，作为另一件实物时请明确新增并填写说明", 409)
    reason = text(data, "reason", maximum=500)
    selected = prepared["selected"]
    if item["tracking"] == "unit":
        result = create_units(db, item, campus_id, quantity, reason,
                              [parsed["raw"][:120]] if role == "serial" else None)
        unit_id = result["unit_ids"][0] if role == "serial" else None
    else:
        stock_change(db, item_id, campus_id, quantity)
        result = {"event_id": event(db, "receive", reason, item_id=item_id, campus_id=campus_id, quantity=quantity,
                                   details={"deltas": [[campus_id, quantity]],
                                            "source": parsed.get("supplier", "external"),
                                            "conflict_reason": prepared["conflict_reason"]})}
        unit_id = None
    if not selected or role == "serial" or prepared["intent"] in ("new_item", "new_unit", "bind"):
        bind_code(db, parsed, item_id, unit_id, prepared["conflict_reason"] or reason)
    evidence = {**parsed, "code_role": role, "intent": prepared["intent"],
                "conflict_reason": prepared["conflict_reason"], "unit_ids": result.get("unit_ids", [])}
    receipt_id = db.execute(
        "INSERT INTO asset_receipts(item_id,event_id,fingerprint,raw,parsed,label_quantity,actual_quantity,duplicate_reason) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (item_id, result["event_id"], fingerprint(parsed["raw"]), parsed["raw"],
         json.dumps(evidence, ensure_ascii=False), parsed.get("label_quantity"), quantity, prepared["duplicate_reason"]),
    ).lastrowid
    return {**result, "item_id": item_id, "receipt_id": receipt_id, "actual_quantity": quantity,
            "entry_id": data.get("entry_id"), "actor_id": current_user()["id"], "created_at": stamp()}


def receive_many(db, receipts, allow_legacy_create=False):
    from modules.assets import save_catalog_record

    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 20:
        raise Invalid("每批登记 1 至 20 条扫码记录")
    prepared, seen, entry_ids, drafts, draft_reasons, supplier_targets = [], set(), set(), {}, {}, {}
    # Validate against the same pre-write snapshot. Our own new mappings/receipts
    # must not invalidate later entries that intentionally share a draft/code.
    for index, original in enumerate(receipts):
        if not isinstance(original, dict):
            raise Invalid("扫码登记字段无效")
        entry = dict(original)
        value = prepare_receipt(db, entry, allow_legacy_create)
        raw = value["parsed"]["raw"]
        entry_id = entry.get("entry_id")
        if entry_id is not None:
            entry_id = text(entry, "entry_id", maximum=100)
            if entry_id in entry_ids:
                raise Invalid("本批清单标识重复", 409)
            entry_ids.add(entry_id)
        if raw in seen and not (value["intent"] in ("new_item", "new_unit") and
                                value["conflict_reason"] and entry_id):
            raise Invalid("本批出现重复标签，请先核对，不自动累计数量", 409)
        seen.add(raw)
        if value["has_draft"]:
            draft_id = entry.get("draft_id", "entry:" + str(index))
            if not isinstance(draft_id, str) or not draft_id or len(draft_id) > 100:
                raise Invalid("待建档资料标识无效")
            entry["draft_id"] = draft_id
            draft_reasons.setdefault(draft_id, value["conflict_reason"] or text(entry, "reason", maximum=500))
            if "draft" in entry:
                draft = entry["draft"]
                if not isinstance(draft, dict) or set(draft) - DRAFT_FIELDS:
                    raise Invalid("待建档资料字段无效")
                draft = {**draft, "force_new": entry.get("force_new", False)}
                if draft_id in drafts and drafts[draft_id] != draft:
                    raise Invalid("同一待建档资料存在不一致编辑，请核对", 409)
                drafts[draft_id] = draft
        parsed = value["parsed"]
        if parsed["type"] in ("supplier", "package") and entry.get("code_role", "item") == "item":
            target = ("draft", entry["draft_id"]) if value["has_draft"] else ("item", value["item_id"])
            previous_targets = supplier_targets.setdefault(parsed["code"], set())
            if previous_targets and target not in previous_targets and not (
                value["intent"] in ("new_item", "bind") and value["conflict_reason"]
            ):
                raise Invalid("本批同一商品编号将关联不同档案，请明确另建并填写冲突说明", 409)
            previous_targets.add(target)
        prepared.append(value)
    for value in prepared:
        if value["has_draft"] and value["data"]["draft_id"] not in drafts:
            raise Invalid("待建档资料不存在，请重新补充")
    draft_items = {key: save_catalog_record(db, {**draft, "reason": draft_reasons[key]}, "items")["id"]
                   for key, draft in drafts.items()}
    return [apply_receipt(db, value, draft_items) for value in prepared]


def receive_one(db, data):
    return receive_many(db, [data])[0]


@bp.post("/api/assets/scan/batch")
@write("receipts")
def receive_batch(db, data):
    return {"receipts": receive_many(db, data.get("receipts"))}
