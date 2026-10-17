"""Pure image validation boundaries for task screenshots."""
from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import FileStorage

# 先经过测试夹具，保证附件目录是临时目录。
import test_tasks  # noqa: F401
from modules.image_storage import ImageValidationError, prepare_image


class TestTaskAttachmentValidation(unittest.TestCase):
    def test_oversized_dimensions_rejected_before_pixel_decode(self) -> None:
        # Given: a highly compressed valid PNG above the 20 megapixel bound.
        encoded = io.BytesIO()
        Image.new("1", (5000, 5000)).save(encoded, format="PNG")
        upload = FileStorage(stream=io.BytesIO(encoded.getvalue()), filename="large.png")

        # When: validation inspects the image while pixel decoding is forbidden.
        with (
            patch("PIL.Image.Image.load", side_effect=AssertionError("pixels decoded")),
            self.assertRaisesRegex(ImageValidationError, "尺寸"),
        ):
            prepare_image(upload)

    def test_multiframe_png_is_rejected(self) -> None:
        # Given: an animated PNG with two frames.
        encoded = io.BytesIO()
        frames = [Image.new("RGB", (8, 8), color) for color in ("red", "blue")]
        frames[0].save(encoded, format="PNG", save_all=True, append_images=frames[1:])
        upload = FileStorage(stream=io.BytesIO(encoded.getvalue()), filename="animated.png")

        # When/Then: the parser rejects ambiguous multi-frame content.
        with self.assertRaisesRegex(ImageValidationError, "多帧"):
            prepare_image(upload)

    def test_webp_is_identified_by_decoded_content(self) -> None:
        # Given: valid WebP bytes with an unrelated client filename and MIME claim.
        encoded = io.BytesIO()
        Image.new("RGBA", (7, 9), "green").save(encoded, format="WEBP")
        upload = FileStorage(
            stream=io.BytesIO(encoded.getvalue()), filename="capture.bin", content_type="text/plain"
        )

        # When: actual content is parsed and re-encoded.
        prepared = prepare_image(upload)

        # Then: trusted metadata comes from Pillow rather than the client fields.
        self.assertEqual(prepared.mime_type, "image/webp")
        self.assertEqual((prepared.width, prepared.height), (7, 9))
        self.assertTrue(prepared.stored_name.endswith(".webp"))


if __name__ == "__main__":
    unittest.main()
