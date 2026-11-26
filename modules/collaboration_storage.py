"""Private streamed originals and optional, separately decoded static thumbnails."""
from __future__ import annotations

import hashlib
import io
import os
import secrets
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FILES = 5
MAX_FILE_BYTES = int(os.environ.get("COLLAB_MAX_FILE_BYTES", 25 * 1024 * 1024))
MAX_TOTAL_BYTES = int(os.environ.get("COLLAB_MAX_TOTAL_BYTES", 60 * 1024 * 1024))
MEMBER_QUOTA_BYTES = int(os.environ.get("COLLAB_MEMBER_QUOTA_BYTES", 512 * 1024 * 1024))
MAX_PIXELS = 20_000_000
MAX_SIDE = 10_000
CHUNK_BYTES = 1024 * 1024


class UploadError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@dataclass
class StagedUpload:
    original_name: str
    stored_name: str
    temporary_path: Path
    final_path: Path
    size_bytes: int = 0
    sha256: str = ''
    thumbnail_name: str | None = None
    thumbnail_temporary_path: Path | None = None
    thumbnail_final_path: Path | None = None


def attachment_path(root, name):
    if not isinstance(name, str) or not name or '/' in name or '\\' in name or Path(name).name != name:
        raise UploadError('附件存储记录无效', 404)
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise UploadError('附件存储记录无效', 404)
    return path


def _thumbnail(source):
    """Never expose an uploaded SVG/HTML or animation as an inline resource."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if image.format not in ('JPEG', 'PNG', 'WEBP', 'GIF'):
                    return None
                width, height = image.size
                if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
                    return None
                image.seek(0)
                image.load()
                with ImageOps.exif_transpose(image).convert('RGBA') as normalized:
                    normalized.thumbnail((1280, 1280))
                    normalized.info.clear()
                    output = io.BytesIO()
                    normalized.save(output, format='PNG')
                    return output.getvalue()
    except (Image.DecompressionBombWarning, Image.DecompressionBombError, UnidentifiedImageError,
            OSError, SyntaxError, ValueError):
        # It is still a valid arbitrary file; only the safe image preview is unavailable.
        return None


def stage_uploads(root, uploads):
    if len(uploads) > MAX_FILES:
        raise UploadError('每次最多上传 5 个文件')
    staged = []
    total = 0
    try:
        if uploads:
            root.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            name = (upload.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
            name = ''.join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip()
            if not name or len(name) > 255:
                raise UploadError('文件名不能为空或超过 255 个字符')
            token = secrets.token_hex(24)
            item = StagedUpload(name, token + '.bin', root / ('.' + token + '.upload'), root / (token + '.bin'))
            staged.append(item)
            digest = hashlib.sha256()
            with item.temporary_path.open('xb') as output:
                while block := upload.stream.read(CHUNK_BYTES):
                    item.size_bytes += len(block)
                    total += len(block)
                    if item.size_bytes > MAX_FILE_BYTES:
                        raise UploadError(f'单个文件不能超过 {MAX_FILE_BYTES / 1024 / 1024:g} MiB', 413)
                    if total > MAX_TOTAL_BYTES:
                        raise UploadError(f'每次上传文件合计不能超过 {MAX_TOTAL_BYTES / 1024 / 1024:g} MiB', 413)
                    output.write(block)
                    digest.update(block)
            item.sha256 = digest.hexdigest()
            thumbnail = _thumbnail(item.temporary_path)
            if thumbnail is not None:
                item.thumbnail_name = token + '.thumb.png'
                item.thumbnail_temporary_path = root / ('.' + token + '.thumb.upload')
                item.thumbnail_final_path = root / item.thumbnail_name
                with item.thumbnail_temporary_path.open('xb') as output:
                    output.write(thumbnail)
        return staged
    except Exception:
        cleanup_staged(staged)
        raise


def finalize_staged(staged):
    for item in staged:
        os.replace(item.temporary_path, item.final_path)
        if item.thumbnail_temporary_path is not None:
            os.replace(item.thumbnail_temporary_path, item.thumbnail_final_path)


def cleanup_staged(staged):
    for item in staged:
        for path in (item.temporary_path, item.final_path, item.thumbnail_temporary_path, item.thumbnail_final_path):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # An inaccessible orphan is never addressable through a committed API record.
                    pass
