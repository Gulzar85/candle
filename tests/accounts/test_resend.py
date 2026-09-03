"""Resend verification flow tests."""

from django.core import mail

from apps.accounts.models import User

from .base import AccountTestCase


class ResendVerificationTest(AccountTestCase):
    def test_resend_existing_unverified_user(self):
        User.objects.create_user(email="bob@example.com", password="S3curePass!23")
        response = self.client.post("/accounts/resend-verification/", {"email": "bob@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/resend_done.html")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Verify your Candle email", mail.outbox[0].subject)

    def test_resend_never_leaks_account_existence(self):
        # Unknown email still returns the generic confirmation page.
        response = self.client.post(
            "/accounts/resend-verification/", {"email": "nobody@example.com"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/resend_done.html")
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_does_not_require_login(self):
        response = self.client.get("/accounts/resend-verification/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/resend.html")
