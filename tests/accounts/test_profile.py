"""Profile editing and avatar upload tests."""

import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.accounts.avatars import MAX_AVATAR_SIZE, process_avatar, validate_upload_size
from apps.accounts.models import User

from .base import AccountTestCase


def make_png_bytes(size: int = 64) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (59, 130, 246)).save(buf, format="PNG")
    return buf.getvalue()


class ProfileTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="bob@example.com", password="S3curePass!23")
        self.client.force_login(self.user)

    def test_update_profile(self):
        response = self.client.post(
            "/accounts/profile/",
            {
                "display_name": "Bobby Tables",
                "pronouns": "he/him",
                "bio": "Loves whiteboards.",
                "accent_color": "#dc2626",
                "timezone": "Europe/Paris",
                "locale": "fr",
            },
        )
        self.assertRedirects(response, "/accounts/profile/", fetch_redirect_response=False)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.display_name, "Bobby Tables")
        self.assertEqual(self.user.profile.pronouns, "he/him")
        self.assertEqual(self.user.profile.bio, "Loves whiteboards.")
        self.assertEqual(self.user.profile.accent_color, "#dc2626")
        self.assertEqual(self.user.profile.timezone, "Europe/Paris")
        self.assertEqual(self.user.profile.locale, "fr")

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get("/accounts/profile/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


class AvatarUploadTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="bob@example.com", password="S3curePass!23")
        self.client.force_login(self.user)

    def test_upload_avatar_saves_file(self):
        avatar = SimpleUploadedFile("me.png", make_png_bytes(), content_type="image/png")
        response = self.client.post("/accounts/profile/avatar/", {"avatar": avatar})
        self.assertRedirects(response, "/accounts/profile/", fetch_redirect_response=False)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.avatar.name.startswith("avatars/"))
        self.assertTrue(self.user.profile.avatar.storage.exists(self.user.profile.avatar.name))

    def test_invalid_image_rejected(self):
        avatar = SimpleUploadedFile("me.txt", b"not an image", content_type="text/plain")
        response = self.client.post("/accounts/profile/avatar/", {"avatar": avatar})
        self.assertRedirects(response, "/accounts/profile/", fetch_redirect_response=False)
        self.assertFalse(self.user.profile.avatar.name)


class AvatarUnitTest(AccountTestCase):
    def test_validate_rejects_oversized_file(self):
        big = SimpleUploadedFile("big.png", b"x" * (MAX_AVATAR_SIZE + 1))
        with self.assertRaises(ValidationError):
            validate_upload_size(big)

    def test_validate_accepts_small_file(self):
        small = SimpleUploadedFile("small.png", b"x" * 100)
        validate_upload_size(small)

    def test_process_avatar_returns_bounded_png(self):
        avatar = SimpleUploadedFile("me.png", make_png_bytes(size=2000), content_type="image/png")
        content = process_avatar(avatar)
        image = Image.open(content)
        self.assertEqual(image.format, "PNG")
        self.assertLessEqual(max(image.size), 256)

    def test_process_avatar_rejects_non_image(self):
        avatar = SimpleUploadedFile("bad.png", b"garbage", content_type="image/png")
        with self.assertRaises(ValidationError):
            process_avatar(avatar)
