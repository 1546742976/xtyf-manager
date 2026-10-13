"""Task progress history with atomic screenshot uploads and optimistic locking."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, url_for

from common import current_user, get_db, login_required, now_str
from modules.image_storage import (
    MAX_FILES, ImageValidationError, StorageWriteError, cleanup_staged,
    finalize_staged, prepare_image, stage_images,
)
from modules.task_attachments import _can_upload
from modules.task_validation import MAX_SQLITE_INTEGER, InvalidIdError, parse_id

bp = Blueprint("task_progress", __name__)
MAX_NOTE_LENGTH = 2000


def _active_task(db, task_id: int):
    return db.execute(
        "SELECT id,owner_id,progress,version FROM tasks WHERE id=? AND deleted_at IS NULL",
        (task_id,),
    ).fetchone()


@bp.get("/api/task/<int:task_id>/progress")
@login_required
def history(task_id: int):
    if task_id > MAX_SQLITE_INTEGER:
        return jsonify(ok=False, error="任务 ID 不合法"), 400
    db = get_db()
    if _active_task(db, task_id) is None:
        return jsonify(ok=False, error="任务不存在"), 404
    updates = [dict(row) for row in db.execute(
        "SELECT id,actor_name,progress,note,created_at FROM task_progress_updates "
        "WHERE task_id=? ORDER BY id DESC LIMIT 100", (task_id,),
    ).fetchall()]
    if updates:
        ids = [int(item["id"]) for item in updates]
        placeholders = ",".join("?" for _ in ids)
        images = db.execute(
            "SELECT id,progress_update_id,original_name FROM task_attachments "
            "WHERE progress_update_id IN (" + placeholders + ") AND task_id=? "
            "AND deleted_at IS NULL ORDER BY id",
            [*ids, task_id],
        ).fetchall()
        by_update = {id_: [] for id_ in ids}
        for image in images:
            by_update[int(image["progress_update_id"])].append({
                "id": int(image["id"]),
                "name": image["original_name"],
                "url": url_for(
                    "task_attachments.download_attachment",
                    task_id=task_id, attachment_id=int(image["id"]),
                ),
            })
        for update in updates:
            update["images"] = by_update[int(update["id"])]
    return jsonify(ok=True, updates=updates)


@bp.post("/api/task/<int:task_id>/progress")
@login_required
def update_progress(task_id: int):
    if task_id > MAX_SQLITE_INTEGER:
        return jsonify(ok=False, error="任务 ID 不合法"), 400
    raw_progress = request.form.get("progress", "")
    if not raw_progress.isascii() or not raw_progress.isdecimal():
        return jsonify(ok=False, error="进度必须是 0～100 的整数"), 400
    progress = int(raw_progress)
    if progress > 100:
        return jsonify(ok=False, error="进度必须是 0～100 的整数"), 400
    note = request.form.get("note", "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        return jsonify(ok=False, error="进度备注最多 2000 字"), 400
    try:
        version = parse_id(request.form.get("version"), "version", False)
    except InvalidIdError:
        return jsonify(ok=False, error="版本号不合法"), 400
    uploads = request.files.getlist("files")
    if len(uploads) > MAX_FILES:
        return jsonify(ok=False, error="每次最多上传 5 张截图"), 400
    try:
        images = [prepare_image(upload) for upload in uploads]
    except ImageValidationError as error:
        return jsonify(ok=False, error=str(error)), error.status_code
    staged = []
    db = get_db()
    user = current_user()
    root = Path(current_app.config["TASK_ATTACHMENTS_DIR"])
    try:
        if images:
            staged = stage_images(root, images)
        db.execute("BEGIN IMMEDIATE")
        task = _active_task(db, task_id)
        if task is None:
            db.rollback()
            cleanup_staged(staged)
            return jsonify(ok=False, error="任务不存在"), 404
        if not _can_upload(db, task, user):
            db.rollback()
            cleanup_staged(staged)
            return jsonify(ok=False, error="无权更新该任务进度"), 403
        if int(task["version"]) != version:
            db.rollback()
            cleanup_staged(staged)
            return jsonify(ok=False, error="任务已更新，请刷新后重试"), 409
        if int(task["progress"] or 0) == progress and not note and not images:
            db.rollback()
            cleanup_staged(staged)
            return jsonify(ok=False, error="进度未改变，请填写备注或添加图片"), 400
        stamp = now_str()
        update_id = db.execute(
            "INSERT INTO task_progress_updates(task_id,actor_id,actor_name,progress,note,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, user["id"], user["name"], progress, note, stamp),
        ).lastrowid
        updated = db.execute(
            "UPDATE tasks SET progress=?,version=?,updated_at=? "
            "WHERE id=? AND version=? AND deleted_at IS NULL",
            (progress, version + 1, stamp, task_id, version),
        )
        if updated.rowcount != 1:
            db.rollback()
            cleanup_staged(staged)
            return jsonify(ok=False, error="任务已更新，请刷新后重试"), 409
        for image in images:
            db.execute(
                "INSERT INTO task_attachments "
                "(task_id,member_id,original_name,stored_name,mime_type,size_bytes,"
                "width,height,description,created_at,progress_update_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, user["id"], image.original_name, image.stored_name,
                 image.mime_type, image.size_bytes, image.width, image.height,
                 note[:300], stamp, update_id),
            )
        if staged:
            finalize_staged(staged)
        db.execute(
            "INSERT INTO logs(task_id,member_id,member_name,note,changes,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (task_id, user["id"], user["name"], note,
             f"更新进度为 {progress}%（附 {len(images)} 张图片）", stamp),
        )
        db.commit()
        return jsonify(ok=True, id=task_id, version=version + 1, progress=progress,
                       update_id=update_id)
    except (sqlite3.Error, StorageWriteError, OSError):
        db.rollback()
        cleanup_staged(staged)
        return jsonify(ok=False, error="保存进度失败，请重试"), 500
