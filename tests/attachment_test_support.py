"""Test-only image and account helpers for task attachment scenarios."""
from __future__ import annotations

import io
from dataclasses import dataclass
from uuid import uuid4

from PIL import Image
from werkzeug.test import Client


@dataclass(frozen=True, slots=True)
class MemberSession:
    member_id: int
    client: Client


def image_bytes(
    image_format: str = "PNG",
    size: tuple[int, int] = (24, 12),
    *,
    exif_orientation: int | None = None,
) -> bytes:
    image = Image.new("RGB", size, (23, 97, 151))
    exif = Image.Exif()
    if exif_orientation is not None:
        exif[274] = exif_orientation
    output = io.BytesIO()
    image.save(output, format=image_format, exif=exif)
    return output.getvalue()


def upload_payload(
    content: bytes,
    name: str = "screen.png",
    mime_type: str = "image/png",
) -> tuple[io.BytesIO, str, str]:
    return io.BytesIO(content), name, mime_type


def create_member(app_module, admin: Client, *, participant_task: int | None = None) -> MemberSession:
    suffix = uuid4().hex[:12]
    username = "attachment_" + suffix
    response = admin.post("/api/member", json={
        "name": "截图成员" + suffix,
        "username": username,
        "password": "attachment-test-password",
        "role": "member",
    })
    member_id = response.get_json()["id"]
    if participant_task is not None:
        with app_module.app.app_context():
            db = app_module.get_db()
            db.execute(
                "INSERT INTO task_participants (task_id, member_id) VALUES (?, ?)",
                (participant_task, member_id),
            )
            db.commit()
    client = app_module.app.test_client()
    login = client.post("/login", json={
        "username": username,
        "password": "attachment-test-password",
    })
    if not login.get_json()["ok"]:
        raise AssertionError("test member login failed")
    return MemberSession(member_id=member_id, client=client)
