"""Value objects for task attachment API and persistence boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class AttachmentPayload(TypedDict):
    id: int
    task_id: int
    member_id: int
    member_name: str
    original_name: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    description: str
    created_at: str
    url: str
    can_delete: bool


@dataclass(frozen=True, slots=True)
class UploadCommand:
    task_id: int
    member_id: int
    member_name: str
    role: str
    description: str
