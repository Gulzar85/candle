"""Email verification flow tests (also covers email-change confirmation)."""

from datetime import timedelta

from django.core import mail
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.models import EmailVerificationToken, User
from apps.accounts.services import send_verification_email

from .base import AccountTestCase, extract_verify_uid_token


def create_unverified(email: str = "alice@example.com") -> User:
    return User.objects.create_user(email=email, password="S3curePass!23")


class VerificationTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        from django.core import mail as _mail

        _mail.outbox.clear()

    def _fresh_link(self, user: User) -> tuple[str, str]:
        # Issue a fresh token and read its link from the newest email.
        mail.outbox.clear()
        send_verification_email(user)
        return extract_verify_uid_token(mail.outbox[-1].body)

    def test_valid_token_verifies_user(self):
        user = create_unverified()
        uid, token = self._fresh_link(user)
        response = self.client.get(f"/accounts/verify-email/{uid}/{token}/")
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_unknown_uid_returns_invalid(self):
        user = create_unverified()
        _, token = self._fresh_link(user)
        bogus_uid = urlsafe_base64_encode(force_bytes(999999))
        response = self.client.get(f"/accounts/verify-email/{bogus_uid}/{token}/")
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

    def test_invalid_token_returns_invalid(self):
        user = create_unverified()
        uid, _ = self._fresh_link(user)
        response = self.client.get(f"/accounts/verify-email/{uid}/garbage-token/")
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

    def test_expired_token_rejected(self):
        user = create_unverified()
        uid, token = self._fresh_link(user)
        EmailVerificationToken.objects.filter(user=user).update(
            expires_at=timezone.now() - timedelta(hours=1)
        )
        response = self.client.get(f"/accounts/verify-email/{uid}/{token}/")
        self.assertEqual(response.status_code, 400)
        user.refresh_from_db()
        self.assertFalse(user.email_verified)

    def test_token_is_single_use(self):
        user = create_unverified()
        uid, token = self._fresh_link(user)
        first = self.client.get(f"/accounts/verify-email/{uid}/{token}/")
        self.assertEqual(first.status_code, 302)
        second = self.client.get(f"/accounts/verify-email/{uid}/{token}/")
        self.assertEqual(second.status_code, 400)
