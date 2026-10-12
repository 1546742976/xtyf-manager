"""Authenticated task screenshot attachment HTTP API."""
from __future__ import annotations

import io
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Final

from flask import Blueprint, current_app, jsonify, request, send_file, url_for

from common import current_user, get_db, login_required, now_str
from modules.attachments.contracts import AttachmentPayload, UploadCommand
from modules.image_storage import (
    MAX_FILES,
    ImageValidationError,
    PreparedImage,
    StorageWriteError,
    attachment_path,
    cleanup_staged,
    finalize_staged,
    prepare_image,
    stage_images,
)
from modules.task_validation import MAX_SQLITE_INTEGER

bp = Blueprint("task_attachments", __name__)
_MANAGEMENT_ROLES: Final = ("admin", "manager")


def _root() -> Path:
    return Path(current_app.config["TASK_ATTACHMENTS_DIR"])


def _validate_path_ids(view):
    def wrapper(**identifiers: int):
        if any(identifier <= 0 or identifier > MAX_SQLITE_INTEGER for identifier in identifiers.values()):
            return jsonify(ok=False, error="ID 格式不正确"), 400
        return view(**identifiers)
    return wrapper


def _active_task(db: sqlite3.Connection, task_id: int):
    return db.execute(
        "SELECT id, owner_id FROM tasks WHERE id=? AND deleted_at IS NULL", (task_id,)
    ).fetchone()


def _can_upload(db: sqlite3.Connection, task, user) -> bool:
    return user["role"] in _MANAGEMENT_ROLES or task["owner_id"] == user["id"] or db.execute(
        "SELECT 1 FROM task_participants WHERE task_id=? AND member_id=?",
        (task["id"], user["id"]),
    ).fetchone() is not None


def _payload(row, user) -> AttachmentPayload:
    return AttachmentPayload(
        id=row["id"],
        task_id=row["task_id"],
        member_id=row["member_id"],
        member_name=row["member_name"],
        original_name=row["original_name"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        width=row["width"],
        height=row["height"],
        description=row["description"],
        created_at=row["created_at"],
        url=url_for(
            "task_attachments.download_attachment",
            task_id=row["task_id"],
            attachment_id=row["id"],
        ),
        can_delete=user["role"] in _MANAGEMENT_ROLES or row["member_id"] == user["id"],
    )


def _attachment_rows(db: sqlite3.Connection, task_id: int, ids: list[int] | None = None):
    parameters: list[int] = [task_id]
    identifier_filter = ""
    if ids:
        placeholders = ",".join("?" for _ in ids)
        identifier_filter = f" AND a.id IN ({placeholders})"
        parameters.extend(ids)
    return db.execute(
        "SELECT a.*, m.name AS member_name FROM task_attachments a "
        "JOIN members m ON m.id=a.member_id "
        "WHERE a.task_id=? AND a.deleted_at IS NULL" + identifier_filter + " ORDER BY a.id",
        parameters,
    ).fetchall()


@bp.get("/api/task/<int:task_id>/attachments", endpoint="list_attachments")
@login_required
@_validate_path_ids
def list_attachments(task_id: int):
    db = get_db()
    if _active_task(db, task_id) is None:
        return jsonify(ok=False, error="任务不存在或已删除"), 404
    user = current_user()
    rows = _attachment_rows(db, task_id)
    return jsonify(ok=True, attachments=[_payload(row, user) for row in rows])


@bp.post("/api/task/<int:task_id>/attachments", endpoint="upload_attachments")
@login_required
@_validate_path_ids
def upload_attachments(task_id: int):
    db = get_db()
    task = _active_task(db, task_id)
    if task is None:
        return jsonify(ok=False, error="任务不存在或已删除"), 404
    user = current_user()
    if not _can_upload(db, task, user):
        return jsonify(ok=False, error="无权为此任务上传截图"), 403
    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify(ok=False, error="请选择截图文件"), 400
    if len(uploads) > MAX_FILES:
        return jsonify(ok=False, error="每次最多上传 5 张截图"), 400
    try:
        images = [prepare_image(upload) for upload in uploads]
    except ImageValidationError as error:
        return jsonify(ok=False, error=str(error)), error.status_code
    command = UploadCommand(
        task_id=task_id,
        member_id=user["id"],
        member_name=user["name"],
        role=user["role"],
        description=request.form.get("description", "").strip(),
    )
    return _persist_upload(db, command, images)


def _persist_upload(db, command: UploadCommand, images: list[PreparedImage]):
    staged = []
    identifiers: list[int] = []
    try:
        staged = stage_images(_root(), images)
        db.execute("BEGIN IMMEDIATE")
        task = _active_task(db, command.task_id)
        transaction_user = {
            "id": command.member_id, "name": command.member_name, "role": command.role,
        }
        if task is None:
            db.rollback()
            cleanup_staged(staged)
            return jsonify(ok=False, error="任务不存在或已删除"), 404
        if not _can_upload(db, task, transaction_user):
            db.rollback()
            cleanup_staged(staged)
            return jsonify(ok=False, error="无权为此任务上传截图"), 403
        created_at = now_str()
        for image in images:
            cursor = db.execute(
                "INSERT INTO task_attachments "
                "(task_id,member_id,original_name,stored_name,mime_type,size_bytes,width,height,description,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (command.task_id, command.member_id, image.original_name, image.stored_name,
                 image.mime_type, image.size_bytes, image.width, image.height,
                 command.description, created_at),
            )
            identifiers.append(cursor.lastrowid)
        finalize_staged(staged)
        db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (created_at, command.task_id))
        db.execute(
            "INSERT INTO logs (task_id,member_id,member_name,note,changes,created_at) VALUES (?,?,?,?,?,?)",
            (command.task_id, command.member_id, command.member_name, "上传截图",
             f"上传截图 {len(images)} 张", created_at),
        )
        db.commit()
    except (sqlite3.Error, StorageWriteError):
        db.rollback()
        cleanup_staged(staged)
        return jsonify(ok=False, error="截图保存失败，请重试"), 500
    rows = _attachment_rows(db, command.task_id, identifiers)
    return jsonify(ok=True, attachments=[_payload(row, transaction_user) for row in rows])


@bp.get("/api/task/<int:task_id>/attachments/<int:attachment_id>", endpoint="download_attachment")
@login_required
@_validate_path_ids
def download_attachment(task_id: int, attachment_id: int):
    db = get_db()
    if _active_task(db, task_id) is None:
        return jsonify(ok=False, error="任务不存在或已删除"), 404
    row = db.execute(
        "SELECT * FROM task_attachments WHERE id=? AND task_id=? AND deleted_at IS NULL",
        (attachment_id, task_id),
    ).fetchone()
    if row is None:
        return jsonify(ok=False, error="截图不存在或已删除"), 404
    path = attachment_path(_root(), row["stored_name"])
    try:
        with path.open("rb") as stream:
            content = stream.read()
    except FileNotFoundError:
        return jsonify(ok=False, error="截图文件缺失，请联系管理员"), 404
    response = send_file(
        io.BytesIO(content), mimetype=row["mime_type"], as_attachment=True,
        download_name=row["original_name"], max_age=0,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.post("/api/task/<int:task_id>/attachments/<int:attachment_id>/delete", endpoint="delete_attachment")
@login_required
@_validate_path_ids
def delete_attachment(task_id: int, attachment_id: int):
    db = get_db()
    if _active_task(db, task_id) is None:
        return jsonify(ok=False, error="任务不存在或已删除"), 404
    row = db.execute(
        "SELECT * FROM task_attachments WHERE id=? AND task_id=? AND deleted_at IS NULL",
        (attachment_id, task_id),
    ).fetchone()
    if row is None:
        return jsonify(ok=False, error="截图不存在或已删除"), 404
    user = current_user()
    if user["role"] not in _MANAGEMENT_ROLES and row["member_id"] != user["id"]:
        return jsonify(ok=False, error="无权删除此截图"), 403
    path = attachment_path(_root(), row["stored_name"])
    if not path.is_file():
        return jsonify(ok=False, error="截图文件缺失，未删除记录"), 404
    deleted_at = now_str()
    tombstone = path.with_name(f".{path.name}.deleted.{secrets.token_hex(8)}")
    try:
        db.execute("BEGIN IMMEDIATE")
        if _active_task(db, task_id) is None:
            db.rollback()
            return jsonify(ok=False, error="任务不存在或已删除"), 404
        locked_row = db.execute(
            "SELECT * FROM task_attachments WHERE id=? AND task_id=? AND deleted_at IS NULL",
            (attachment_id, task_id),
        ).fetchone()
        if locked_row is None:
            db.rollback()
            return jsonify(ok=False, error="截图已被删除"), 404
        if user["role"] not in _MANAGEMENT_ROLES and locked_row["member_id"] != user["id"]:
            db.rollback()
            return jsonify(ok=False, error="无权删除此截图"), 403
        os.replace(path, tombstone)
        changed = db.execute(
            "UPDATE task_attachments SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
            (deleted_at, attachment_id),
        ).rowcount
        if changed != 1:
            db.rollback()
            return jsonify(ok=False, error="截图已被删除"), 404
        db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (deleted_at, task_id))
        db.execute(
            "INSERT INTO logs (task_id,member_id,member_name,note,changes,created_at) VALUES (?,?,?,?,?,?)",
            (task_id, user["id"], user["name"], "删除截图", "删除截图", deleted_at),
        )
        db.commit()
    except OSError:
        db.rollback()
        return jsonify(ok=False, error="截图文件暂时无法删除"), 500
    except sqlite3.Error:
        if tombstone.is_file():
            try:
                os.replace(tombstone, path)
            except OSError:
                db.commit()
                return jsonify(ok=False, error="截图已删除，但文件清理待重试"), 500
        db.rollback()
        return jsonify(ok=False, error="截图删除失败，请重试"), 500
    try:
        tombstone.unlink()
    except OSError:
        return jsonify(ok=True, cleanup_pending=True)
    return jsonify(ok=True, cleanup_pending=False)
