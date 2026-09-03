"""Safe avatar upload processing with Pillow.

Uploads are validated by the form (real image, size limit), then re-encoded
here into a clean, well-formed PNG at a bounded dimension. Re-encoding strips
embedded metadata/scripts so a malicious or malformed source image cannot be
carried through, and prevents executable content from ever being served.
"""

from __future__ import annotations

from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps

MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MiB
AVATAR_DIMENSION = 256


def validate_upload_size(file: UploadedFile) -> None:
    if file.size > MAX_AVATAR_SIZE:
        raise ValidationError("The avatar must be smaller than 2 MB.")


def process_avatar(file: UploadedFile) -> ContentFile:
    """Validate and normalize an uploaded avatar, returning a re-encoded PNG.

    Raises ``ValidationError`` if the file is not a decodable image.
    """
    try:
        image = Image.open(file)
        image.load()
    except Exception as exc:  # noqa: BLE001 - any decode failure is rejected
        raise ValidationError("Uploaded file is not a valid image.") from exc

    image = ImageOps.exif_transpose(image)
    image.thumbnail((AVATAR_DIMENSION, AVATAR_DIMENSION))

    # Preserve transparency for PNG sources; flatten everything else to RGB.
    if image.mode in ("RGBA", "LA", "P") and "transparency" in image.info:
        base_mode = "RGBA"
    else:
        base_mode = "RGB"
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

    image = image.convert(base_mode)
    output = BytesIO()
    image.save(output, format="PNG")
    return ContentFile(output.getvalue())
