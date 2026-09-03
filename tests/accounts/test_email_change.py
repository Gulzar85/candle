"""Email change flow tests."""

from django.core import mail

from apps.accounts.models import User

from .base import AccountTestCase, extract_verify_uid_token

PASSWORD = "S3curePass!23"


class EmailChangeTest(AccountTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="bob@example.com", password=PASSWORD)
        self.user.email_verified = True
        self.user.save(update_fields=["email_verified"])
        self.client.force_login(self.user)

    def test_request_email_change(self):
        response = self.client.post(
            "/accounts/profile/email-change/request/",
            {"new_email": "new@example.com"},
        )
        self.assertRedirects(response, "/accounts/settings/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email_pending, "new@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new@example.com"])

    def test_confirm_email_change_updates_address(self):
        self.client.post(
            "/accounts/profile/email-change/request/",
            {"new_email": "new@example.com"},
        )
        uid, token = extract_verify_uid_token(mail.outbox[0].body)
        response = self.client.get(f"/accounts/verify-email/{uid}/{token}/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/email_change_done.html")

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")
        self.assertEqual(self.user.email_pending, "")
        self.assertTrue(self.user.email_verified)

    def test_same_email_rejected(self):
        response = self.client.post(
            "/accounts/profile/email-change/request/",
            {"new_email": "bob@example.com"},
        )
        self.assertRedirects(response, "/accounts/settings/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email_pending, "")
        self.assertEqual(len(mail.outbox), 0)

    def test_existing_email_rejected(self):
        User.objects.create_user(email="taken@example.com", password=PASSWORD)
        response = self.client.post(
            "/accounts/profile/email-change/request/",
            {"new_email": "taken@example.com"},
        )
        self.assertRedirects(response, "/accounts/settings/", fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email_pending, "")
        self.assertEqual(len(mail.outbox), 0)
