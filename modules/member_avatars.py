"""Self-service avatars, stored privately alongside the existing attachments."""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from PIL import Image

from common import avatar_url, current_user, get_db, login_required
from modules.image_storage import (
    ImageValidationError, PreparedImage, StorageWriteError, attachment_path,
    cleanup_staged, finalize_staged, prepare_image, stage_images,
)

bp = Blueprint("member_avatars", __name__)


def migrate(db):
    with db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(members)")}
        if "avatar_key" not in columns:
            db.execute("ALTER TABLE members ADD COLUMN avatar_key TEXT")


def _root():
    return Path(current_app.config["TASK_ATTACHMENTS_DIR"]) / "avatars"


def prepare_avatar(upload, crop):
    if (not isinstance(crop, dict) or set(crop) != {"x", "y", "size"}
            or any(type(value) is not int for value in crop.values())):
        raise ImageValidationError("请选择有效的头像裁剪区域")
    image = prepare_image(upload)
    x, y, size = crop["x"], crop["y"], crop["size"]
    if x < 0 or y < 0 or size <= 0 or x + size > image.width or y + size > image.height:
        raise ImageValidationError("头像裁剪区域超出图片范围")
    with Image.open(io.BytesIO(image.content)) as source:
        with source.crop((x, y, x + size, y + size)) as cropped:
            with cropped.resize((512, 512), Image.Resampling.LANCZOS) as resized:
                with resized.convert("RGBA" if "A" in resized.getbands() else "RGB") as output:
                    output.info.clear()
                    stream = io.BytesIO()
                    output.save(stream, format="WEBP", quality=90, method=4)
    return PreparedImage("avatar.webp", Path(image.stored_name).stem + ".webp",
                         "image/webp", 512, 512, stream.getvalue())


def _cleanup(staged):
    try:
        cleanup_staged(staged)
    except OSError:
        current_app.logger.exception("Could not clean up staged avatar files")


def _remove_old(key):
    if key:
        try:
            attachment_path(_root(), key).unlink(missing_ok=True)
        except (OSError, ImageValidationError):
            # The committed new avatar remains valid even if old-file cleanup fails.
            current_app.logger.exception("Could not remove previous avatar file")


def _replace(image=None):
    db = get_db()
    staged = []
    try:
        if image is not None:
            staged = stage_images(_root(), [image])
        db.execute("BEGIN IMMEDIATE")
        # Check the live account again after decoding and acquiring the write lock.
        user = current_user()
        if user is None or user["must_change_password"]:
            db.rollback()
            _cleanup(staged)
            return jsonify(ok=False, error="账户状态已变化，请重新登录后重试"), 403
        old = db.execute("SELECT avatar_key FROM members WHERE id=?", (user["id"],)).fetchone()[0]
        key = image.stored_name if image else None
        if staged:
            finalize_staged(staged)
        db.execute("UPDATE members SET avatar_key=? WHERE id=?", (key, user["id"]))
        db.commit()
    except (sqlite3.Error, OSError, StorageWriteError):
        db.rollback()
        _cleanup(staged)
        return jsonify(ok=False, error="头像保存失败，请重试"), 500
    # Names are unique per upload. A later writer can never start referencing old.
    _remove_old(old)
    return jsonify(ok=True, avatar_url=avatar_url(key))


@bp.route("/api/account/avatar", methods=["GET", "POST", "DELETE"])
@login_required
def account_avatar():
    if request.method == "GET":
        user = current_user()
        key = get_db().execute("SELECT avatar_key FROM members WHERE id=?", (user["id"],)).fetchone()[0]
        if not key:
            return jsonify(ok=False, error="尚未设置头像"), 404
        try:
            # Read before returning to avoid replacement/unlink races on Windows.
            content = attachment_path(_root(), key).read_bytes()
        except (OSError, ImageValidationError):
            return jsonify(ok=False, error="头像文件不可用，请重新上传"), 404
        return send_file(io.BytesIO(content), mimetype="image/webp", max_age=0)

    if request.args:
        return jsonify(ok=False, error="头像操作不接受指定其他用户或附加参数"), 400
    if request.method == "DELETE":
        if request.get_data():
            return jsonify(ok=False, error="恢复默认头像无需提交其他字段"), 400
        return _replace()

    if (set(request.files) != {"avatar"} or len(request.files.getlist("avatar")) != 1
            or set(request.form) != {"crop"} or len(request.form.getlist("crop")) != 1):
        return jsonify(ok=False, error="请提交一张头像及裁剪区域"), 400
    try:
        crop = json.loads(request.form["crop"])
    except (TypeError, ValueError):
        return jsonify(ok=False, error="头像裁剪区域格式不正确"), 400
    try:
        image = prepare_avatar(request.files["avatar"], crop)
    except ImageValidationError as error:
        return jsonify(ok=False, error=str(error).replace("截图", "头像")), error.status_code
    return _replace(image)
