"""Bounded validation and private storage operations for screenshot images."""
from __future__ import annotations

import io
import os
import secrets
import warnings
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_FILES: Final = 5
MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
MAX_PIXELS: Final = 20_000_000
MAX_SIDE: Final = 10_000
class ImageFormat(StrEnum):
    PNG = "PNG"
    JPEG = "JPEG"
    WEBP = "WEBP"


_FORMATS: Final = {
    ImageFormat.PNG: ("image/png", ".png"),
    ImageFormat.JPEG: ("image/jpeg", ".jpg"),
    ImageFormat.WEBP: ("image/webp", ".webp"),
}


@dataclass(frozen=True, slots=True)
class ImageValidationError(Exception):
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class StorageWriteError(Exception):
    detail: str

    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class PreparedImage:
    original_name: str
    stored_name: str
    mime_type: str
    width: int
    height: int
    content: bytes

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class StagedImage:
    image: PreparedImage
    temporary_path: Path
    final_path: Path


def prepare_image(upload: FileStorage) -> PreparedImage:
    """Decode one bounded upload and re-encode it without embedded metadata."""
    original_name = Path(upload.filename or "screenshot").name
    content = upload.stream.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise ImageValidationError("截图文件为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise ImageValidationError("单张截图不能超过 10 MiB", 413)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with io.BytesIO(content) as source, Image.open(source) as probe:
                try:
                    image_format = ImageFormat(probe.format)
                except (TypeError, ValueError):
                    raise ImageValidationError("仅支持 PNG、JPEG 和 WebP 图片") from None
                if getattr(probe, "n_frames", 1) != 1:
                    raise ImageValidationError("不支持动画或多帧图片")
                probe_width, probe_height = probe.size
                if (probe_width > MAX_SIDE or probe_height > MAX_SIDE
                        or probe_width * probe_height > MAX_PIXELS):
                    raise ImageValidationError("截图尺寸超过限制")
                probe.verify()
            with io.BytesIO(content) as source, Image.open(source) as decoded:
                decoded.load()
                with ImageOps.exif_transpose(decoded).copy() as normalized:
                    normalized.info.clear()
                    width, height = normalized.size
                    encoded = _encode(normalized, image_format)
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError,
            OSError, SyntaxError) as error:
        raise ImageValidationError("截图内容无效或已损坏") from error

    if len(encoded) > MAX_IMAGE_BYTES:
        raise ImageValidationError("处理后的截图不能超过 10 MiB", 413)
    mime_type, extension = _FORMATS[image_format]
    return PreparedImage(
        original_name=original_name,
        stored_name=secrets.token_hex(24) + extension,
        mime_type=mime_type,
        width=width,
        height=height,
        content=encoded,
    )


def _encode(image: Image.Image, image_format: ImageFormat) -> bytes:
    with io.BytesIO() as output:
        match image_format:
            case ImageFormat.JPEG:
                with image.convert("RGB") as safe_image:
                    safe_image.save(output, format="JPEG", quality=92, optimize=True)
            case ImageFormat.PNG:
                image.save(output, format="PNG", optimize=True)
            case ImageFormat.WEBP:
                target_mode = "RGBA" if "A" in image.getbands() else "RGB"
                with image.convert(target_mode) as safe_image:
                    safe_image.save(output, format="WEBP", quality=92, method=4)
            case unreachable:
                assert_never(unreachable)
        return output.getvalue()


def stage_images(root: Path, images: list[PreparedImage]) -> list[StagedImage]:
    root.mkdir(parents=True, exist_ok=True)
    staged: list[StagedImage] = []
    try:
        for image in images:
            temporary = root / ("." + image.stored_name + ".upload")
            final = root / image.stored_name
            item = StagedImage(image=image, temporary_path=temporary, final_path=final)
            staged.append(item)
            with temporary.open("xb") as stream:
                stream.write(image.content)
    except OSError as error:
        cleanup_staged(staged)
        raise StorageWriteError("截图暂存失败") from error
    return staged


def finalize_staged(staged: list[StagedImage]) -> None:
    try:
        for item in staged:
            os.replace(item.temporary_path, item.final_path)
    except OSError as error:
        cleanup_staged(staged)
        raise StorageWriteError("截图保存失败") from error


def cleanup_staged(staged: list[StagedImage]) -> None:
    for item in staged:
        item.temporary_path.unlink(missing_ok=True)
        item.final_path.unlink(missing_ok=True)


def attachment_path(root: Path, stored_name: str) -> Path:
    """Resolve only randomized basenames within the configured private root."""
    if Path(stored_name).name != stored_name:
        raise ImageValidationError("截图存储记录无效")
    candidate = (root / stored_name).resolve()
    if candidate.parent != root.resolve():
        raise ImageValidationError("截图存储记录无效")
    return candidate
