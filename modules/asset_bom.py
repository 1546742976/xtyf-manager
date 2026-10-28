"""Offline BOM spreadsheet preview and transactional quantity-stock batches.

The uploaded file is only parsed in memory; it never changes inventory. Writes
use the shared asset_requests idempotency ledger and immutable asset_events.
"""
import csv
import io
from decimal import Decimal, InvalidOperation
import re
import zipfile

from flask import Blueprint, jsonify, request
from common import get_db, login_required
from modules.task_validation import InvalidIdError, parse_id
from modules.request_limits import asset_request_limit
from modules.assets import (
    Invalid, MAX_QUANTITY, integer, manager, operate_record, require_manager,
    row, save_catalog_record, text, mutation,
)

bp = Blueprint("asset_bom", __name__)
FILE_LIMIT = 2 * 1024 * 1024
EXPANDED_LIMIT = 12 * 1024 * 1024
ROW_LIMIT = 200
BATCH_LIMIT = 200

ALIASES = {
    "code": {"编号", "物料编号", "物料编码", "嘉立创编号", "嘉立创料号", "c编号", "商品编号",
             "物料代码", "库存编号", "sku", "code", "lcsc", "lcscpart", "partnumber", "partno"},
    "name": {"名称", "物料名称", "元件名称", "品名", "商品名称", "器件名称", "name", "description", "component"},
    "specification": {"规格", "规格型号", "型号", "封装", "规格参数", "spec", "specification", "model", "footprint", "package"},
    "quantity": {"数量", "用量", "每套数量", "每套用量", "单套用量", "需求数量", "实收数量", "qty", "quantity", "count", "designatorcount"},
    "unit": {"单位", "计量单位", "unit"},
}
ALIAS_LOOKUP = {alias: name for name, aliases in ALIASES.items() for alias in aliases}


def normalize(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(value or "").strip().casefold())


def clean(value, limit=120):
    result = str(value if value is not None else "").strip()
    if len(result) > limit:
        raise Invalid("表格中存在超长单元格，请缩短后重新导入")
    return result


def parse_quantity(value):
    if isinstance(value, bool):
        raise Invalid("数量必须是正整数")
    s = clean(value, 40)
    if "," in s:
        if not re.fullmatch(r"[1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.0+)?", s):
            raise Invalid("数量的千分位格式无效")
        s = s.replace(",", "")
    if not re.fullmatch(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", s):
        raise Invalid("数量必须是正整数，可使用小数零尾或科学计数法")
    try:
        n = Decimal(s)
    except InvalidOperation:
        raise Invalid("数量必须是正整数") from None
    if not n.is_finite() or not 1 <= n <= MAX_QUANTITY or n != n.to_integral_value():
        raise Invalid("数量必须是 1 至 1000000000 的整数")
    return int(n)


def bounded_rows(records):
    result = []
    for index, record in enumerate(records):
        if index >= 4096:
            raise Invalid("表格超过 4096 个物理行，请删除多余空行或拆分文件")
        if any(value is not None and str(value).strip() for value in record[32:]):
            raise Invalid("表格超过 32 列，请删除多余列后导入")
        result.append(list(record[:32]))
    return result


def sparse_xlsx_rows(sheet):
    # openpyxl is pinned to 3.1.5. Use its streaming cell decoder (including
    # shared strings, cached formulas and dates) without dense _get_row tuples.
    from openpyxl.worksheet._reader import WorkSheetParser
    class BoundedCellParser(WorkSheetParser):
        def parse_cell(self, element):
            cell = super().parse_cell(element)
            # An uncached formula has value=None in data_only mode, but is
            # actual content (including shared formulas with no own text).
            if cell["column"] > 32 and element.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f") is not None:
                raise Invalid("表格超过 32 列，请删除多余列后导入")
            return cell

    result = []
    with sheet._get_source() as source:
        parser = BoundedCellParser(source, sheet._shared_strings, data_only=True,
                                 epoch=sheet.parent.epoch, date_formats=sheet.parent._date_formats,
                                 timedelta_formats=sheet.parent._timedelta_formats)
        for index, cells in parser.parse():
            if not 1 <= index <= 4096:
                raise Invalid("表格超过 4096 个物理行，请删除多余空行或拆分文件")
            while len(result) < index:
                result.append([None] * 32)
            for cell in cells:
                column, value = cell["column"], cell["value"]
                if not 1 <= cell["row"] <= 4096:
                    raise Invalid("表格超过 4096 个物理行，请删除多余空行或拆分文件")
                if column > 32:
                    if value is not None and str(value).strip():
                        raise Invalid("表格超过 32 列，请删除多余列后导入")
                elif column >= 1:
                    result[index - 1][column - 1] = value
    return result


def spreadsheet_rows(upload):
    name = (upload.filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    suffix = name.lower().rsplit(".", 1)[-1]
    if suffix not in ("csv", "tsv", "xlsx"):
        raise Invalid("仅支持 CSV、TSV 和 XLSX 文件（旧版 XLS 请另存为 XLSX）")
    raw = upload.stream.read(FILE_LIMIT + 1)
    if not raw or len(raw) > FILE_LIMIT:
        raise Invalid("文件为空或超过 2 MiB")
    if suffix in ("csv", "tsv"):
        try:
            payload = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                payload = raw.decode("gb18030")
            except UnicodeDecodeError:
                raise Invalid("CSV 编码无效，请保存为 UTF-8") from None
        sample = payload[:4096]
        delimiter = "\t" if suffix == "tsv" else (max((",", "\t", ";"), key=sample.count))
        try:
            return bounded_rows(csv.reader(io.StringIO(payload), delimiter=delimiter))
        except csv.Error:
            raise Invalid("CSV 单元格过长或格式无效，请修正后导入") from None
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise Invalid("XLSX 文件损坏")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if len(archive.infolist()) > 2048 or sum(x.file_size for x in archive.infolist()) > EXPANDED_LIMIT:
            raise Invalid("XLSX 解压大小超出限制")
    from openpyxl import load_workbook
    try:
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
        try:
            sheet = workbook.worksheets[0]
            return sparse_xlsx_rows(sheet)
        finally:
            workbook.close()
    except (ValueError, KeyError, OSError, zipfile.BadZipFile, IndexError):
        raise Invalid("XLSX 解析失败，请确认文件内容与表头") from None


def extract_table(source):
    headers, header_index = None, None
    for index, record in enumerate(source[:10]):
        mapping = {}
        for column, value in enumerate(record[:32]):
            field = ALIAS_LOOKUP.get(normalize(value))
            if field and field not in mapping:
                mapping[field] = column
        if "quantity" in mapping and ("code" in mapping or "name" in mapping):
            headers, header_index = mapping, index
            break
    if headers is None:
        raise Invalid("无法识别表头：需要 数量 与 名称／物料编号 两列")
    result = []
    for row_no, cells in enumerate(source[header_index + 1:], start=header_index + 2):
        def cell(key):
            pos = headers.get(key)
            return clean(cells[pos]) if pos is not None and pos < len(cells) else ""
        code, name = cell("code"), cell("name")
        quantity = cell("quantity")
        if not any((code, name, quantity, cell("specification"))):
            continue
        if not quantity:
            raise Invalid(f"第 {row_no} 行数量为空")
        try:
            amount = parse_quantity(quantity)
        except Invalid as exc:
            raise Invalid(f"第 {row_no} 行：{exc.message}") from exc
        if not code and not name:
            raise Invalid(f"第 {row_no} 行缺少编号或名称")
        if len(result) >= ROW_LIMIT:
            raise Invalid(f"每次最多导入 {ROW_LIMIT} 行，请拆分文件")
        result.append({"row_no": row_no, "code": code, "name": name,
                       "specification": cell("specification"), "unit": cell("unit") or "个",
                       "quantity": amount})
    if not result:
        raise Invalid("表格没有可导入的物料行")
    return result


def match_candidates(db, entry, campus_id):
    code, name, spec = entry["code"].upper(), entry["name"].casefold(), entry["specification"].casefold()
    ids = set()
    if re.fullmatch(r"M:[1-9][0-9]*", code):
        try:
            ids.add(parse_id(code[2:], "物料 ID", False))
        except InvalidIdError as exc:
            raise Invalid(str(exc)) from None
    if re.fullmatch(r"C[1-9][0-9]{0,17}", code):
        ids.update(r[0] for r in db.execute(
            "SELECT item_id FROM asset_identifiers WHERE code=? "
            "UNION SELECT item_id FROM asset_code_aliases WHERE namespace='lcsc-cn' AND code=?",
            (code, code)))
    # A supplied recognized vendor identifier must not silently fall back to a
    # same-name item when its C number has not been linked.
    if not ids and name and not re.fullmatch(r"C[1-9][0-9]{0,17}", code):
        catalog = db.execute("SELECT id,name,specification FROM asset_items WHERE active=1").fetchall()
        same_name = [r for r in catalog if r["name"].casefold() == name]
        ids.update(r["id"] for r in same_name if not spec or r["specification"].casefold() == spec)
    elif not ids and code and not re.fullmatch(r"C[1-9][0-9]{0,17}", code):
        ids.update(r["id"] for r in db.execute(
            "SELECT id FROM asset_items WHERE upper(specification)=? AND active=1 LIMIT 20", (code,)))
    result = []
    for item_id in sorted(ids)[:20]:
        found = db.execute("SELECT id,name,specification,unit,kind,tracking,active,version "
                           "FROM asset_items WHERE id=?", (item_id,)).fetchone()
        if found is None:
            continue
        item = dict(found)
        stock = db.execute("SELECT available,version FROM asset_stock WHERE item_id=? AND campus_id=?",
                           (item_id, campus_id)).fetchone()
        item.update(available=stock["available"] if stock else 0,
                    stock_version=stock["version"] if stock else 0)
        result.append(item)
    return result



def raw_table(upload):
    """Expose bounded cells for a human-selected header and column mapping."""
    source = spreadsheet_rows(upload)
    lines = []
    for row_no, cells in enumerate(source, start=1):
        values = [clean(value) for value in cells[:32]]
        if any(values):
            lines.append({"row_no": row_no, "cells": values})
    if not lines:
        raise Invalid("表格没有可导入的数据")
    best, score = lines[0]["row_no"], -1
    for record in lines:
        if record["row_no"] > 10:
            break
        found = {ALIAS_LOOKUP.get(normalize(v)) for v in record["cells"]}
        rank = (4 if "quantity" in found else 0) + (3 if "name" in found or "code" in found else 0) + len(found - {None})
        if rank > score:
            best, score = record["row_no"], rank
    if len([v for v in lines if v["row_no"] > best]) > ROW_LIMIT:
        raise Invalid(f"每次最多导入 {ROW_LIMIT} 行，请拆分文件")
    return lines, best


@bp.post("/api/assets/bom/import")
@login_required
def import_sheet():
    try:
        if request.content_length and request.content_length > FILE_LIMIT + 16384:
            raise Invalid("文件上传体积过大", 413)
        upload = request.files.get("file")
        if upload is None:
            raise Invalid("请选择文件")
        lines, header_row = raw_table(upload)
        return jsonify(ok=True, lines=lines, header_row=header_row,
                       file_name=clean(upload.filename or "", 120))
    except Invalid as exc:
        return jsonify(ok=False, error=exc.message), exc.status


@bp.post("/api/assets/bom/match")
@login_required
def match_rows():
    """Match a manually edited list without writing inventory or creating assets."""
    try:
        if request.content_length and request.content_length > asset_request_limit(request.endpoint):
            raise Invalid("匹配请求体过大", 413)
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or set(data) - {"campus_id", "rows"}:
            raise Invalid("物料匹配参数无效")
        campus_id = data.get("campus_id")
        if type(campus_id) is not int:
            raise Invalid("请选择操作校区")
        db = get_db()
        if not row(db, "asset_campuses", campus_id)["active"]:
            raise Invalid("校区已停用", 409)
        entries = data.get("rows")
        if not isinstance(entries, list) or not 1 <= len(entries) <= ROW_LIMIT:
            raise Invalid(f"每次必须匹配 1 至 {ROW_LIMIT} 行")
        result = []
        for i, supplied in enumerate(entries, start=1):
            if not isinstance(supplied, dict) or set(supplied) - {"code", "name", "specification", "unit", "quantity"}:
                raise Invalid(f"第 {i} 行字段无效")
            entry = {key: clean(supplied.get(key)) for key in ("code", "name", "specification", "unit")}
            if not entry["code"] and not entry["name"]:
                raise Invalid(f"第 {i} 行缺少物料编号或名称")
            entry["quantity"] = parse_quantity(supplied.get("quantity"))
            result.append(match_candidates(db, entry, campus_id))
        return jsonify(ok=True, matches=result, can_manage=manager())
    except Invalid as exc:
        return jsonify(ok=False, error=exc.message), exc.status


@bp.post("/api/assets/bom/preview")
@login_required
def preview():
    try:
        if request.content_length and request.content_length > FILE_LIMIT + 16384:
            raise Invalid("文件上传体积过大", 413)
        upload = request.files.get("file")
        if upload is None:
            raise Invalid("请选择文件")
        campus_id = int(request.form.get("campus_id", "0"))
        campus = row(get_db(), "asset_campuses", campus_id)
        if not campus["active"]:
            raise Invalid("校区已停用", 409)
        mode = request.form.get("mode")
        if mode not in ("inbound", "outbound"):
            raise Invalid("请选择入库或出库")
        rows = extract_table(spreadsheet_rows(upload))
        db = get_db()
        for entry in rows:
            entry["candidates"] = match_candidates(db, entry, campus_id)
        return jsonify(ok=True, rows=rows, can_manage=manager(), file_name=clean(upload.filename or "", 120))
    except (Invalid, ValueError, TypeError) as exc:
        if isinstance(exc, Invalid):
            return jsonify(ok=False, error=exc.message), exc.status
        return jsonify(ok=False, error="校区参数无效"), 400


def write_batch(view):
    view.asset_fields = {"request_id", "mode", "campus_id", "sets", "reason", "rows"}
    return mutation(view)


@bp.post("/api/assets/bom/commit")
@write_batch
def commit(db, data):
    mode = text(data, "mode")
    if mode not in ("inbound", "outbound"):
        raise Invalid("请选择入库或出库")
    campus_id = integer(data, "campus_id")
    campus = row(db, "asset_campuses", campus_id)
    if not campus["active"]:
        raise Invalid("校区已停用", 409)
    sets = integer(data, "sets")
    reason = text(data, "reason", maximum=500)
    rows = data.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= BATCH_LIMIT:
        raise Invalid(f"每批必须包含 1 至 {BATCH_LIMIT} 行物料")
    quantities, identities = {}, {}
    created, new_drafts = [], {}
    for entry in rows:
        if not isinstance(entry, dict):
            raise Invalid("物料行格式无效")
        qty = integer(entry, "quantity")
        total_qty = qty * sets if mode == "outbound" else qty
        if total_qty > MAX_QUANTITY:
            raise Invalid("乘以套数后的数量超限")
        item_id = entry.get("item_id")
        if item_id is None:
            if mode != "inbound":
                raise Invalid("出库不能包含未匹配物料")
            require_manager()
            if set(entry) - {"item_id", "quantity", "name", "specification", "unit", "code"}:
                raise Invalid("新建物料参数无效")
            candidate = {"name": text(entry, "name"), "specification": text(entry, "specification", False),
                         "unit": text(entry, "unit", maximum=12), "kind": "consumable",
                         "consumable_mode": "quantity"}
            code = text(entry, "code", False, 40).upper()
            if code:
                if not re.fullmatch(r"C[1-9][0-9]{0,17}", code):
                    raise Invalid("新建物料的外部编号仅支持嘉立创 C 编号")
                candidate["supplier_code"] = code
            draft_key = code or ("name", candidate["name"].casefold(),
                                 candidate["specification"].casefold())
            if draft_key in new_drafts:
                item_id, original = new_drafts[draft_key]
                if candidate != original:
                    raise Invalid("同一待建档物料存在不一致的名称、规格或单位")
            else:
                item_id = save_catalog_record(db, candidate, "items")["id"]
                new_drafts[draft_key] = (item_id, candidate)
                created.append(item_id)
        else:
            if set(entry) - {"item_id", "quantity", "version", "stock_version"}:
                raise Invalid("物料行参数无效")
            item_id = integer(entry, "item_id")
        item = row(db, "asset_items", item_id)
        if not item["active"] or item["tracking"] in ("unit", "migrating", "status"):
            raise Invalid(f"M:{item_id} 不支持按数量批量出入库，请走单件或状态管理", 409)
        if entry.get("item_id") is not None:
            if integer(entry, "version") != item["version"]:
                raise Invalid(f"M:{item_id} 物料资料已更新，请重新预览", 409)
            current_stock = db.execute("SELECT available,version FROM asset_stock "
                                       "WHERE item_id=? AND campus_id=?", (item_id, campus_id)).fetchone()
            snapshot = current_stock["version"] if current_stock else 0
            if integer(entry, "stock_version", 0) != snapshot:
                raise Invalid(f"M:{item_id} 库存已经变化，请重新预览", 409)
        if item_id in identities and identities[item_id]["kind"] != item["kind"]:
            raise Invalid("同一物料类型不一致")
        identities[item_id] = item
        quantities[item_id] = quantities.get(item_id, 0) + total_qty
        if quantities[item_id] > MAX_QUANTITY:
            raise Invalid("相同物料汇总数量超限")
    if mode == "outbound":
        for item_id, qty in quantities.items():
            stock = db.execute("SELECT available FROM asset_stock WHERE item_id=? AND campus_id=?",
                               (item_id, campus_id)).fetchone()
            if (stock["available"] if stock else 0) < qty:
                raise Invalid(f"M:{item_id} 库存不足，需要 {qty}，请修改套数或补货", 409)
    results = []
    for item_id, qty in quantities.items():
        action = "receive" if mode == "inbound" else (
            "consume" if identities[item_id]["kind"] == "consumable" else "borrow")
        result = operate_record(db, {"action": action, "item_id": item_id,
                                     "campus_id": campus_id, "quantity": qty,
                                     "reason": f"BOM批量{'入库' if mode == 'inbound' else f'出库（{sets}套）'}：{reason}"})
        results.append({"item_id": item_id, "quantity": qty, "action": action,
                        "event_id": result["event_id"], "loan_id": result["loan_id"]})
    return {"rows": len(rows), "sets": sets, "created": created, "operations": results}
